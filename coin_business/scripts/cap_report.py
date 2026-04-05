"""
coin_business/scripts/cap_report.py
=====================================
キャップ(Claude Code)専用 Slack報告CLI。

タスク完了・次指示待ち・質問のたびに必ずこれを呼ぶ。
チャットだけで完結させない。

Usage:
    python scripts/cap_report.py done   "タスク名" "結果サマリー"
    python scripts/cap_report.py ask    "次に何を実装するか教えてください"
    python scripts/cap_report.py block  "タスク名" "ブロック理由"
    python scripts/cap_report.py status  # 現在のWORKINGタスク確認

Examples:
    python scripts/cap_report.py done "自動返信解除" "AUTO_REPLY=False実装・PID=28376稼働確認"
    python scripts/cap_report.py ask  "再発防止実装完了。次に何を実装するか教えてください"
    python scripts/cap_report.py block "daily_candidates接続" "Supabase接続エラー・キー確認待ち"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# .env 読み込み（coin_business/.env → ルート .env）
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

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
COIN_CHANNEL    = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")

WORKING_FILE    = Path(__file__).parent.parent / "data" / "working_task.json"
TASK_QUEUE_FILE = Path(__file__).parent.parent / "data" / "task_queue.json"


# ────────────────────────────────────────────────────────────────
# Slack送信
# ────────────────────────────────────────────────────────────────

def _post(text: str) -> bool:
    if not SLACK_BOT_TOKEN:
        print("[cap_report] ERROR: SLACK_BOT_TOKEN 未設定", file=sys.stderr)
        return False
    data = json.dumps({"channel": COIN_CHANNEL, "text": text},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        if res.get("ok"):
            print("[cap_report] Slack送信成功")
            return True
        else:
            print(f"[cap_report] Slack error: {res.get('error')}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[cap_report] 通信エラー: {e}", file=sys.stderr)
        return False


# ────────────────────────────────────────────────────────────────
# コマンド実装
# ────────────────────────────────────────────────────────────────

def cmd_done(task: str, result: str, detail: str = "") -> bool:
    """
    タスク実行完了後にACKをSlackに送信する。
    あるべきフロー: Claude実行完了 → この関数 → Slack ACK → pending mark_done
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # WORKINGファイルから task_id と message_ts を取得
    tid = "?"
    msg_ts = ""
    if WORKING_FILE.exists():
        try:
            w = json.loads(WORKING_FILE.read_text(encoding="utf-8"))
            tid    = w.get("task_id", "?")
            msg_ts = w.get("message_ts", "")
        except Exception:
            pass

    detail_str = f"\n{detail}" if detail else ""
    text = (
        f"【キャップ⇒マーケ】完了報告\n"
        f"task_id={tid} | {task}\n"
        f"\n■ 結果\n{result}{detail_str}\n"
        f"\n■ 次の指示をお願いします\n"
        f"({now})"
    )
    ok = _post(text)

    if ok:
        # WORKINGファイルを DONE に更新
        if WORKING_FILE.exists():
            try:
                w = json.loads(WORKING_FILE.read_text(encoding="utf-8"))
                w["completed_at"] = datetime.now(timezone.utc).isoformat()
                w["status"] = "DONE"
                WORKING_FILE.write_text(
                    json.dumps(w, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

        # pending_instructions.json を done にマーク（ACK完了）
        if msg_ts:
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from unread_check import mark_done  # type: ignore
                mark_done(msg_ts)
            except Exception:
                pass

    return ok


def cmd_ask(question: str) -> bool:
    """マーケへの質問・次指示確認をSlackに送信"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"【キャップ⇒マーケ】確認・質問\n"
        f"\n{question}\n"
        f"\n({now})"
    )
    return _post(text)


def cmd_block(task: str, reason: str) -> bool:
    """ブロック報告をSlackに送信"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"【キャップ⇒マーケ】ブロック報告\n"
        f"タスク: {task}\n"
        f"\n■ 理由\n{reason}\n"
        f"\n■ 対応待ち: マーケまたはCEOの判断が必要です\n"
        f"({now})"
    )
    return _post(text)


def cmd_status() -> None:
    """現在のWORKINGタスクとキューを表示"""
    # WORKING
    if WORKING_FILE.exists():
        try:
            w = json.loads(WORKING_FILE.read_text(encoding="utf-8"))
            tid    = w.get("task_id", "?")
            subj   = w.get("subject", "")
            start  = w.get("started_at", "")[:16]
            status = w.get("status", "WORKING")
            print(f"WORKING: [{status}] {tid} - {subj}")
            print(f"  開始: {start}")
        except Exception:
            print("WORKING: (読み込みエラー)")
    else:
        print("WORKING: なし")

    # キュー
    if TASK_QUEUE_FILE.exists():
        try:
            q = json.loads(TASK_QUEUE_FILE.read_text(encoding="utf-8"))
            queued = [t for t in q if t.get("status") == "queued"]
            print(f"\nキュー: {len(queued)}件")
            for t in queued[-5:]:
                print(f"  {t.get('task_id')} | {t.get('text','')[:60]}")
        except Exception:
            print("キュー: (読み込みエラー)")
    else:
        print("キュー: なし")


# ────────────────────────────────────────────────────────────────
# CLI エントリーポイント
# ────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "done":
        if len(args) < 3:
            print("Usage: cap_report.py done <タスク名> <結果サマリー> [詳細]")
            sys.exit(1)
        task   = args[1]
        result = args[2]
        detail = args[3] if len(args) > 3 else ""
        ok = cmd_done(task, result, detail)
        sys.exit(0 if ok else 1)

    elif cmd == "ask":
        if len(args) < 2:
            print("Usage: cap_report.py ask <質問内容>")
            sys.exit(1)
        question = " ".join(args[1:])
        ok = cmd_ask(question)
        sys.exit(0 if ok else 1)

    elif cmd == "block":
        if len(args) < 3:
            print("Usage: cap_report.py block <タスク名> <理由>")
            sys.exit(1)
        task   = args[1]
        reason = " ".join(args[2:])
        ok = cmd_block(task, reason)
        sys.exit(0 if ok else 1)

    elif cmd == "status":
        cmd_status()

    else:
        print(f"不明なコマンド: {cmd}")
        print("コマンド: done / ask / block / status")
        sys.exit(1)


if __name__ == "__main__":
    main()
