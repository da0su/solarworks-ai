"""
SEO エンジン — メインステートループ

■ 動作フロー:
  1. キュー確認 → 6件未満なら記事生成を実行（6件になるまで）
  2. スケジュール確認 → 08:00/11:00/14:00/17:00/20:00/23:00 に1件投稿
  3. 1サイクル = 60秒ポーリング（daemon モード）

■ 記事生成ロジック:
  - 未使用キーワードから優先度順に選択
  - Claude APIで生成 → SQLiteキューに保存
  - キーワード枯渇時はWARNINGログ（Slackには未通知）

■ 投稿ロジック:
  - 指定時刻の±5分以内に1件投稿
  - 同一時間帯の二重投稿を防止（posted_at で確認）
  - 失敗→3回リトライ→ログのみ記録

■ Slack通知:
  - 当日23:55に投稿数=0なら Slackアラート
"""

import os
import time
import json
import urllib.request
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import keyword_db as db
from article_generator import generate_article
from wp_poster import post_to_wordpress

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")

# 投稿スケジュール（時間のみ、±5分以内に実行）
POST_HOURS = [8, 11, 14, 17, 20, 23]

# キュー最小維持件数
QUEUE_MIN = 6

# 1ポーリング間隔（秒）
POLL_INTERVAL = 60

# 最大連続エラー数（これを超えたらdaemonを一時停止）
MAX_CONSECUTIVE_ERRORS = 10


def _now() -> datetime:
    return datetime.now()


def _today_str() -> str:
    return date.today().isoformat()


def _log(msg: str, level: str = "INFO"):
    ts = _now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def _slack(msg: str):
    """Slack通知送信"""
    if not SLACK_WEBHOOK:
        return
    payload = json.dumps({"text": msg}, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        _log(f"Slack送信失敗: {e}", "WARN")


def check_and_generate() -> int:
    """
    キューが QUEUE_MIN 未満なら不足分を生成。
    生成した件数を返す。
    """
    queued = db.get_queued_count()
    if queued >= QUEUE_MIN:
        return 0

    need = QUEUE_MIN - queued
    _log(f"キュー不足 ({queued}/{QUEUE_MIN}) → {need}件生成開始")
    generated = 0

    for _ in range(need):
        kw_row = db.get_next_keyword()
        if not kw_row:
            _log("未使用キーワードが枯渇しました。keyword_db.pyにキーワードを追加してください。", "WARN")
            break

        keyword   = kw_row["keyword"]
        theme     = kw_row["theme"]
        kw_type   = kw_row["type"]
        kw_id     = kw_row["id"]

        _log(f"記事生成中: [{kw_type}] {keyword}")
        result = generate_article(keyword, theme, kw_type)

        if not result["success"]:
            _log(f"記事生成失敗: {keyword} → {result['error']}", "ERROR")
            continue

        article_id = db.add_article_to_queue(
            keyword_id=kw_id,
            keyword=keyword,
            title=result["title"],
            content=result["content"],
        )
        db.mark_keyword_used(kw_id)
        generated += 1
        _log(f"記事生成完了 id={article_id}: {result['title'][:60]}")

    _log(f"生成完了: {generated}件追加 (キュー: {db.get_queued_count()}件)")
    return generated


def _is_post_time(now: datetime) -> bool:
    """現在時刻がPOST_HOURSの±5分以内かチェック"""
    for h in POST_HOURS:
        target_min = h * 60
        current_min = now.hour * 60 + now.minute
        if abs(current_min - target_min) <= 5:
            return True
    return False


def _already_posted_this_slot(now: datetime) -> bool:
    """同一時間帯スロットで既に投稿済みかチェック"""
    posted_hours = db.get_today_posted_hours()
    for h in POST_HOURS:
        if abs(now.hour - h) <= 0 and h in posted_hours:
            return True
    return False


def check_and_post() -> bool:
    """
    スケジュール時刻に合わせて1件投稿。
    投稿したらTrue、スキップならFalseを返す。
    """
    now = _now()

    if not _is_post_time(now):
        return False

    if _already_posted_this_slot(now):
        return False

    article = db.get_next_article_to_post()
    if not article:
        _log("投稿時刻だがキューが空。記事生成を待機します。", "WARN")
        return False

    article_id = article["id"]
    keyword    = article["keyword"]
    title      = article["title"]
    content    = article["content"]
    theme      = _get_theme_from_keyword(keyword)

    _log(f"投稿開始: [{theme}] {title[:60]}")
    db.mark_article_posting(article_id)

    result = post_to_wordpress(
        title=title,
        content=content,
        theme=theme,
        status="draft",  # 安全のためdraft。HRさん確認後publishに変更
    )

    if result["success"]:
        db.mark_article_posted(article_id, result["wp_post_id"], result["wp_url"])
        _log(f"投稿成功: post_id={result['wp_post_id']} {result['wp_url']}")
        _slack(f"✅ SEO記事投稿完了\n📝 {title[:60]}\n🔗 {result['wp_url']}")
        return True
    else:
        retry = db.get_retry_count(article_id)
        _log(f"投稿失敗 (retry={retry}): {result['error']}", "ERROR")
        db.mark_article_failed(article_id, result["error"])

        # 3回未満なら再キューに戻す
        if retry < 3:
            db.requeue_article(article_id)
            _log(f"再キューに戻しました (retry={retry+1}/3)")
        else:
            _log(f"3回失敗。記事id={article_id}を失敗確定にします。", "ERROR")
            _slack(f"❌ SEO記事投稿3回失敗\n📝 {title[:60]}\nエラー: {result['error'][:100]}")

        return False


def _get_theme_from_keyword(keyword: str) -> str:
    """キーワードからテーマを推定"""
    if "V2H" in keyword or "v2h" in keyword.lower():
        return "V2H"
    if "蓄電池" in keyword or "卒FIT" in keyword or "卒fit" in keyword.lower():
        return "蓄電池"
    return "太陽光"


def check_daily_zero_post():
    """23:55に投稿数が0なら Slack アラート"""
    now = _now()
    if now.hour == 23 and 50 <= now.minute <= 59:
        today = _today_str()
        posted_hours = db.get_today_posted_hours()
        if not posted_hours:
            _log("本日の投稿数が0件です。Slackに通知します。", "WARN")
            _slack(
                f"⚠️ SEO自動投稿アラート\n"
                f"本日({today})の投稿数が0件です。\n"
                f"エンジンの状態を確認してください。"
            )


def run_once() -> dict:
    """
    1サイクル実行:
      1. キュー補充チェック
      2. 投稿タイミングチェック
      3. 日次アラートチェック
    """
    db.init_db()
    generated = check_and_generate()
    posted    = check_and_post()
    check_daily_zero_post()

    s = db.get_status_summary()
    return {
        "generated": generated,
        "posted": posted,
        "queue": s,
    }


def run_daemon():
    """
    常駐ループ。POLL_INTERVAL秒ごとにrun_once()を実行。
    Ctrl+C で停止。
    """
    _log("=== SEO エンジン起動 (daemon) ===")
    _log(f"投稿スケジュール: {POST_HOURS}時")
    _log(f"キュー最小維持: {QUEUE_MIN}件")
    _log(f"ポーリング間隔: {POLL_INTERVAL}秒")

    db.init_db()
    s = db.get_status_summary()
    _log(f"現在のキュー: queued={s['queued']}, posted={s['posted']}, kw_pending={s['kw_pending']}")

    consecutive_errors = 0

    try:
        while True:
            try:
                result = run_once()
                consecutive_errors = 0

                if result["generated"] > 0 or result["posted"]:
                    _log(f"サイクル完了: generated={result['generated']}, posted={result['posted']}, queued={result['queue']['queued']}")

            except Exception as e:
                consecutive_errors += 1
                _log(f"サイクルエラー ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}", "ERROR")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _log("連続エラーが多すぎます。60秒休止後に再試行します。", "ERROR")
                    _slack(f"🚨 SEOエンジン連続エラー\n{consecutive_errors}回連続でエラーが発生しています。\n最新エラー: {str(e)[:200]}")
                    time.sleep(60)
                    consecutive_errors = 0

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        _log("=== SEO エンジン停止 (KeyboardInterrupt) ===")


if __name__ == "__main__":
    run_daemon()
