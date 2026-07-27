"""品質判定ハブ v1.0
フェーズ1: received_messages.jsonl を読んで自動分類・QA判定・Slack返信

Usage:
    python ops/quality_hub/quality_hub.py --once    # 1回処理して終了
    python ops/quality_hub/quality_hub.py --daemon  # 60秒間隔で常駐
"""

import json
import re
import sys
import os
import time
import argparse
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# パス設定
# ============================================================
BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parent.parent

# .env 読み込み
def _load_env():
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")

# ============================================================
# ファイルパス定義
# ============================================================
RECEIVED_LOG     = ROOT_DIR / "ops" / "slack_monitor" / "received_messages.jsonl"
EVENT_LOG_RAW    = ROOT_DIR / "ops" / "slack_monitor" / "event_log_raw.jsonl"

CLASSIFIED_DIR   = BASE_DIR / "classified"
INBOX_LOG        = CLASSIFIED_DIR / "03_inbox.jsonl"
DONE_LOG         = CLASSIFIED_DIR / "04_done_log.jsonl"
RESEARCH_LOG     = CLASSIFIED_DIR / "05_research_log.jsonl"
ERROR_LOG        = CLASSIFIED_DIR / "07_error_log.jsonl"
NOTIFY_LOG       = CLASSIFIED_DIR / "08_notify_queue.jsonl"
QA_LOG           = CLASSIFIED_DIR / "09_qa_log.jsonl"

PROCESSED_IDS    = BASE_DIR / "processed_ids.json"
HIGH_PRIORITY_Q  = BASE_DIR / "high_priority_queue.json"

# Slack送信先
TARGET_CHANNEL   = "C0AQASABVL7"
# 自分自身のbot_id（このメッセージには返信しない）
OWN_BOT_ID       = "B0ALSQ2RM5Y"

# ============================================================
# 分類ルール（キーワード正規表現）
# ============================================================
CLASSIFICATION_RULES = [
    # (分類名, ファイルパス, パターンリスト)
    ("done",     DONE_LOG,     [
        r"完了", r"SUCCESS", r"DONE", r"済み", r"対応済",
        r"実装完了", r"修正完了", r"解決", r"クローズ",
    ]),
    ("error",    ERROR_LOG,    [
        r"エラー", r"Error", r"ERROR", r"障害", r"FAIL",
        r"失敗", r"クラッシュ", r"停止", r"例外", r"Traceback",
        r"500", r"接続できない", r"タイムアウト", r"Timeout",
    ]),
    ("research", RESEARCH_LOG, [
        r"調査", r"リサーチ", r"確認中", r"調べ", r"分析",
        r"調べました", r"調べ結果",
    ]),
    ("notify",   NOTIFY_LOG,   [
        r"通知必要", r"再通知", r"要通知", r"通知お願",
        r"お知らせ", r"アラート", r"ALERT",
    ]),
    ("inbox",    INBOX_LOG,    [
        r"質問", r"要確認", r"進捗", r"BLOCKED", r"ACTION_REQUIRED",
        r"即着手", r"お願い", r"依頼", r"教えて", r"どうすれば",
        r"どうなって", r"確認してください", r"確認お願",
        r"実装してください", r"修正してください",
    ]),
]

# 高優先プレフィックス
HIGH_PRIORITY_PREFIXES = [
    r"【即停止】", r"【一時停止】", r"【即確認】",
    r"【要5分以内応答】", r"【BLOCKED確認】", r"【即着手指示】",
]

# ============================================================
# QAチェック必須フィールドパターン
# ============================================================
QA_FIELD_PATTERNS = {
    "対象":     [r"対象[:：]", r"■\s*対象", r"▼\s*対象"],
    "実施内容": [r"実施内容[:：]", r"■\s*実施", r"▼\s*実施", r"実施した"],
    "結果":     [r"結果[:：]", r"■\s*結果", r"SUCCESS", r"ERROR", r"BLOCKED", r"完了", r"失敗"],
    "証跡":     [r"https?://", r"証跡[:：]", r"ファイル[:：]", r"ログ[:：]", r"\.json", r"\.py", r"\.log", r"\.txt"],
    "残課題":   [r"残課題[:：]", r"■\s*残課題", r"なし", r"課題[:：]"],
    "次対応":   [r"次対応[:：]", r"■\s*次対応", r"次のアクション", r"要対応", r"不要"],
}


# ============================================================
# ユーティリティ
# ============================================================
def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def _load_processed_ids() -> set:
    if PROCESSED_IDS.exists():
        try:
            data = json.loads(PROCESSED_IDS.read_text(encoding="utf-8"))
            return set(data.get("ids", []))
        except Exception:
            return set()
    return set()


def _save_processed_ids(ids: set):
    PROCESSED_IDS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROCESSED_IDS.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"ids": list(ids), "count": len(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp.replace(PROCESSED_IDS)


def _make_processed_key(channel_id: str, ts: str) -> str:
    return f"{channel_id}::{ts}"


def _append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_high_priority() -> list:
    if HIGH_PRIORITY_Q.exists():
        try:
            return json.loads(HIGH_PRIORITY_Q.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_high_priority(items: list):
    tmp = HIGH_PRIORITY_Q.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HIGH_PRIORITY_Q)


# ============================================================
# Slack API
# ============================================================
def _slack_post_message(channel: str, text: str, thread_ts: str = None) -> dict:
    # CEO 2026-07-23「スラックでの報告を全て終了させて。今後は、報告必要なし」
    import os as _os
    if _os.path.exists(r"C:\Users\infoa\Documents\solarworks-ai\state\SLACK_FULL_STOP"):
        _log("Slack: FULL STOP (CEO 2026-07-23 全報告終了)")
        return {"ok": False, "error": "slack_full_stop"}

    if not SLACK_BOT_TOKEN:
        _log("WARN: SLACK_BOT_TOKEN未設定。送信スキップ")
        return {"ok": False, "error": "no_token"}

    url = "https://slack.com/api/chat.postMessage"
    payload = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            result = json.loads(res.read())
            if not result.get("ok"):
                _log(f"Slack送信エラー: {result.get('error')}")
            return result
    except Exception as e:
        _log(f"Slack送信例外: {e}")
        return {"ok": False, "error": str(e)}


# ============================================================
# 分類ロジック
# ============================================================
def classify_message(text: str) -> str:
    """テキストをキーワードマッチで分類する。最初にマッチしたカテゴリを返す。"""
    for category, _path, patterns in CLASSIFICATION_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return category
    return "inbox"  # デフォルトはinbox


def get_classification_path(category: str) -> Path:
    path_map = {
        "done":     DONE_LOG,
        "error":    ERROR_LOG,
        "research": RESEARCH_LOG,
        "notify":   NOTIFY_LOG,
        "inbox":    INBOX_LOG,
    }
    return path_map.get(category, INBOX_LOG)


def is_high_priority(text: str) -> bool:
    for pat in HIGH_PRIORITY_PREFIXES:
        if re.search(pat, text):
            return True
    return False


# ============================================================
# QA判定ロジック
# ============================================================
def qa_judge(text: str) -> dict:
    """テキストに対してQA判定を実施する。"""
    found = {}
    missing = []

    for field, patterns in QA_FIELD_PATTERNS.items():
        matched = False
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                matched = True
                break
        found[field] = matched
        if not matched:
            missing.append(field)

    # 証跡あり判定
    has_evidence = found.get("証跡", False)

    # エラー/本番障害/権限不足を含むか
    is_escalate = bool(re.search(
        r"本番障害|権限不足|CRITICAL|P0|緊急|即停止|即確認",
        text, re.IGNORECASE
    ))

    if is_escalate:
        verdict = "ESCALATE"
    elif len(missing) == 0 and has_evidence:
        verdict = "ACCEPT"
    elif len(missing) <= 2 and len(missing) > 0:
        verdict = "ACCEPT_WITH_TWEAK"
    else:
        verdict = "RETURN_FOR_FIX"

    return {
        "verdict": verdict,
        "found_fields": found,
        "missing_fields": missing,
        "has_evidence": has_evidence,
        "is_escalate": is_escalate,
    }


# ============================================================
# Slack返信フォーマット
# ============================================================
def build_reply_text(verdict: str, missing: list, text_excerpt: str) -> str:
    excerpt = text_excerpt[:80].replace("\n", " ") + ("..." if len(text_excerpt) > 80 else "")

    if verdict == "ACCEPT":
        return (
            f"【ACCEPT】\n"
            f"■ 確認した内容: {excerpt}\n"
            f"■ 判定: 全必須項目・証跡を確認。受理します。"
        )
    elif verdict == "ACCEPT_WITH_TWEAK":
        missing_str = "、".join(missing) if missing else "軽微不足"
        return (
            f"【ACCEPT_WITH_TWEAK】\n"
            f"■ 確認した内容: {excerpt}\n"
            f"■ 不足項目: {missing_str}\n"
            f"■ 修正依頼: 次回提出時に不足項目を補完してください。\n"
            f"■ 再提出条件: 今回はACCEPTとして処理しますが、フォーマット改善をお願いします。"
        )
    elif verdict == "RETURN_FOR_FIX":
        missing_str = "、".join(missing) if missing else "複数項目"
        return (
            f"【RETURN_FOR_FIX】\n"
            f"■ 確認した内容: {excerpt}\n"
            f"■ 不足項目: {missing_str}\n"
            f"■ 修正依頼: 必須項目が不足しています。フォーマットに沿って再提出してください。\n"
            f"■ 再提出条件: 対象・実施内容・結果・証跡・残課題・次対応 の全項目を記載すること。"
        )
    elif verdict == "ESCALATE":
        return (
            f"【ESCALATE】\n"
            f"■ 確認した内容: {excerpt}\n"
            f"■ エスカレーション: 本番障害・権限不足・緊急対応が含まれています。\n"
            f"■ 修正依頼: 即座にCEO/COOへエスカレーションしてください。\n"
            f"■ 再提出条件: エスカレーション完了後に結果を報告してください。"
        )
    return f"【{verdict}】\n■ 確認した内容: {excerpt}"


# ============================================================
# メッセージの送信者がbot自身かチェック
# ============================================================
def is_own_bot(record: dict) -> bool:
    """自分自身の投稿（bot_id）かどうか判定"""
    bot_id = record.get("bot_id", "")
    user = record.get("user", "")
    # bot_idフィールドに自分のIDが含まれる場合
    if bot_id == OWN_BOT_ID:
        return True
    # textに自分のbot_idを含む場合（フォールバック）
    return False


# ============================================================
# メイン処理
# ============================================================
def process_once():
    _log("=== quality_hub 処理開始 ===")

    # ディレクトリ作成
    CLASSIFIED_DIR.mkdir(parents=True, exist_ok=True)
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    # 処理済みIDを読み込み
    processed_ids = _load_processed_ids()
    high_priority_items = _load_high_priority()

    if not RECEIVED_LOG.exists():
        _log(f"WARN: received_messages.jsonl が存在しません: {RECEIVED_LOG}")
        return

    # received_messages.jsonl を読む
    messages = []
    with open(RECEIVED_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    _log(f"受信メッセージ数: {len(messages)}")

    new_count = 0
    for msg in messages:
        channel_id = msg.get("channel_id", "")
        ts         = msg.get("ts", "")
        text       = msg.get("text", "")
        user       = msg.get("user", "")
        received_at = msg.get("received_at", "")
        thread_ts  = msg.get("thread_ts", ts)

        # 重複チェック
        key = _make_processed_key(channel_id, ts)
        if key in processed_ids:
            continue

        new_count += 1

        # --- 1. event_log_raw に保存 ---
        raw_record = {
            "channel":              channel_id,
            "message_ts":           ts,
            "thread_ts":            thread_ts,
            "sender":               user,
            "text":                 text,
            "received_at":          received_at,
            "classification_status": "pending",
        }
        _append_jsonl(EVENT_LOG_RAW, raw_record)

        # --- 自分自身の投稿スキップ（無限ループ防止）---
        if is_own_bot(msg):
            _log(f"自botメッセージをスキップ: ts={ts}")
            processed_ids.add(key)
            continue

        # --- 2. 分類 ---
        category = classify_message(text)
        out_path = get_classification_path(category)

        classified_record = {
            "channel_id":   channel_id,
            "ts":           ts,
            "thread_ts":    thread_ts,
            "user":         user,
            "text":         text,
            "received_at":  received_at,
            "category":     category,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(out_path, classified_record)
        _log(f"分類: category={category}, ts={ts}, ch={channel_id}")

        # --- 3. 高優先チェック ---
        if is_high_priority(text):
            high_priority_items.append({
                "channel_id": channel_id,
                "ts":         ts,
                "text":       text,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            })
            _log(f"高優先メッセージ検知: ts={ts}")

        # --- 4. QA判定（対象チャンネルのメッセージのみ）---
        qa_result = qa_judge(text)
        verdict   = qa_result["verdict"]
        missing   = qa_result["missing_fields"]

        qa_record = {
            "channel_id":    channel_id,
            "ts":            ts,
            "user":          user,
            "text":          text,
            "verdict":       verdict,
            "found_fields":  qa_result["found_fields"],
            "missing_fields": missing,
            "has_evidence":  qa_result["has_evidence"],
            "is_escalate":   qa_result["is_escalate"],
            "judged_at":     datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(QA_LOG, qa_record)
        _log(f"QA判定: verdict={verdict}, missing={missing}")

        # --- 5. Slack自動返信（TARGET_CHANNELのみ）---
        if channel_id == TARGET_CHANNEL:
            # ACCEPTの場合も返信はしない（ノイズ削減）
            # ACCEPT_WITH_TWEAK, RETURN_FOR_FIX, ESCALATE には返信
            if verdict in ("ACCEPT_WITH_TWEAK", "RETURN_FOR_FIX", "ESCALATE"):
                reply_text = build_reply_text(verdict, missing, text)
                result = _slack_post_message(TARGET_CHANNEL, reply_text, thread_ts=ts)
                _log(f"Slack返信送信: verdict={verdict}, ok={result.get('ok')}")
            else:
                _log(f"Slack返信スキップ: verdict={verdict}（ACCEPT）")

        # 処理済みIDに追加
        processed_ids.add(key)

    # 処理済みIDを保存
    _save_processed_ids(processed_ids)
    _save_high_priority(high_priority_items)

    _log(f"=== 完了: 新規処理={new_count}件 / 累計処理済={len(processed_ids)}件 ===")


def run_daemon(interval: int = 60):
    _log(f"daemonモード開始 (interval={interval}秒)")
    while True:
        try:
            process_once()
        except Exception as e:
            _log(f"ERROR: {e}")
        _log(f"{interval}秒後に再実行...")
        time.sleep(interval)


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="品質判定ハブ v1.0")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once",   action="store_true", help="1回処理して終了")
    group.add_argument("--daemon", action="store_true", help="常駐ループ（60秒間隔）")
    parser.add_argument("--interval", type=int, default=60, help="daemonモードの間隔（秒）")
    args = parser.parse_args()

    if args.daemon:
        run_daemon(args.interval)
    else:
        # --once もデフォルトも1回処理
        process_once()
