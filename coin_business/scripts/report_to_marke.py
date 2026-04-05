"""
coin_business/scripts/report_to_marke.py
==========================================
コイン事業 業務完了時の標準Slack報告モジュール。

すべての業務完了時にこのモジュールの report_done() を呼ぶこと。

使い方:
    from scripts.report_to_marke import report_done, report_progress, report_blocked

    # 完了報告
    report_done(
        task="スプシ初版作成",
        content=["91件エクスポート", "4シート構成"],
        changes=["spreadsheet_export.csv 新規"],
        result="MARKETING_REVIEW=91件確認",
        evidence="https://docs.google.com/...",
        remaining=["PRICE_NEEDED案件の更新待ち"],
    )

    # 進捗報告
    report_progress(task="eBayスキャン", progress="150/300件完了", eta="30分")

    # 滞留報告（CEO対応必要）
    report_blocked(task="xxx", reason="権限不足", ceo_action="Slack Admin操作")

CLI:
    python scripts/report_to_marke.py done --task "テスト" --result "OK"
    python scripts/report_to_marke.py check   # チャンネル未読確認
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# .env 読み込み（coin_business/.env → ルート .env の順）
for _env in [Path(__file__).parent.parent / ".env",
             Path(__file__).parent.parent.parent / ".env"]:
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SLACK_BOT_TOKEN  = os.environ.get("SLACK_BOT_TOKEN", "")
COIN_CHANNEL     = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")  # #coin-cap-marke
BOT_USER_ID      = "U0AMM2M9Y48"   # solarworkscoo
SLACK_API_POST   = "https://slack.com/api/chat.postMessage"
SLACK_API_HIST   = "https://slack.com/api/conversations.history"


# ────────────────────────────────────────────────────────────────
# 内部: Slack API呼び出し
# ────────────────────────────────────────────────────────────────

def _post(text: str, channel: str = COIN_CHANNEL) -> bool:
    """テキストをSlackに投稿する。成功時 True。"""
    if not SLACK_BOT_TOKEN:
        print("[report_to_marke] SLACK_BOT_TOKEN が未設定です。投稿をスキップ。", file=sys.stderr)
        return False
    payload = {"channel": channel, "text": text}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SLACK_API_POST, data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        if not res.get("ok"):
            print(f"[report_to_marke] Slack error: {res.get('error')}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[report_to_marke] 通信エラー: {e}", file=sys.stderr)
        return False


# ────────────────────────────────────────────────────────────────
# 公開API
# ────────────────────────────────────────────────────────────────

def report_done(
    task: str,
    content: list[str],
    changes: list[str],
    result: str,
    evidence: str = "",
    remaining: list[str] | None = None,
) -> bool:
    """
    業務完了報告を #coin-cap-marke に送信する。

    Args:
        task:      タスク名 (例: "スプシ初版作成")
        content:   実施内容リスト
        changes:   変更箇所リスト
        result:    結果サマリー文字列
        evidence:  証跡URL or ファイルパス (任意)
        remaining: 残課題リスト (任意)
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content_str  = "\n".join(f"• {c}" for c in content)
    changes_str  = "\n".join(f"• {c}" for c in changes)
    remaining_str = "\n".join(f"• {r}" for r in (remaining or [])) or "なし"
    evidence_str  = evidence or "なし"

    text = f"""【キャップ⇒マーケ】完了報告: {task}
【差出人】キャップ（Claude Code） 【宛先】マーケ

■ 実施内容
{content_str}

■ 変更箇所
{changes_str}

■ 結果
{result}

■ 証跡
{evidence_str}

■ 残課題
{remaining_str}

({now})"""
    return _post(text)


def report_progress(
    task: str,
    progress: str,
    eta: str = "",
    note: str = "",
) -> bool:
    """進捗報告を送信する。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    eta_str  = f" / ETA: {eta}" if eta else ""
    note_str = f"\n{note}" if note else ""
    text = f"【キャップ⇒マーケ】進捗報告: {task}\n【差出人】キャップ（Claude Code） 【宛先】マーケ\n\n{progress}{eta_str}{note_str}\n\n({now})"
    return _post(text)


def report_blocked(
    task: str,
    reason: str,
    ceo_action: str,
) -> bool:
    """
    滞留報告（CEO対応必要）を送信する。

    Args:
        task:       ブロックされているタスク名
        reason:     ブロック理由 (権限不足 / 仕様衝突 / 本番影響 等)
        ceo_action: CEOに求めるアクション
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"""【キャップ⇒マーケ】滞留報告: {task}
【差出人】キャップ（Claude Code） 【宛先】マーケ

■ 理由
{reason}

■ CEOへの依頼
{ceo_action}

({now} by cap)"""
    return _post(text)


def check_channel(limit: int = 5) -> list[dict]:
    """
    #coin-cap-marke の未返信メッセージを返す。
    セッション開始時に呼んで未読を確認する。
    """
    if not SLACK_BOT_TOKEN:
        return []
    req = urllib.request.Request(
        f"{SLACK_API_HIST}?channel={COIN_CHANNEL}&limit={limit}",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
    except Exception as e:
        print(f"[report_to_marke] channel check error: {e}", file=sys.stderr)
        return []

    if not res.get("ok"):
        return []

    messages = res.get("messages", [])
    human = [m for m in messages if m.get("user") not in (BOT_USER_ID, None)]
    return human


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="コイン事業 Slack報告ツール")
    sub = parser.add_subparsers(dest="cmd")

    # done
    p_done = sub.add_parser("done", help="完了報告を送信")
    p_done.add_argument("--task",    required=True)
    p_done.add_argument("--content", nargs="+", default=["(詳細未記載)"])
    p_done.add_argument("--changes", nargs="+", default=["(変更箇所未記載)"])
    p_done.add_argument("--result",  required=True)
    p_done.add_argument("--evidence", default="")
    p_done.add_argument("--remaining", nargs="+", default=[])

    # progress
    p_prog = sub.add_parser("progress", help="進捗報告を送信")
    p_prog.add_argument("--task",     required=True)
    p_prog.add_argument("--progress", required=True)
    p_prog.add_argument("--eta",      default="")

    # blocked
    p_blk = sub.add_parser("blocked", help="滞留報告を送信")
    p_blk.add_argument("--task",       required=True)
    p_blk.add_argument("--reason",     required=True)
    p_blk.add_argument("--ceo-action", required=True, dest="ceo_action")

    # check
    sub.add_parser("check", help="チャンネル未読確認")

    args = parser.parse_args()

    if args.cmd == "done":
        ok = report_done(
            task=args.task, content=args.content, changes=args.changes,
            result=args.result, evidence=args.evidence, remaining=args.remaining,
        )
        print("sent" if ok else "FAILED")

    elif args.cmd == "progress":
        ok = report_progress(task=args.task, progress=args.progress, eta=args.eta)
        print("sent" if ok else "FAILED")

    elif args.cmd == "blocked":
        ok = report_blocked(task=args.task, reason=args.reason, ceo_action=args.ceo_action)
        print("sent" if ok else "FAILED")

    elif args.cmd == "check":
        msgs = check_channel(limit=10)
        if msgs:
            print(f"未返信メッセージ {len(msgs)}件:")
            for m in msgs:
                print(f"  [{m.get('ts')}] {m.get('text','')[:100]}")
        else:
            print("未返信なし")

    else:
        parser.print_help()
