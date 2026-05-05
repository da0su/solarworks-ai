#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM 内 setup を HOST から VBoxManage 経由で完全自動実行.

CEO 「全自動化」指示で setup_vm_v6.bat の手動実行を排除。
keyboardputstring で VM 内 cmd を制御し、setup を全自動化する。

実行: python ops/vm_v6/auto_vm_setup.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 既存 vm_follow_launcher.py の putstr_jp を流用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vm_follow_launcher import (  # type: ignore
    run_vbox, scancode, putstr, putstr_jp, vm_running,
    VBOXMANAGE, VM_NAME, _NO_WINDOW,
)

NO_WIN = _NO_WINDOW


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_vm_ready(timeout: int = 120) -> bool:
    """GuestAdditionsRunLevel=3 + IME 切替 (+30秒)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = run_vbox("showvminfo", VM_NAME, "--machinereadable")
        if rc == 0:
            for line in (out or "").splitlines():
                if line.startswith("GuestAdditionsRunLevel="):
                    level = line.split("=", 1)[1].strip().strip('"')
                    if level == "3":
                        log(f"  GuestAdditionsRunLevel=3 OK")
                        log(f"  IME 切替待機 +30s")
                        time.sleep(30)
                        return True
                    break
        time.sleep(3)
    log(f"  TIMEOUT {timeout}s")
    return False


def open_cmd_window():
    """VM 内に cmd window を foreground で起動."""
    log("Opening cmd window in VM (Win+R → cmd)")
    # Win+D ×2 でデスクトップ前面化
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.0)
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.5)
    # Win+R
    scancode("e0", "5b", "13", "93", "e0", "db")
    time.sleep(2.0)
    putstr("cmd")
    time.sleep(0.3)
    scancode("1c", "9c")  # Enter
    time.sleep(2.5)


def send_cmd(cmd: str, wait_sec: float = 1.0):
    """1 コマンドを cmd window に送信."""
    putstr_jp(cmd)
    time.sleep(0.3)
    scancode("1c", "9c")  # Enter
    time.sleep(wait_sec)


def wait_http_alive(timeout: int = 120) -> bool:
    """VM HTTP server が応答するまで待つ."""
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get("http://localhost:18765/healthz", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-port-forward", action="store_true")
    parser.add_argument("--skip-pip", action="store_true",
                        help="pip install を skip (既にインストール済の場合)")
    parser.add_argument("--skip-playwright-install", action="store_true",
                        help="playwright install chromium を skip")
    parser.add_argument("--skip-profile-copy", action="store_true",
                        help="4 profile copy を skip")
    args = parser.parse_args()

    log("=" * 60)
    log("VM v6 自動 setup START (CEO 承認のみ・全自動)")
    log("=" * 60)

    # Step A: port forward 設定 (VM 停止 → modifyvm → 起動)
    if not args.skip_port_forward:
        log("[A] Port Forward 設定: vmhttp 8765 → host 18765")
        if vm_running():
            log("  VM 停止 (acpipowerbutton)")
            run_vbox("controlvm", VM_NAME, "acpipowerbutton")
            for _ in range(60):
                rc, out = run_vbox("showvminfo", VM_NAME, "--machinereadable")
                if "VMState=\"poweroff\"" in (out or ""):
                    break
                time.sleep(2)
            log("  VM poweroff 確認")

        # 既存の natpf1 vmhttp があれば削除してから登録 (idempotent)
        run_vbox("modifyvm", VM_NAME, "--natpf1", "delete", "vmhttp")
        rc, out = run_vbox("modifyvm", VM_NAME, "--natpf1", "vmhttp,tcp,,18765,,8765")
        log(f"  natpf1 設定 rc={rc}")

        # VM 起動
        log("  VM 起動 (gui mode)")
        run_vbox("startvm", VM_NAME, "--type", "gui")

        # 起動待機
        if not wait_vm_ready(timeout=180):
            log("  ERROR: VM not ready")
            return 2

    # Step B: VM 内に cmd window 起動
    log("[B] VM 内 cmd window 起動")
    open_cmd_window()

    # Step C: setup_vm_v6.bat を VM 内に copy → 実行
    # bat 内で全 step (mkdir / copy / pip / playwright / profile copy / startup register / server起動) を逐次実行
    log("[C] setup_vm_v6.bat を VM 内 cmd で copy & 実行 (合計 15-30分)")
    send_cmd('copy /Y "\\\\vboxsvr\\share\\..\\..\\..\\ops\\vm_v6\\setup_vm_v6.bat" "%USERPROFILE%\\Desktop\\setup_vm_v6.bat"', 1.5)
    send_cmd('"%USERPROFILE%\\Desktop\\setup_vm_v6.bat"', 1.0)

    # Step D: 完了 marker (HOST 上 .setup_done) を待つ (max 30min)
    log("[D] setup 完了 marker 待機 (最大 30分)")
    setup_done_marker = Path(__file__).resolve().parent / ".setup_done"
    if setup_done_marker.exists():
        setup_done_marker.unlink()  # 古い marker 削除
    deadline = time.time() + 1800  # 30min
    last_log = 0
    while time.time() < deadline:
        if setup_done_marker.exists():
            log("  ✅ .setup_done marker 検出")
            break
        # 60秒に1回 progress log
        if time.time() - last_log > 60:
            elapsed_min = (time.time() - (deadline - 1800)) / 60
            log(f"  [waiting] {elapsed_min:.1f}分経過...")
            last_log = time.time()
        time.sleep(10)
    else:
        log("  ❌ TIMEOUT 30分: setup 完了 marker 出ず")
        return 4

    # Step E: HTTP server 疎通確認
    log("[E] HOST から HTTP server 疎通確認 (timeout 2分)")
    if wait_http_alive(timeout=120):
        log("  ✅ VM HTTP server alive")
    else:
        log("  ❌ HTTP server unreachable")
        return 3

    log("=" * 60)
    log("VM v6 自動 setup 完了 ✅")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
