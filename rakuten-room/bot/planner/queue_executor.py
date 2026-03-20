"""ROOM BOT v2 - キューベース投稿実行

SQLiteキューから queued のアイテムを取り出し、
既存の PostExecutor を使って投稿を実行する。
自動停止条件: ログイン切れ、連続失敗、セレクタ不一致
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from planner.queue_manager import QueueManager, STATUS_POSTED, STATUS_FAILED, STATUS_SKIPPED
from executor.browser_manager import BrowserManager
from executor.post_executor import PostExecutor
from executor.comment_generator import detect_genre, generate_comment
from executor.post_scorer import score_and_regenerate
from logger.logger import setup_logger, save_post_result

POST_HISTORY_PATH = config.DATA_DIR / "post_history.json"


def _append_post_history(item_code: str, url: str, title: str,
                         genre: str, comment: str, score: int,
                         room_url: str = ""):
    """post_history.json に投稿記録を追加する"""
    history = []
    if POST_HISTORY_PATH.exists():
        try:
            with open(POST_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, Exception):
            history = []

    history.append({
        "item_code": item_code,
        "url": url,
        "genre": genre,
        "title": title,
        "opening": comment.split("\n")[0] if comment else "",
        "comment": comment[:200],
        "score": score,
        "room_url": room_url,
        "posted_at": datetime.now().isoformat(),
    })

    POST_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POST_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

logger = setup_logger()

# 自動停止閾値
MAX_CONSECUTIVE_FAILURES = 3
# ログイン切れ等の致命的エラー
FATAL_ERROR_TYPES = {"login_redirect", "mix_page_error"}
# 導線問題（skipped扱い、連続失敗にカウントしない）
SKIP_ERROR_TYPES = {"collect_not_supported", "product_page_error",
                    "no_share_button", "no_room_link"}


class QueueExecutor:
    """SQLiteキューから投稿を実行する"""

    def __init__(self, queue_date: str = None, limit: int = None,
                 min_wait: float = None, max_wait: float = None,
                 tone: str = "normal"):
        self.queue_date = queue_date or datetime.now().strftime("%Y-%m-%d")
        self.limit = limit
        self.min_wait = min_wait if min_wait is not None else config.POST_INTERVAL_MIN
        self.max_wait = max_wait if max_wait is not None else config.POST_INTERVAL_MAX
        self.tone = tone  # "normal" | "pickup"

    def run(self) -> dict:
        """キューから投稿を実行"""
        qm = QueueManager()

        # 異常終了で running のまま残ったレコードを戻す
        reset_count = qm.reset_running(self.queue_date)
        if reset_count > 0:
            logger.info(f"前回異常終了の {reset_count}件 を queued に戻しました")

        # 実行対象を取得
        pending = qm.get_pending(self.queue_date, limit=self.limit)
        total = len(pending)

        if total == 0:
            logger.info("実行対象が0件です")
            print("\n実行対象が0件です。")
            print("  → python run.py plan で計画を生成してください。")
            qm.close()
            return {"total": 0, "posted": 0, "failed": 0, "skipped": 0, "aborted": False}

        logger.info(f"=== キュー実行開始: {self.queue_date} ({total}件) ===")

        bm = BrowserManager()
        consecutive_failures = 0
        posted_count = 0
        failed_count = 0
        skipped_count = 0
        abort_reason = None

        try:
            bm.start()

            # ログイン確認
            login_status = bm.check_login_status()
            if not login_status["logged_in"]:
                logger.error(f"未ログイン ({login_status['method']})")
                abort_reason = f"未ログイン ({login_status['method']})"
                return self._make_summary(total, posted_count, failed_count,
                                          skipped_count, True, abort_reason)

            executor = PostExecutor(bm)

            for i, item in enumerate(pending):
                num = i + 1
                queue_id = item["id"]
                title = item["title"]
                url = item["item_url"]
                comment = item["comment"]

                logger.info(f"-" * 40)
                logger.info(f"[{num}/{total}] {title[:40]}")

                # 連続失敗チェック
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    abort_reason = f"{MAX_CONSECUTIVE_FAILURES}件連続失敗で自動停止"
                    logger.error(abort_reason)
                    break

                # running に更新
                if not qm.mark_running(queue_id):
                    logger.warning(f"[{num}] ステータス更新失敗(running) - スキップ")
                    skipped_count += 1
                    continue

                # コメントが空なら自動生成
                if not comment or len(comment.strip()) < 10:
                    genre = item.get("genre") or detect_genre(title, url)
                    # tone対応: pickupトーンの場合はラッパーで渡す
                    if self.tone == "pickup":
                        gen_fn = lambda t, u, g: generate_comment(t, u, g, tone="pickup")
                    else:
                        gen_fn = generate_comment
                    comment, score_result = score_and_regenerate(
                        title, url, genre, gen_fn,
                    )
                    logger.info(f"コメント自動生成 ({len(comment)}文字, score={score_result['score']}, tone={self.tone})")

                # 投稿実行
                try:
                    result = executor.execute(url, comment)

                    if result["success"]:
                        # 成功
                        room_url = result.get("room_url", "")
                        qm.mark_posted(queue_id, room_url=room_url,
                                       result_message="OK")
                        posted_count += 1
                        consecutive_failures = 0
                        logger.info(f"[{num}] 成功 -> {room_url}")

                        # post_history.json にも記録
                        _append_post_history(
                            item_code=item.get("item_code", ""),
                            url=url, title=title,
                            genre=item.get("genre", ""),
                            comment=comment,
                            score=item.get("score", 0),
                            room_url=room_url,
                        )

                        # POST_LOG にも記録
                        save_post_result({
                            "post_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
                            "date": self.queue_date,
                            "title": title,
                            "product_url": url,
                            "comment": comment[:200],
                            "status": "posted",
                            "posted_at": datetime.now().isoformat(),
                            "room_url": room_url,
                            "method": "room_bot_v2_queue",
                        })

                    elif result.get("error_type") in SKIP_ERROR_TYPES:
                        # 導線問題 → skipped
                        reason = result.get("error_type", "unknown")
                        qm.mark_skipped(queue_id, reason=reason)
                        skipped_count += 1
                        logger.warning(f"[{num}] スキップ({reason})")

                    elif result.get("error_type") in FATAL_ERROR_TYPES:
                        # 致命的エラー → 即停止
                        error_msg = result.get("error", "致命的エラー")
                        qm.mark_failed(queue_id,
                                       error_type=result.get("error_type", ""),
                                       result_message=error_msg)
                        abort_reason = f"致命的エラー: {error_msg}"
                        logger.error(abort_reason)
                        failed_count += 1
                        break

                    else:
                        # 通常失敗
                        error_msg = result.get("error", "不明なエラー")
                        qm.mark_failed(queue_id,
                                       error_type=result.get("error_type", ""),
                                       result_message=error_msg)
                        failed_count += 1
                        consecutive_failures += 1
                        logger.error(f"[{num}] 失敗: {error_msg}")
                        logger.warning(f"  連続失敗: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")

                except Exception as e:
                    error_msg = str(e)
                    qm.mark_failed(queue_id, error_type="exception",
                                   result_message=error_msg)
                    failed_count += 1
                    consecutive_failures += 1
                    logger.error(f"[{num}] 例外: {error_msg}")

                # 次の投稿まで待機
                if num < total and abort_reason is None:
                    # 20投稿ごとに安全休憩
                    if posted_count > 0 and posted_count % config.POST_REST_EVERY == 0:
                        rest = random.uniform(
                            config.POST_REST_DURATION_MIN,
                            config.POST_REST_DURATION_MAX,
                        )
                        logger.info(f"安全休憩: {posted_count}件完了 → {rest:.1f}秒休憩")
                        time.sleep(rest)

                    if result and not result.get("success") and \
                       result.get("error_type") in SKIP_ERROR_TYPES:
                        interval = random.uniform(1.0, 3.0)
                    else:
                        interval = random.uniform(self.min_wait, self.max_wait)
                    logger.info(f"次の投稿まで {interval:.1f}秒 待機...")
                    time.sleep(interval)

        finally:
            bm.stop()
            # デイリーサマリー更新
            qm.save_daily_summary(self.queue_date)
            qm.close()

        return self._make_summary(total, posted_count, failed_count,
                                  skipped_count, abort_reason is not None, abort_reason)

    def _make_summary(self, total, posted, failed, skipped,
                      aborted=False, reason=None) -> dict:
        summary = {
            "date": self.queue_date,
            "total": total,
            "posted": posted,
            "failed": failed,
            "skipped": skipped,
            "aborted": aborted,
            "reason": reason,
        }

        print(f"\n{'=' * 60}")
        if aborted:
            print(f"キュー実行中断: {reason}")
        else:
            print("キュー実行完了")
        print(f"{'=' * 60}")
        print(f"  対象日:   {self.queue_date}")
        print(f"  合計:     {total}件")
        print(f"  成功:     {posted}件")
        print(f"  失敗:     {failed}件")
        print(f"  スキップ: {skipped}件")
        print(f"{'=' * 60}")

        return summary
