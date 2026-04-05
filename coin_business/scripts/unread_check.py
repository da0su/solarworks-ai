"""
coin_business/scripts/unread_check.py
======================================
Claude Code セッション開始時の必須チェック（unread_hook）。

マーケからの未読指示を表示し、Claudeが全文を読む前に
他の作業を始めないようブロックする役割を担う。

Usage:
    python scripts/unread_check.py          # 未読一覧（デフォルト）
    python scripts/unread_check.py --full   # 全文表示
    python scripts/unread_check.py --mark-reading <ts>  # 読み始めマーク

CLAUDE.mdのルール:
    セッション開始時に必ずこれを実行し、
    未読（unread）がある場合は先に実行してからACKを送ること。
"""

from __future__ import annotations

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PENDING_FILE = Path(__file__).parent.parent / "data" / "pending_instructions.json"


# ────────────────────────────────────────────────────────────────
# ファイル操作
# ────────────────────────────────────────────────────────────────

def load() -> list[dict]:
    if not PENDING_FILE.exists():
        return []
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save(instructions: list[dict]) -> None:
    PENDING_FILE.write_text(
        json.dumps(instructions, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ────────────────────────────────────────────────────────────────
# 公開API（ポーラーや cap_report.py から呼ぶ）
# ────────────────────────────────────────────────────────────────

def add_instruction(ts: str, text: str, sender: str, task_id: str) -> None:
    """ポーラーが新着を検知したときに呼ぶ。status=unread で追加。"""
    instructions = load()
    # 同一tsの重複チェック
    if any(i["ts"] == ts for i in instructions):
        return
    instructions.append({
        "ts": ts,
        "task_id": task_id,
        "sender": sender,
        "text": text,
        "status": "unread",       # unread → reading → done
        "received_at": datetime.now(timezone.utc).isoformat(),
        "read_at": "",
        "done_at": "",
    })
    save(instructions)


def mark_reading(ts: str) -> None:
    """Claudeが読み始めたときに呼ぶ。"""
    instructions = load()
    for i in instructions:
        if i["ts"] == ts and i["status"] == "unread":
            i["status"] = "reading"
            i["read_at"] = datetime.now(timezone.utc).isoformat()
    save(instructions)


def mark_done(ts: str) -> None:
    """Claude が実行完了＋ACK送信後に呼ぶ。"""
    instructions = load()
    for i in instructions:
        if i["ts"] == ts and i["status"] in ("unread", "reading"):
            i["status"] = "done"
            i["done_at"] = datetime.now(timezone.utc).isoformat()
    save(instructions)


def get_unread() -> list[dict]:
    return [i for i in load() if i["status"] == "unread"]


def get_reading() -> list[dict]:
    return [i for i in load() if i["status"] == "reading"]


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    show_full = "--full" in args

    if "--mark-reading" in args:
        idx = args.index("--mark-reading")
        ts = args[idx + 1]
        mark_reading(ts)
        print(f"reading マーク: ts={ts}")
        return

    if "--mark-done" in args:
        idx = args.index("--mark-done")
        ts = args[idx + 1]
        mark_done(ts)
        print(f"done マーク: ts={ts}")
        return

    instructions = load()
    unread  = [i for i in instructions if i["status"] == "unread"]
    reading = [i for i in instructions if i["status"] == "reading"]
    done    = [i for i in instructions if i["status"] == "done"]

    print(f"=== pending_instructions ===")
    print(f"未読(unread): {len(unread)}件 / 読み中(reading): {len(reading)}件 / 完了(done): {len(done)}件")
    print()

    if unread or reading:
        print("【要対応】以下の指示を実行してから他の作業を始めること")
        print("-" * 60)
        for i in (reading + unread):
            status_mark = "[読み中]" if i["status"] == "reading" else "[未読]"
            recv = i.get("received_at", "")[:16].replace("T", " ")
            print(f"{status_mark} {i['task_id']} (ts={i['ts'][:12]}) 受信:{recv}")
            if show_full:
                print(f"  全文:\n{i['text']}\n")
            else:
                print(f"  概要: {i['text'][:150]}...")
            print()
    else:
        print("未読指示なし。通常作業を続けてください。")

    # 終了コード: 未読があれば1（Claudeへの警告シグナル）
    sys.exit(1 if (unread or reading) else 0)


if __name__ == "__main__":
    main()
