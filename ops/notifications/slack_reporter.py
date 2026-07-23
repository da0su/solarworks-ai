#!/usr/bin/env python3
"""汎用Slack報告スクリプト v1.0

Usage:
    python ops/notifications/slack_reporter.py "メッセージ本文"
    python ops/notifications/slack_reporter.py "メッセージ本文" --channel C0AQASABVL7
    python ops/notifications/slack_reporter.py --mark-done      # 報告済みフラグを立てる
    python ops/notifications/slack_reporter.py --mark-pending   # 未報告フラグを立てる（タスク開始時）
    python ops/notifications/slack_reporter.py --status         # 未報告フラグ確認

デフォルト送信先: #web-cyber_marke_clow (C0AQASABVL7)
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# ============================================================
# 設定
# ============================================================
ROOT_DIR = Path(__file__).parent.parent.parent
PENDING_FLAG = ROOT_DIR / "state" / ".report_pending"
DEFAULT_CHANNEL = "C0AQASABVL7"  # #web-cyber_marke_clow


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
# Slack送信
# ============================================================
SENDER_PREFIX = "[サイバー] "  # 全送信に自動付与


def _add_sender_prefix(text: str) -> str:
    """送信者（サイバー）を先頭に明示する。既に含まれていれば追加しない。"""
    if text.startswith(SENDER_PREFIX) or "サイバー報告" in text[:20] or "サイバー】" in text[:30]:
        return text
    return SENDER_PREFIX + text


def post_message(text: str, channel: str = DEFAULT_CHANNEL, critical: bool = False) -> bool:
    """Slackにメッセージを送信

    critical=True: 機能全停止など「知らせないと事業が止まる」障害通知。
      killswitch (SLACK_DISABLED) を貫通して送る。
      2026-07-16 の16日間サイレント停止は、この critical が killswitch に
      巻き込まれて無音化したのが原因。定期報告(ノイズ)は従来通り停止のまま、
      本当の障害だけは必ず届くようにする (CEO 2026-07-23「毎日監視できてるの?」)。
    """
    # CEO 2026-06-04 全Slack停止: state/SLACK_DISABLED がある間は送信しない (フラグ削除で再開)
    try:
        from ops.notifications.slack_killswitch import slack_disabled
    except Exception:
        import os as _os
        slack_disabled = lambda: _os.path.exists(r"C:\Users\infoa\Documents\solarworks-ai\state\SLACK_DISABLED")
    if slack_disabled() and not critical:
        print("Slack: SKIPPED (CEO 2026-06-04 全停止)", file=sys.stderr)
        return False
    if slack_disabled() and critical:
        print("Slack: CRITICAL bypass (killswitch 貫通)", file=sys.stderr)

    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN が設定されていません", file=sys.stderr)
        return False

    text = _add_sender_prefix(text)

    payload = json.dumps({
        "channel": channel,
        "text": text
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        if res.get("ok"):
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] Slack送信OK → channel={channel}")
            return True
        else:
            print(f"ERROR: Slack API error: {res.get('error')}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return False


# ============================================================
# フラグ管理
# ============================================================
def mark_pending(task_desc: str = ""):
    """タスク開始時に未報告フラグを立てる"""
    PENDING_FLAG.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "created_at": datetime.now().isoformat(),
        "task_desc": task_desc,
    }
    PENDING_FLAG.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"未報告フラグ設定: {task_desc or '(task)' }")


def mark_done():
    """報告完了後にフラグを削除"""
    if PENDING_FLAG.exists():
        PENDING_FLAG.unlink()
        print("未報告フラグ解除: 報告済みとしてマーク")
    else:
        print("フラグなし（既に解除済み）")


def check_pending() -> dict | None:
    """未報告フラグが立っているか確認"""
    if PENDING_FLAG.exists():
        try:
            return json.loads(PENDING_FLAG.read_text(encoding="utf-8"))
        except Exception:
            return {"created_at": "unknown", "task_desc": ""}
    return None


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Slack汎用報告スクリプト")
    parser.add_argument("message", nargs="?", default="", help="送信するメッセージ")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help="送信先チャンネルID")
    parser.add_argument("--mark-done", action="store_true", help="報告済みフラグ解除")
    parser.add_argument("--mark-pending", metavar="TASK", nargs="?", const="",
                        help="未報告フラグを立てる（タスク開始時）")
    parser.add_argument("--status", action="store_true", help="未報告フラグ確認")
    parser.add_argument("--critical", action="store_true",
                        help="機能全停止等の障害通知。killswitch を貫通して送る")
    args = parser.parse_args()

    if args.mark_done:
        mark_done()
        return

    if args.mark_pending is not None:
        mark_pending(args.mark_pending)
        return

    if args.status:
        pending = check_pending()
        if pending:
            print(f"[未報告あり] 開始: {pending['created_at']} | タスク: {pending['task_desc'] or '(未指定)'}")
        else:
            print("[報告済み] 未報告フラグなし")
        return

    if not args.message:
        parser.print_help()
        sys.exit(1)

    ok = post_message(args.message, args.channel, critical=args.critical)

    # 送信成功したら未報告フラグも自動解除
    if ok:
        mark_done()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
