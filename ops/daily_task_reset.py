#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
毎朝 06:00 に Task Scheduler を全有効化するリセットスクリプト
CTO指示 2026-04-28: 4実装が前日100%到達後 disable → 翌朝 enable で日次サイクル復帰
"""
from __future__ import annotations
import io
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 4実装ごとの Task Scheduler 名
TASKS = [
    # POST
    "RoomBot_POST_Batch1",
    "RoomBot_POST_Batch2",
    "RoomBot_POST_Batch3",
    # LIKE
    "RoomBot_LIKE_Hourly",
    # FOLLOWBACK
    "RoomBot_FOLLOWBACK_Hourly",
    "RoomBot_FB_SourceFeed_4h",
    # FOLLOW (host runner) は 2026-04-30 CEO指示で永久停止 → リセット対象外
    # "FollowHostRunner_15min",  # VM専用化のため除外
]


def sync_yesterday_sheet() -> None:
    """前日分をスプシに自動書き込み（CEO指示: 0時になったら前日分を完了させること）"""
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    script = Path(__file__).resolve().parent / "sheets" / "daily_log_writer.py"
    if not script.exists():
        print(f"[WARN] daily_log_writer not found: {script}")
        return
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--date", yesterday],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            print(f"[OK] sheet sync yesterday ({yesterday}) done")
        else:
            print(f"[WARN] sheet sync returned rc={r.returncode}: {out[-200:]}")
        if out:
            print(out[-300:])
    except Exception as e:
        print(f"[WARN] sheet sync exception: {e}")


def sync_yesterday_limit_analysis() -> None:
    """前日分のフォロー上限分析データを05_上限分析タブへ書き込み（CEO指示 2026-05-01）"""
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    script = Path(__file__).resolve().parent / "sheets" / "limit_analysis_writer.py"
    if not script.exists():
        print(f"[WARN] limit_analysis_writer not found: {script}")
        return
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--date", yesterday],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            print(f"[OK] limit_analysis sync yesterday ({yesterday}) done")
        else:
            print(f"[WARN] limit_analysis sync rc={r.returncode}: {out[-200:]}")
        if out:
            print(out[-200:])
    except Exception as e:
        print(f"[WARN] limit_analysis sync exception: {e}")


def generate_today_post_plan() -> None:
    """本日分の POST plan を生成 (CEO 指示 2026-05-26: POST plan 自動生成が抜け落ちていた).

    真因: daily_task_reset は task enable / sheet sync のみで POST plan 生成が無く、
    Replenish_Daily は genre_pool 補充のみで post_queue は触らない。結果として
    5/26 朝 9:00 POST Batch1 が走った時 post_queue が完全に空 → 投稿 0 件で
    本日 POST=0/13 直行となっていた。

    解決: 06:00 daily reset 時に `python run.py plan` を実行して post_queue に
    今日分の investment を seed する。
    """
    run_py = Path(__file__).resolve().parent.parent / "rakuten-room" / "bot" / "run.py"
    if not run_py.exists():
        print(f"[WARN] run.py not found: {run_py}")
        return
    try:
        r = subprocess.run(
            [sys.executable, str(run_py), "plan"],
            cwd=str(run_py.parent),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180
        )
        out = (r.stdout or "")[-500:]
        if r.returncode == 0:
            print(f"[OK] post plan generated for today")
        else:
            print(f"[WARN] post plan rc={r.returncode}: {out}")
    except Exception as e:
        print(f"[WARN] post plan exception: {e}")


def main():
    print(f"=== daily_task_reset start: {datetime.now()} ===")

    # 前日スプシ書き込み（最優先・タスク再有効化より先に実行）
    sync_yesterday_sheet()
    # 前日フォロー上限分析データを05_上限分析へ書き込み（CEO指示 2026-05-01）
    sync_yesterday_limit_analysis()
    # 本日 POST plan 生成 (2026-05-26 fix: plan 自動生成が抜けていた)
    generate_today_post_plan()

    enabled = []
    failed = []
    for tn in TASKS:
        try:
            r = subprocess.run(
                ["schtasks", "/change", "/tn", tn, "/enable"],
                capture_output=True, text=True, encoding="cp932", errors="replace", timeout=10
            )
            if r.returncode == 0:
                enabled.append(tn)
            else:
                failed.append((tn, r.stderr[:100] if r.stderr else "unknown"))
        except Exception as e:
            failed.append((tn, str(e)[:100]))
    print(f"[OK] enabled: {len(enabled)} tasks")
    for t in enabled:
        print(f"  + {t}")
    if failed:
        print(f"[WARN] failed: {len(failed)} tasks")
        for t, e in failed:
            print(f"  - {t}: {e}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
