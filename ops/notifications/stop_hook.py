#!/usr/bin/env python3
"""Claude Stop Hook - セッション終了時の未報告チェック v1.0

動作:
  1. state/.report_pending ファイルが存在する場合 → 未報告と判断
  2. Slackに「未報告タスクあり」の警告を送信
  3. 連続発火を防ぐため最終実行から30秒以内はスキップ

呼び出し元: .claude/settings.local.json の Stop hook
"""

import sys
import os
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).parent.parent.parent
PENDING_FLAG = ROOT_DIR / "state" / ".report_pending"
LAST_RUN_FILE = ROOT_DIR / "state" / ".stop_hook_last_run"
FORBIDDEN_READS_LOG = ROOT_DIR / "state" / "forbidden_reads.log"
FORBIDDEN_ALERTED_FLAG = ROOT_DIR / "state" / ".forbidden_reads_alerted_date"
DEFAULT_CHANNEL = "C0AQASABVL7"  # #web-cyber_marke_clow
COOLDOWN_SECONDS = 30
FORBIDDEN_DAILY_THRESHOLD = 3  # 1 日 3 回以上の禁忌 Read 試行で WARN


def _load_env():
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _is_cooldown() -> bool:
    """クールダウン中かチェック"""
    if not LAST_RUN_FILE.exists():
        return False
    try:
        last = datetime.fromisoformat(LAST_RUN_FILE.read_text().strip())
        return (datetime.now() - last).total_seconds() < COOLDOWN_SECONDS
    except Exception:
        return False


def _update_last_run():
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(datetime.now().isoformat())


def _post_slack(text: str, token: str) -> bool:
    payload = json.dumps({"channel": DEFAULT_CHANNEL, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read())
        return res.get("ok", False)
    except Exception:
        return False


def _check_forbidden_reads(token: str) -> None:
    """禁忌ファイル Read 試行を 1 日集約して WARN (Codex 推奨 B).

    state/forbidden_reads.log の今日分を読み、blocked count >= FORBIDDEN_DAILY_THRESHOLD
    なら 1 日 1 回 Slack 通知. 連続発火を防ぐため state/.forbidden_reads_alerted_date で
    最後の通知日を記録.
    """
    if not FORBIDDEN_READS_LOG.exists():
        return
    today = datetime.now().date().isoformat()
    # 今日既に通知済?
    if FORBIDDEN_ALERTED_FLAG.exists():
        try:
            if FORBIDDEN_ALERTED_FLAG.read_text().strip() == today:
                return
        except Exception:
            pass
    blocked_today = []
    try:
        for line in FORBIDDEN_READS_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("ts", "").startswith(today) and rec.get("result") == "blocked":
                    blocked_today.append(rec)
            except Exception:
                continue
    except Exception:
        return
    if len(blocked_today) < FORBIDDEN_DAILY_THRESHOLD:
        return
    samples = blocked_today[:3]
    msg_lines = [
        ":no_entry: *Claude 禁忌ファイル Read 試行 集約 WARN*",
        f"本日 {len(blocked_today)} 回 host のレガシー JSON を Read しようとしてブロックされました.",
        "原因の可能性: SSOT (ops/room_status.py / state/follow_runtime_state.json) を見ずに",
        "host の Plan v6 凍結ファイルで状況判断しようとした.",
        "",
        "サンプル (上位 3 件):",
    ]
    for s in samples:
        msg_lines.append(f"  - {s.get('ts')} {s.get('tool')} {s.get('file_path')}")
    msg_lines += [
        "",
        "確認: `python ops/room_status.py --human` で正しい SSOT 状況を取得",
        "詳細ログ: state/forbidden_reads.log",
    ]
    _post_slack("\n".join(msg_lines), token)
    FORBIDDEN_ALERTED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    FORBIDDEN_ALERTED_FLAG.write_text(today)


def main():
    _load_env()
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        sys.exit(0)

    # クールダウンチェック
    if _is_cooldown():
        sys.exit(0)

    _update_last_run()

    # Codex 推奨 B: 禁忌 Read 集約 WARN (1 日 1 回まで)
    _check_forbidden_reads(token)

    # 未報告フラグチェック
    if not PENDING_FLAG.exists():
        sys.exit(0)

    # 未報告フラグが立っている → 警告送信
    try:
        pending = json.loads(PENDING_FLAG.read_text(encoding="utf-8"))
        task_desc = pending.get("task_desc", "")
        created_at = pending.get("created_at", "")
    except Exception:
        task_desc = ""
        created_at = ""

    msg = (
        f":warning: *サイバー未報告検知*\n"
        f"作業が完了しましたが、Slack報告が送信されていません。\n"
        f"タスク: {task_desc or '(未指定)'}\n"
        f"開始: {created_at}\n"
        f"`python ops/notifications/slack_reporter.py \"【サイバー報告 #NNN】...\"` を実行してください。"
    )
    _post_slack(msg, token)
    sys.exit(0)


if __name__ == "__main__":
    main()
