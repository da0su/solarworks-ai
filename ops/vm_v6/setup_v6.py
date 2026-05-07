#!/usr/bin/env python3
"""VM v6 setup - Python ONE-FILE script (bat 廃止版).

VM 内 cmd で:
    python \\vboxsvr\vm_v6\setup_v6.py

内部で全 step を subprocess で順次実行。bat の文字列処理問題を回避。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.environ["USERPROFILE"]) / "Desktop" / "rakuten_room_bot"
VM_V6_SHARE = Path(r"\\vboxsvr\vm_v6")
VM_DATA_SHARE = Path(r"\\vboxsvr\vm_data")
MARKER = VM_V6_SHARE / ".setup_done"
NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kwargs):
    """subprocess wrapper. Returns rc."""
    log(f"$ {cmd if isinstance(cmd, str) else ' '.join(map(str, cmd))[:120]}")
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), **kwargs)
        return r.returncode
    except Exception as e:
        log(f"  EXCEPTION: {e}")
        return 99


def step1_mkdir():
    log("[STEP1] mkdir")
    for sub in ["", "runner", "server", "data", "logs", "credentials"]:
        d = BASE / sub if sub else BASE
        d.mkdir(parents=True, exist_ok=True)
    log(f"  BASE = {BASE}")


def step2_copy_code():
    log("[STEP2] copy code from vm_v6 share")
    import shutil
    for sub in ["runner", "server"]:
        src = VM_V6_SHARE / sub
        dst = BASE / sub
        dst.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            log(f"  WARN: {src} not found")
            continue
        for f in src.glob("*.py"):
            shutil.copy2(f, dst / f.name)
            log(f"  copied {f.name}")


def step3_pip():
    log("[STEP3] pip install")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], creationflags=NO_WIN)
    rc = run([sys.executable, "-m", "pip", "install",
              "playwright", "fastapi", "uvicorn", "requests", "gspread", "psutil"],
             creationflags=NO_WIN)
    log(f"  pip rc={rc}")
    return rc == 0


def step4_playwright():
    log("[STEP4] Playwright Chromium install (5-10min)")
    rc = run([sys.executable, "-m", "playwright", "install", "chromium"], creationflags=NO_WIN)
    log(f"  playwright install rc={rc}")
    return rc == 0


def step5_profile_copy():
    log("[STEP5] copy 4 chrome profiles (10-20min)")
    for action in ["post", "like", "followback", "follow"]:
        src = VM_DATA_SHARE / f"chrome_profile_{action}"
        dst = BASE / "data" / f"chrome_profile_{action}"
        if not src.exists():
            log(f"  WARN: {src} not found, skipping")
            continue
        log(f"  robocopy chrome_profile_{action} ...")
        rc = run(["robocopy", str(src), str(dst), "/E",
                  "/XF", "SingletonLock", "SingletonSocket", "SingletonCookie",
                  "/R:3", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS"],
                 creationflags=NO_WIN)
        # robocopy rc 0-7 = success
        log(f"    rc={rc} {'(OK)' if rc < 8 else '(ERROR)'}")


def step5b_env_vm():
    """.env_vm の存在確認 + template からコピー (CEO 編集用)."""
    log("[STEP5b] credential file (.env_vm)")
    env_vm = BASE / "data" / ".env_vm"
    template = VM_DATA_SHARE / ".env_vm.template"
    if env_vm.exists():
        log(f"  OK: {env_vm} 既に存在")
        # password 設定済みか軽く確認
        try:
            content = env_vm.read_text(encoding="utf-8")
            has_pw = any(
                line.strip().startswith("RAKUTEN_LOGIN_PASSWORD=") and len(line.split("=", 1)[1].strip()) > 0
                for line in content.splitlines()
            )
            if has_pw:
                log("  RAKUTEN_LOGIN_PASSWORD 設定済 (POST 自動通過 OK)")
            else:
                log("  WARN: RAKUTEN_LOGIN_PASSWORD が空 → POST batch は session/upgrade で停止します")
        except Exception:
            pass
        return
    # template が share にあれば copy
    if template.exists():
        import shutil
        shutil.copy2(template, env_vm)
        log(f"  template → {env_vm} にコピー")
        log("  >>> CEO action: このファイルを編集して RAKUTEN_LOGIN_PASSWORD=... を設定してください")
    else:
        # 最低限の空ファイル作成
        env_vm.write_text(
            "# VM v6 credential file (Plan v6)\n"
            "RAKUTEN_LOGIN_PASSWORD=\n",
            encoding="utf-8",
        )
        log(f"  empty {env_vm} 作成")


def step6_register_startup():
    log("[STEP6] register startup")
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_bat = startup_dir / "rakuten_room_bot_v6_server.bat"
    content = (
        "@echo off\r\n"
        f'cd /d "{BASE}\\server"\r\n'
        f'start "" /B python http_server.py\r\n'
    )
    startup_bat.write_text(content, encoding="ascii")
    log(f"  registered: {startup_bat}")


def step7_start_server():
    log("[STEP7] start HTTP server")
    server_py = BASE / "server" / "http_server.py"
    if not server_py.exists():
        log(f"  ERROR: {server_py} not found")
        return False
    # detached + no window で起動
    DETACHED = 0x00000008
    NEW_GROUP = 0x00000200
    subprocess.Popen([sys.executable, str(server_py)],
                     cwd=str(server_py.parent),
                     creationflags=DETACHED | NEW_GROUP | NO_WIN)
    log("  HTTP server launched (detached)")


def main():
    log("=" * 60)
    log("VM v6 setup START (Python ONE-FILE)")
    log("=" * 60)

    try:
        step1_mkdir()
        step2_copy_code()
        if not step3_pip():
            log("[ABORT] pip install failed")
            return 3
        if not step4_playwright():
            log("[ABORT] playwright install failed")
            return 4
        step5_profile_copy()
        step5b_env_vm()
        step6_register_startup()
        step7_start_server()

        # 完了 marker (vm_v6 share 経由で HOST に通知)
        try:
            MARKER.write_text("done", encoding="ascii")
            log(f"  marker written: {MARKER}")
        except Exception as e:
            log(f"  marker write failed: {e}")

        log("=" * 60)
        log("SETUP DONE - HTTP server on port 8765")
        log("=" * 60)
        return 0

    except KeyboardInterrupt:
        log("INTERRUPTED by user")
        return 130
    except Exception as e:
        log(f"FATAL: {e}")
        import traceback
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
