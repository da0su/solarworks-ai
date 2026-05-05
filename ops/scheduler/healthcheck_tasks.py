#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase B-5: Windows Task Scheduler 健全性監視

毎日 00:00 に実行され、必要な RoomBot_* タスクが揃っているかチェックする。
欠落があれば Slack に通知する。

CEO の「bat を押すだけ」設計を補強：
  - 新マシンセットアップ時の task 登録漏れを早期検知
  - patrol などの critical task が誤って削除された場合の検知
  - 「設定したつもり」状態の防止

実行: python ops/scheduler/healthcheck_tasks.py [--check-only]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# 必須タスク一覧 (Phase A/B/C 全完了時の最終形)
REQUIRED_TASKS = {
    # Critical: bot 4機能
    "RoomBot_POST_Batch1":             {"category": "post",        "critical": True},
    "RoomBot_POST_Batch2":             {"category": "post",        "critical": True},
    "RoomBot_POST_Batch3":             {"category": "post",        "critical": True},
    "RoomBot_LIKE_Hourly":             {"category": "like",        "critical": True},
    "RoomBot_FOLLOWBACK_Hourly":       {"category": "followback",  "critical": True},
    "RoomBotFollow_Hourly":            {"category": "follow",      "critical": True},
    # Critical: 監視 + reset
    "RoomBot_Patrol_Hourly":           {"category": "monitor",     "critical": True},
    "RoomBot_DailyReset_06":           {"category": "monitor",     "critical": True},
    # Critical: source / pool 補給
    "RoomBot_FB_SourceFeed_4h":        {"category": "source",      "critical": True},
    "RoomBot_Replenish_Daily":         {"category": "source",      "critical": True},   # Phase B-1
    # Self-monitoring
    "RoomBot_TaskHealthcheck_Daily":   {"category": "monitor",     "critical": False},  # 自分自身
}


def list_existing_tasks() -> set[str]:
    """Windows Task Scheduler から RoomBot_* で始まる現存 task 名のセットを返す"""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30, encoding="cp932", errors="replace"
        )
        if result.returncode != 0:
            return set()
        existing = set()
        for line in result.stdout.splitlines():
            # CSV: "TaskName","Next Run Time","Status"
            parts = [p.strip().strip('"') for p in line.split(",", 2)]
            if not parts:
                continue
            tn = parts[0].lstrip("\\")  # remove leading backslash
            if tn.startswith("RoomBot"):
                existing.add(tn)
        return existing
    except Exception as e:
        print(f"[healthcheck_tasks] schtasks query failed: {e}", file=sys.stderr)
        return set()


def slack_alert(missing_critical: list[str], missing_optional: list[str]) -> None:
    """Slack に欠落タスクをレポート"""
    try:
        from ops.notifications import slack_reporter
    except Exception:
        try:
            sys.path.insert(0, str(REPO_ROOT / "ops"))
            from notifications import slack_reporter
        except Exception as e:
            print(f"[healthcheck_tasks] slack module unavailable: {e}", file=sys.stderr)
            return

    msg_lines = []
    if missing_critical:
        msg_lines.append("<!channel> 【Task Scheduler 健全性 ALERT】 critical task 欠落:")
        for t in missing_critical:
            cat = REQUIRED_TASKS[t]["category"]
            msg_lines.append(f"  - {t} ({cat})")
    if missing_optional:
        msg_lines.append("")
        msg_lines.append("Optional task 欠落:")
        for t in missing_optional:
            msg_lines.append(f"  - {t}")
    msg_lines.append("")
    msg_lines.append("対応: ops/scheduler/setup_scheduler.ps1 を再実行 or 該当の set_*.ps1 で個別登録")

    try:
        slack_reporter.send_to_slack("\n".join(msg_lines))
        print("[healthcheck_tasks] Slack alert sent")
    except Exception as e:
        print(f"[healthcheck_tasks] slack send failed: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows Task Scheduler 健全性チェック")
    parser.add_argument("--check-only", action="store_true",
                        help="alert なしで結果のみ出力")
    args = parser.parse_args()

    existing = list_existing_tasks()
    missing_critical = []
    missing_optional = []

    for task_name, meta in REQUIRED_TASKS.items():
        if task_name not in existing:
            if meta["critical"]:
                missing_critical.append(task_name)
            else:
                missing_optional.append(task_name)

    print(f"[healthcheck_tasks] {datetime.now().isoformat()}")
    print(f"  existing: {len(existing)} tasks (RoomBot_*)")
    print(f"  required critical: {sum(1 for v in REQUIRED_TASKS.values() if v['critical'])}")
    print(f"  missing critical: {len(missing_critical)}")
    print(f"  missing optional: {len(missing_optional)}")
    if missing_critical:
        print("  CRITICAL MISSING:")
        for t in missing_critical:
            print(f"    - {t}")
    if missing_optional:
        print("  OPTIONAL MISSING:")
        for t in missing_optional:
            print(f"    - {t}")

    if not args.check_only and (missing_critical or missing_optional):
        slack_alert(missing_critical, missing_optional)

    # exit code: 0=ok, 2=critical missing
    return 2 if missing_critical else 0


if __name__ == "__main__":
    sys.exit(main())
