"""
coin_business/scripts/slack_session_start.py
============================================
Claude Code セッション開始時の自動Slack確認スクリプト。

【役割】
  - pending_instructions.json の未読チェック
  - Slack API から直接最新メッセージを取得（ポーラーが見逃した場合の補完）
  - 未読あれば全文を出力し、即座に対応開始できる状態にする

【起動タイミング】
  - スケジュールタスク（定期実行）から呼ばれる
  - または手動: python scripts/slack_session_start.py

【出力】
  - 未読なし → "SLACK OK: 未読なし"
  - 未読あり → 全文出力 + exit code 1
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# .env 読み込み
for _env in [
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
]:
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL     = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")
BOT_USER_ID = "U0AMM2M9Y48"

PENDING_FILE   = Path(__file__).parent.parent / "data" / "pending_instructions.json"
POLL_STATE     = Path(__file__).parent.parent / "data" / "slack_poll_state.json"
HEARTBEAT_LOG  = Path(__file__).parent.parent / "data" / "slack_heartbeat.log"


def load_pending() -> list[dict]:
    if not PENDING_FILE.exists():
        return []
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_pending(data: list[dict]) -> None:
    PENDING_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def slack_get(path: str, params: dict) -> dict:
    url = f"https://slack.com/api/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_last_ts() -> str:
    """slack_poll_state.json から last_ts を取得"""
    if POLL_STATE.exists():
        try:
            s = json.loads(POLL_STATE.read_text(encoding="utf-8"))
            return s.get("last_ts", "0")
        except Exception:
            pass
    return "0"


def check_slack_direct() -> list[dict]:
    """Slack API から直接新着メッセージを取得（ポーラー補完）"""
    last_ts = get_last_ts()
    params = {"channel": CHANNEL, "limit": 20}
    if last_ts != "0":
        params["oldest"] = last_ts

    res = slack_get("conversations.history", params)
    if not res.get("ok"):
        err = res.get("error", "unknown")
        print(f"[session_start] Slack API エラー: {err}")
        return []

    msgs = res.get("messages", [])
    new_user_msgs = [
        m for m in msgs
        if m.get("user") and m.get("user") != BOT_USER_ID
        and float(m.get("ts", "0")) > float(last_ts)
    ]
    return new_user_msgs


def add_to_pending(ts: str, text: str, user: str) -> str:
    """pending_instructions.json に未読として追加。task_id を返す"""
    instructions = load_pending()
    if any(i["ts"] == ts for i in instructions):
        return ""  # 重複

    # task_id 採番
    n = len({i.get("task_id", "") for i in instructions})
    tid = f"TASK-{n+1:04d}"

    instructions.append({
        "ts": ts,
        "task_id": tid,
        "sender": user,
        "text": text,
        "status": "unread",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "read_at": "",
        "done_at": "",
    })
    save_pending(instructions)
    return tid


def log_heartbeat(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with HEARTBEAT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now}] [session_start] {msg}\n")


def main():
    print("=" * 60)
    print(f"[session_start] Slack自動確認 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Step 1: pending_instructions.json の未読確認
    instructions = load_pending()
    unread = [i for i in instructions if i["status"] == "unread"]
    reading = [i for i in instructions if i["status"] == "reading"]

    # Step 2: Slack API 直接確認（ポーラーが見逃したメッセージを補完）
    new_msgs = check_slack_direct()
    newly_added = []

    for m in reversed(new_msgs):
        ts = m.get("ts", "")
        text = m.get("text", "")
        user = m.get("user", "unknown")
        # pendingに未登録のものだけ追加
        if not any(i["ts"] == ts for i in instructions):
            tid = add_to_pending(ts, text, user)
            if tid:
                newly_added.append({"ts": ts, "task_id": tid, "text": text})
                log_heartbeat(f"新着検知(session_start) ts={ts[:10]} tid={tid}")
                print(f"  ★新着★ {tid} | {text[:100]}")

    # 再ロード
    instructions = load_pending()
    unread = [i for i in instructions if i["status"] == "unread"]
    reading = [i for i in instructions if i["status"] == "reading"]

    if unread or reading:
        print(f"\n【未読あり: {len(unread)}件 / 読み中: {len(reading)}件】")
        print("─" * 60)
        for i in (reading + unread):
            mark = "[読み中]" if i["status"] == "reading" else "[未読] "
            recv = i.get("received_at", "")[:16].replace("T", " ")
            print(f"{mark} {i['task_id']}  受信:{recv}")
            print(f"  全文:\n{i['text']}\n")
        print("─" * 60)
        print("→ 上記の指示を先に実行してください。")
        log_heartbeat(f"未読{len(unread)}件 / 新着{len(newly_added)}件")
        sys.exit(1)
    else:
        msg = f"未読なし。新着検知:{len(newly_added)}件"
        print(f"\nSLACK OK: {msg}")
        log_heartbeat(msg)
        sys.exit(0)


if __name__ == "__main__":
    main()
