#!/usr/bin/env python3
"""Slack未読チェック Hook版 v2.0
PreToolUse hook から呼ばれる。

修正履歴:
  v1.0: 'unread' キーのみ読取 → push_receiver が 'messages' キーで書くため常に0件
  v2.0: 'messages' / 'unread' 両方 + pending_instructions.json も確認
        HIGH優先 or 未実行指示あり → exit(2) でツール実行をブロック
        クールダウン 5分 → 1分に短縮

設計:
  - HIGH優先メッセージ or 未実行指示あり → stderr に詳細 + exit(2) でブロック
  - 通常メッセージのみ → stderr に通知 + exit(0)（ブロックしない）
  - 未読なし → 静音 exit(0)
"""

import sys
import json
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).parent.parent.parent
UNREAD_FILE = ROOT_DIR / "state" / "slack_unread.json"
PENDING_FILE = ROOT_DIR / "state" / "pending_instructions.json"
LAST_NOTIFIED_FILE = ROOT_DIR / "state" / ".unread_hook_last_notified"
COOLDOWN_SEC = 60  # 1分クールダウン（旧: 5分）


def _cooldown_passed() -> bool:
    if not LAST_NOTIFIED_FILE.exists():
        return True
    try:
        last = datetime.fromisoformat(LAST_NOTIFIED_FILE.read_text().strip())
        elapsed = (datetime.now() - last).total_seconds()
        return elapsed >= COOLDOWN_SEC
    except Exception:
        return True


def _update_notified():
    LAST_NOTIFIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_NOTIFIED_FILE.write_text(datetime.now().isoformat())


def _load_unread() -> list:
    """'messages' キーと 'unread' キー両方に対応"""
    if not UNREAD_FILE.exists():
        return []
    try:
        data = json.loads(UNREAD_FILE.read_text(encoding="utf-8"))
        # v2: 'messages' キー優先、なければ 'unread' キー
        msgs = data.get("messages", data.get("unread", []))
        return [m for m in msgs if isinstance(m, dict)]
    except Exception:
        return []


def _load_pending() -> list:
    """未実行指示の件数を返す"""
    if not PENDING_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        instructions = data.get("instructions", [])
        return [i for i in instructions if i.get("status") == "pending"]
    except Exception:
        return []


def main():
    msgs = _load_unread()
    pending = _load_pending()

    total = len(msgs) + len(pending)
    if total == 0:
        sys.exit(0)

    if not _cooldown_passed():
        sys.exit(0)

    _update_notified()

    # HIGH優先 or 未実行指示 → exit(2) でブロック
    high_msgs = [m for m in msgs if m.get("priority") == "HIGH"]
    has_pending = len(pending) > 0
    is_blocking = len(high_msgs) > 0 or has_pending

    lines = [
        "",
        "=" * 60,
        f"[SLACK自動実行指示] {len(msgs)}件の未読 + {len(pending)}件の未実行指示",
        "→ 今すぐ check_unread.py を実行して指示内容を読み、実行してください",
        "→ 実行後にACKを送信してください（自動ACKは廃止済み）",
    ]
    for m in high_msgs[:3]:
        text = m.get("text", "")[:80]
        lines.append(f"  [HIGH] {text}")
    for p in pending[:3]:
        text = p.get("full_text", "")[:80]
        lines.append(f"  [指示] {text}")
    lines += [
        "確認コマンド: python ops/slack_monitor/check_unread.py",
        "=" * 60,
        "",
    ]

    output = "\n".join(lines)
    print(output, file=sys.stderr)

    # 2026-05-12 CEO 指示: 「外部から指示ができなくなる設定を解除」
    # 旧: HIGH 優先 or 未実行指示で exit(2) でツール呼び出しをブロック
    #     → 古い未消化指示が pending に残ると私が CEO のメッセージに応答不能になる事故 (21件 stale)
    # 新: 常に exit(0) で通知のみ・ブロックしない. is_blocking はログ上は残すが return code は不変.
    sys.exit(0)


if __name__ == "__main__":
    main()
