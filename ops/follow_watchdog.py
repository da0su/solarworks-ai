#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOW Watchdog - 30分ごとに実行し、logが更新されているか検証してから復旧する。

設計:
  1. log_age > STALE_THRESHOLD_MIN → R3c (poweroff → startvm → wait → launch)
  2. R3c後 VERIFY_WAIT_MIN 待機 → log再確認
  3. 回復確認 → OK
  4. log依然stale → Slack ALARM (人手確認要)

Windows Task Scheduler: 毎30分 (00/30分) に呼び出す
"""
from __future__ import annotations
import io, sys, json, subprocess, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "RoomBot"
WATCHDOG_LOG = ROOT / "ops" / "follow_watchdog.log"

STALE_THRESHOLD_MIN = 45    # これ以上古いlog → 復旧開始
VERIFY_WAIT_MIN = 12         # R3c後の検証待機時間
VM_BOOT_WAIT_SEC = 130       # VM起動後の待機秒数


def wlog(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_log_age_min() -> float:
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not data:
            return 9999.0
        last_ts = datetime.fromisoformat(data[-1]["timestamp"][:19])
        return (datetime.now() - last_ts).total_seconds() / 60
    except Exception:
        return 9999.0


def slack_alarm(msg: str):
    token = ""
    for path_s in [".env", "ops/notifications/.env", "rakuten-room/bot/.env"]:
        p = ROOT / path_s
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if "SLACK_BOT_TOKEN" in line and "=" in line:
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if token:
                        break
        if token:
            break
    if not token:
        return
    try:
        data = json.dumps({"channel": "C0AQASABVL7", "text": msg}).encode("utf-8")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage", data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# 2026-05-05 礎: cmd window flash 抑制
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def vm_is_running() -> bool:
    try:
        r = subprocess.run([VBOXMANAGE, "list", "runningvms"], capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        return VM_NAME in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return False


def r3c_reset():
    """Hard reset: poweroff → startvm → wait → launch"""
    wlog("R3c: poweroff VM")
    subprocess.run([VBOXMANAGE, "controlvm", VM_NAME, "poweroff"], capture_output=True, timeout=30, creationflags=_NO_WINDOW)
    time.sleep(5)

    wlog(f"R3c: startvm (headless)")
    subprocess.run([VBOXMANAGE, "startvm", VM_NAME, "--type", "headless"], capture_output=True, timeout=60, creationflags=_NO_WINDOW)

    wlog(f"R3c: waiting {VM_BOOT_WAIT_SEC}s for boot")
    time.sleep(VM_BOOT_WAIT_SEC)

    wlog("R3c: launching follow bot")
    r = subprocess.run(
        [sys.executable, str(ROOT / "ops" / "vm_follow_launcher.py"), "--force"],
        capture_output=True, timeout=120, creationflags=_NO_WINDOW
    )
    wlog(f"R3c: launcher rc={r.returncode}")


def main():
    age = get_log_age_min()
    wlog(f"follow_watchdog start: log_age={age:.1f}min (threshold={STALE_THRESHOLD_MIN}min)")

    if age <= STALE_THRESHOLD_MIN:
        wlog("OK: log is fresh, no action needed")
        return

    # stale → check if this is nighttime daily reset window (00:00-04:00 log stale is expected)
    now_hour = datetime.now().hour
    if now_hour < 4 and age < 120:
        wlog(f"SKIP: nighttime window (hour={now_hour}, age={age:.1f}min < 120min) - normal gap")
        return

    wlog(f"STALE DETECTED: log_age={age:.1f}min > {STALE_THRESHOLD_MIN}min → starting R3c")
    slack_alarm(f"⚠️ [FOLLOW watchdog] log stale {age:.0f}min → R3c開始 (VM poweroff→restart)")

    # R3c
    r3c_reset()

    # Verify after wait
    wlog(f"Waiting {VERIFY_WAIT_MIN}min to verify bot started...")
    time.sleep(VERIFY_WAIT_MIN * 60)

    new_age = get_log_age_min()
    wlog(f"Post-R3c log_age={new_age:.1f}min (expected <{VERIFY_WAIT_MIN+5}min)")

    if new_age < VERIFY_WAIT_MIN + 5:
        wlog("RECOVERED: log updated after R3c")
        slack_alarm(f"✅ [FOLLOW watchdog] R3c成功 log_age={new_age:.0f}min → 正常稼働復帰")
    else:
        wlog("ALARM: log still stale after R3c - HUMAN INTERVENTION REQUIRED")
        slack_alarm(
            f"🚨 [FOLLOW watchdog] R3c後も log stale ({new_age:.0f}min)\n"
            f"VM画面の手動確認が必要です。\n"
            f"- VirtualBox Manager で RoomBot を確認\n"
            f"- Windowsログイン状態・Chrome表示状態を確認\n"
            f"- 手動で follow_rpa_v1.py を起動してください"
        )


if __name__ == "__main__":
    main()
