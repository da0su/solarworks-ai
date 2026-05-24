"""HOST から VM 内 task を trigger する単一エントリ (CEO 5/22「自立できないと一生自立できない」対応).

【背景】 VM HTTP server (port 18765) が不通になった時の代替経路.
Plan v6 設計で VBoxManage guestproperty + Guest Additions の watcher を活用.

【経路 (優先順)】
1. **HTTP**: VM HTTP server (port 18765) → POST /run
2. **GuestProperty**: VBoxManage guestproperty set RoomBot /RakutenBot/Trigger <value>
   - VM 内 VBoxControl guestproperty wait /RakutenBot/Trigger で pickup
   - VM 内 watcher が動いていれば host から完全制御可能
3. **記録のみ**: state/host_trigger_queue.jsonl に追記 (VM 内 watcher が起動すれば消化)

【使い方】
    python ops/vm_v6/host_trigger.py --mode comment_edit
    python ops/vm_v6/host_trigger.py --mode post --batch 1 --limit 50
    python ops/vm_v6/host_trigger.py --status     # 全経路 status

【exit code】
- 0: HTTP or GuestProperty で trigger 成功
- 4: 全経路 fail (CEO に手動 trigger 依頼)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "RoomBot"
NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
TRIGGER_QUEUE = REPO_ROOT / "state" / "host_trigger_queue.jsonl"
TRIGGER_GUESTPROP = "/RakutenBot/Trigger"

# Git Bash 経由実行時 MSYS2 が `/path` を Windows path に変換してしまうのを抑止.
# Python subprocess は通常 MSYS 介さないが、安全のため明示 disable.
os.environ.setdefault("MSYS_NO_PATHCONV", "1")
os.environ.setdefault("MSYS2_ARG_CONV_EXCL", "*")


def _http_trigger(mode: str, payload: dict) -> tuple[bool, dict]:
    """HTTP 経由で trigger."""
    try:
        import requests
        api_token = os.environ.get("BOT_API_TOKEN", "rakuten-room-v6-secret")
        port = int(os.environ.get("VM_HTTP_PORT", "18765"))
        url = f"http://localhost:{port}/run"
        r = requests.post(url, json={**payload, "mode": mode},
                          headers={"Authorization": f"Bearer {api_token}"},
                          timeout=5)
        return r.status_code == 200, {"status_code": r.status_code,
                                       "body": (r.text or "")[:200]}
    except Exception as e:
        return False, {"error": str(e)[:200]}


def _guestproperty_trigger(mode: str, payload: dict) -> tuple[bool, dict]:
    """VBoxManage guestproperty set で VM 内 watcher に pulse 送信."""
    try:
        value = json.dumps({
            "mode": mode,
            "payload": payload,
            "issued_at": datetime.now().isoformat(timespec="seconds"),
            "trigger_id": f"{mode}_{int(time.time())}",
        }, ensure_ascii=False)
        r = subprocess.run(
            [VBOXMANAGE, "guestproperty", "set", VM_NAME, TRIGGER_GUESTPROP, value],
            capture_output=True, text=True, timeout=15, creationflags=NO_WIN,
            encoding="utf-8", errors="replace",
        )
        return r.returncode == 0, {
            "rc": r.returncode,
            "value_len": len(value),
            "stderr": (r.stderr or "")[:200],
        }
    except Exception as e:
        return False, {"error": str(e)[:200]}


def _queue_record(mode: str, payload: dict, paths: dict) -> None:
    """trigger 履歴を JSONL に追記 (VM 内 watcher 起動後に消化対象)."""
    try:
        TRIGGER_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with TRIGGER_QUEUE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": mode,
                "payload": payload,
                "paths": paths,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def trigger(mode: str, **kwargs) -> dict:
    """すべての経路を試行して結果を返す."""
    payload = {k: v for k, v in kwargs.items() if v is not None}
    paths: dict = {}

    # 1. HTTP
    http_ok, http_info = _http_trigger(mode, payload)
    paths["http"] = {"ok": http_ok, **http_info}
    if http_ok:
        _queue_record(mode, payload, paths)
        return {"ok": True, "path": "http", "paths": paths}

    # 2. GuestProperty (VM 内 watcher 起動済なら pickup)
    gp_ok, gp_info = _guestproperty_trigger(mode, payload)
    paths["guestproperty"] = {"ok": gp_ok, **gp_info}
    if gp_ok:
        _queue_record(mode, payload, paths)
        return {"ok": True, "path": "guestproperty",
                "note": "VM 内 watcher が起動していれば pickup. 起動していない場合は queue に積むだけ.",
                "paths": paths}

    # 3. queue 記録のみ
    _queue_record(mode, payload, paths)
    return {"ok": False, "paths": paths,
            "queue_recorded": True,
            "ceo_action_needed": (
                "VM コンソールで watcher 起動が必要. "
                "詳細: ops/vm_v6/trigger_comment_edit.bat を VM 内 dbclick."
            )}


def status() -> dict:
    """全経路 status."""
    # HTTP
    http_ok, http_info = False, {}
    try:
        import requests
        port = int(os.environ.get("VM_HTTP_PORT", "18765"))
        r = requests.get(f"http://localhost:{port}/healthz", timeout=3)
        http_ok = r.status_code == 200
        http_info = {"status_code": r.status_code, "body": (r.text or "")[:100]}
    except Exception as e:
        http_info = {"error": str(e)[:200]}

    # GuestProperty 最終値
    gp_value = None
    try:
        r = subprocess.run(
            [VBOXMANAGE, "guestproperty", "get", VM_NAME, TRIGGER_GUESTPROP],
            capture_output=True, text=True, timeout=10, creationflags=NO_WIN,
            encoding="utf-8", errors="replace",
        )
        gp_value = (r.stdout or "").strip()
    except Exception as e:
        gp_value = f"error: {e}"

    # VM state
    vm_state = None
    try:
        r = subprocess.run(
            [VBOXMANAGE, "showvminfo", VM_NAME, "--machinereadable"],
            capture_output=True, text=True, timeout=10, creationflags=NO_WIN,
            encoding="utf-8", errors="replace",
        )
        for line in r.stdout.splitlines():
            if line.startswith("VMState="):
                vm_state = line.split("=", 1)[1].strip('"')
                break
    except Exception:
        pass

    # queue 件数
    queue_size = 0
    if TRIGGER_QUEUE.exists():
        try:
            queue_size = sum(1 for _ in TRIGGER_QUEUE.open("r", encoding="utf-8"))
        except Exception:
            pass

    return {
        "vm_state": vm_state,
        "http": {"ok": http_ok, **http_info},
        "guestproperty_last": gp_value,
        "queue_size": queue_size,
        "queue_path": str(TRIGGER_QUEUE),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode",
                    choices=["post", "like", "follow", "followback",
                             "comment_edit", "bootstrap", "http_server"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=None)
    ap.add_argument("--force", action="store_true", default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0

    if not args.mode:
        ap.print_help()
        return 2

    payload = {}
    if args.limit:
        payload["limit"] = args.limit
    if args.batch:
        payload["batch"] = args.batch
    if args.force:
        payload["force"] = True

    out = trigger(args.mode, **payload)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") else 4


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
