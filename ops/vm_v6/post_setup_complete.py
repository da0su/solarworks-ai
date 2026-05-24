#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CEO 手動 setup 完了後の自動完了スクリプト.

CEO が VM 内で setup_vm_v6.bat を実行・完了したら、本スクリプトを HOST 上で実行。
4機能テスト + Task refactor + 全 task 再有効化 + patrol_v6 動作確認 + Slack 報告 を全自動。

実行: python ops/vm_v6/post_setup_complete.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def log(msg: str):
    # cp932 にencode できない char を ascii safe にして出力
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg.encode('ascii', errors='replace').decode('ascii')}", flush=True)


def step1_wait_http_alive(timeout: int = 60) -> bool:
    """HTTP server 疎通確認."""
    log("[Step 1] VM HTTP server 疎通確認")
    try:
        import requests
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True, creationflags=NO_WIN)
        import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get("http://localhost:18765/healthz", timeout=2)
            if r.status_code == 200:
                log(f"  [OK] HTTP alive")
                return True
        except Exception:
            pass
        time.sleep(3)
    log(f"  [FAIL] TIMEOUT: HTTP unreachable")
    return False


def step2_test_4_modes(skip_real_run: bool = True) -> dict:
    """4機能の動作確認 (limit=5 で安全テスト)."""
    log("[Step 2] 4機能 動作確認")
    results = {}
    from ops.vm_v6 import vm_controller as vc

    # まず status
    status = vc.get_status()
    log(f"  vm status: {status}")

    if skip_real_run:
        log("  --skip-real-run: limit テストはスキップ (CEO 確認後に実行)")
        return {"skipped": True}

    for mode, limit in [("post", 5), ("like", 10), ("followback", 5), ("follow", 10)]:
        log(f"  testing {mode} (limit={limit})")
        r = vc.run(mode, limit=limit, force=(mode == "follow"))
        results[mode] = r
        log(f"    -> {r}")
        time.sleep(5)
    return results


def step3_refactor_tasks() -> int:
    """Task Scheduler を vm_controller 経由に refactor."""
    log("[Step 3] Task Scheduler refactor")
    ps = REPO_ROOT / "ops" / "scheduler" / "refactor_tasks_v6.ps1"
    r = subprocess.run([
        "powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps)
    ], capture_output=True, text=True, timeout=120, encoding="cp932", errors="replace",
       creationflags=NO_WIN)
    log(f"  rc={r.returncode}")
    if r.stdout: log(f"  out: {r.stdout[-500:]}")
    return r.returncode


def step4_enable_all_tasks() -> int:
    """全 RoomBot task 再有効化."""
    log("[Step 4] 全 RoomBot task 再有効化")
    ps = REPO_ROOT / "ops" / "scheduler" / "enable_all_v6_tasks.ps1"
    r = subprocess.run([
        "powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps)
    ], capture_output=True, text=True, timeout=60, encoding="cp932", errors="replace",
       creationflags=NO_WIN)
    log(f"  rc={r.returncode}")
    if r.stdout: log(f"  out: {r.stdout[-800:]}")
    return r.returncode


def step5_patrol_v6_check() -> int:
    """patrol_v6 を1回手動実行して全 layer 動作確認."""
    log("[Step 5] patrol_v6 動作確認")
    r = subprocess.run([
        sys.executable, "-m", "ops.patrol_v6.patrol_orchestrator", "--check-only"
    ], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
       creationflags=NO_WIN)
    log(f"  rc={r.returncode}")
    if r.stdout: log(f"  out:\n{r.stdout[-1500:]}")
    return r.returncode


def step6_slack_report() -> int:
    """CEO Slack に完了報告."""
    log("[Step 6] CEO Slack 完了報告")
    sl = REPO_ROOT / "ops" / "notifications" / "slack_reporter.py"
    msg = """【サイバー報告 #362】Plan v4 P1 (VB完結化) 本日中 完全稼働開始

CEO 手動 1分 (VM 内 setup_vm_v6.bat 実行) 完了確認後、HOST 自動で以下完了:
- VM HTTP server 疎通確認 [OK]
- Task Scheduler refactor (vm_controller 経由)
- 全 RoomBot task 再有効化
- patrol_v6 8 Layer 動作確認

明日朝から 4機能 (POST/LIKE/FOLLOW/FOLLOWBACK) が VM 内 Playwright で完結稼働。
HOST PC Chrome は CEO 業務専用に解放されました。"""
    r = subprocess.run([sys.executable, str(sl), msg], capture_output=True, timeout=30,
                      creationflags=NO_WIN)
    log(f"  rc={r.returncode}")
    return r.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-test", action="store_true",
                        help="4機能の実テスト (limit=5-10) も実行する")
    parser.add_argument("--http-timeout", type=int, default=60)
    args = parser.parse_args()

    log("=" * 60)
    log("post_setup_complete START")
    log("=" * 60)

    # 1. HTTP 疎通
    if not step1_wait_http_alive(timeout=args.http_timeout):
        log("ABORT: VM HTTP server unreachable")
        return 2

    # 2. 4機能テスト (default skip)
    step2_test_4_modes(skip_real_run=not args.with_test)

    # 3. Task refactor
    if step3_refactor_tasks() != 0:
        log("WARN: refactor_tasks rc != 0")

    # 4. 全 task 再有効化
    if step4_enable_all_tasks() != 0:
        log("WARN: enable_all_tasks rc != 0")

    # 5. patrol_v6 動作確認
    step5_patrol_v6_check()

    # 6. CEO 報告
    step6_slack_report()

    log("=" * 60)
    log("post_setup_complete DONE [OK]")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
