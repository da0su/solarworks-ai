"""5/11 FOLLOW audit 専用 day mode の start / end 切替.

CEO 5/10 21:00 指示: 5/11 限定で POST/LIKE/FB を 0 にし、
FOLLOW 上限値監査 + 最高値達成のみを目指す。5/12 0:00 通常復帰。

usage:
    python follow_audit_mode_toggle.py start   # 5/11 00:00 に呼ぶ
    python follow_audit_mode_toggle.py end     # 5/12 00:00 に呼ぶ
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BAT = REPO_ROOT / "ops" / "host_follow_launcher.bat"

OFF_TASKS = [
    "RoomBot_POST_Batch1", "RoomBot_POST_Batch2",
    "RoomBot_POST_Batch3", "RoomBot_POST_Batch4",
    "RoomBot_LIKE_Hourly", "RoomBot_FOLLOWBACK_Hourly",
]


def disable_task(name: str):
    print(f"  disable: {name}")
    subprocess.run(["schtasks", "/Change", "/TN", name, "/DISABLE"],
                   capture_output=True, text=True)


def enable_task(name: str):
    print(f"  enable:  {name}")
    subprocess.run(["schtasks", "/Change", "/TN", name, "/ENABLE"],
                   capture_output=True, text=True)


def patch_bat_target(target: int, duration_min: int):
    """host_follow_launcher.bat の --target / --duration-min を書換える."""
    if not BAT.exists():
        print(f"[ERR] bat not found: {BAT}")
        return
    text = BAT.read_text(encoding="utf-8")
    import re
    new_line = f"python rakuten-room\\bot\\scripts\\follow_via_seeds.py --target {target} --duration-min {duration_min} >> \"C:\\Users\\infoa\\Documents\\solarworks-ai\\ops\\scheduler\\logs\\windows_task_follow_host.log\" 2>&1"
    text = re.sub(r'python rakuten-room\\bot\\scripts\\follow_via_seeds\.py --target \d+ --duration-min \d+ >> "[^"]+" 2>&1', new_line, text)
    BAT.write_text(text, encoding="utf-8")
    print(f"  bat patched: target={target} duration={duration_min}")


def main():
    if len(sys.argv) < 2:
        print("usage: follow_audit_mode_toggle.py {start|end}")
        return 1
    mode = sys.argv[1].lower()

    if mode == "start":
        print("[FOLLOW AUDIT MODE START - 5/11]")
        for t in OFF_TASKS:
            disable_task(t)
        patch_bat_target(target=200, duration_min=14)
        print("[OK] POST/LIKE/FB disabled. FOLLOW target=200 boosted.")
    elif mode == "end":
        print("[FOLLOW AUDIT MODE END - 5/12]")
        for t in OFF_TASKS:
            enable_task(t)
        patch_bat_target(target=30, duration_min=14)
        print("[OK] POST/LIKE/FB re-enabled. FOLLOW target=30 reverted.")
    else:
        print(f"unknown mode: {mode}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
