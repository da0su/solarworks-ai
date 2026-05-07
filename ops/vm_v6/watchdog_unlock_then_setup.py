#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM ロック解除を待って setup を autonomous 再開する watchdog.

Plan v6 Phase B 自動再開トリガー。
sandbox や security boundary で CEO の VM password を取得できないが、
CEO が VM 画面を一度 unlock すれば、その瞬間 (画面ピクセルが lock 画面と異なる) を
HOST 側で screenshot 比較で検知 → 即 setup_vm_v6.bat keystroke を発火する。

検出ロジック:
    VBoxManage controlvm RoomBot screenshotpng で VM 画面を 60 秒毎に snapshot.
    lock screen のシグネチャ (大時計の白テキストが画面下部にある) を画像 hash で照合.
    hash が変化 → desktop 復帰 → keystroke setup を実行.

実行: python ops/vm_v6/watchdog_unlock_then_setup.py
バックグラウンドで起動推奨 (run_in_background=true).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "RoomBot"
SCREENSHOT_PATH = REPO_ROOT / "state" / "vm_unlock_watch.png"
MARKER_PATH = REPO_ROOT / "ops" / "vm_v6" / ".setup_done"
NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

CHECK_INTERVAL = 60   # 秒
LOCK_HASH_HISTORY: list[str] = []


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def take_screenshot() -> bytes:
    """VM screenshot の bytes."""
    SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [VBOXMANAGE, "controlvm", VM_NAME, "screenshotpng", str(SCREENSHOT_PATH)],
            capture_output=True, timeout=10, creationflags=NO_WIN,
        )
        if SCREENSHOT_PATH.exists():
            return SCREENSHOT_PATH.read_bytes()
    except Exception as e:
        log(f"  screenshot err: {e}")
    return b""


LOCK_PNG_SIZE_THRESHOLD = 80_000  # bytes. lock screen は単色多 → PNG が小さい
# 観測値: 現状 lock 画面 = 40KB / sticky keys dialog ありで 42KB. desktop = 通常 200KB+


def is_locked_by_screenshot_size(img_bytes: bytes) -> bool:
    """PNG ファイルサイズで lock 判定 (lock = 単色多 → 小サイズ)."""
    return 0 < len(img_bytes) < LOCK_PNG_SIZE_THRESHOLD


def get_user_usage_state() -> str:
    """VBoxManage guestproperty で cyber user の UsageState 取得.

    Returns: 'Idle' (lock 中 or 待機) / 'InUse' (active) / '' (取得不能)
    """
    try:
        r = subprocess.run(
            [VBOXMANAGE, "guestproperty", "enumerate", VM_NAME],
            capture_output=True, text=True, timeout=10, creationflags=NO_WIN,
        )
        for line in (r.stdout or "").splitlines():
            if "UsageState" in line and "/User/" in line:
                # 例: /VirtualBox/GuestInfo/User/cyber@DESKTOP-1N4BBTO/UsageState = 'Idle'
                if "'InUse'" in line:
                    return "InUse"
                if "'Idle'" in line:
                    return "Idle"
        return ""
    except Exception as e:
        log(f"  guestproperty err: {e}")
        return ""


def trigger_setup():
    """VM unlock 検知 → auto_vm_setup.py を spawn."""
    log("UNLOCK DETECTED → setup_v6.py 発火")
    py = sys.executable
    setup_script = REPO_ROOT / "ops" / "vm_v6" / "auto_vm_setup.py"
    log_file = REPO_ROOT / "state" / "vm_setup_after_unlock.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as fp:
        proc = subprocess.Popen(
            [py, str(setup_script), "--skip-port-forward"],
            stdout=fp, stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT),
            creationflags=NO_WIN,
        )
    log(f"  spawned pid={proc.pid} → log={log_file}")


def main():
    log("=" * 60)
    log("VM unlock watchdog START (Plan v6 Phase B 自動再開)")
    log(f"  check interval: {CHECK_INTERVAL}s")
    log(f"  marker: {MARKER_PATH}")
    log("=" * 60)

    triggered = False
    triggered_at = 0.0  # 最後に発火した時刻 (epoch)
    SETUP_TIMEOUT = 30 * 60  # 30 分で marker 出なければ再 trigger 可能とする
    while True:
        # 既に setup 完了している (marker 存在) なら exit
        if MARKER_PATH.exists():
            log(f"setup_done marker 検出 → watchdog 終了")
            return 0

        img = take_screenshot()
        size = len(img)
        size_lock = is_locked_by_screenshot_size(img)
        usage = get_user_usage_state()
        # 2 軸判定: PNG サイズ (lock=単色多=小) + GuestProperty UsageState (cyber=InUse なら active)
        is_locked = size_lock and usage != "InUse"
        log(f"  png_size={size}b size_lock={size_lock} usage={usage!r} → locked={is_locked} triggered={triggered}")

        # triggered 状態でも SETUP_TIMEOUT 経過 + 依然 marker 不在 → 再 trigger 可能に reset
        if triggered and (time.time() - triggered_at) > SETUP_TIMEOUT:
            log(f"setup timeout {SETUP_TIMEOUT}s 経過 + marker 出ず → triggered reset (再発火可能)")
            triggered = False

        if not is_locked and not triggered:
            log("UNLOCK detected → setup 発火")
            trigger_setup()
            triggered = True
            triggered_at = time.time()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(130)
