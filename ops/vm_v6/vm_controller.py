#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HOST 側 VM Controller — VM 内 HTTP server に webhook 命令を送る.

Plan v4 P1: HOST から VM bot を webhook 経由で制御する単一エントリ。
旧 ops/vm_follow_launcher.py / orchestrator_v5 dispatch を置換。

使い方:
    python ops/vm_v6/vm_controller.py --mode post --limit 50 --batch 1
    python ops/vm_v6/vm_controller.py --mode like --limit 100
    python ops/vm_v6/vm_controller.py --mode follow --limit 200 --force
    python ops/vm_v6/vm_controller.py --mode followback --limit 30
    python ops/vm_v6/vm_controller.py --status
    python ops/vm_v6/vm_controller.py --abort post
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("[ERROR] requests 未インストール: pip install requests")
    sys.exit(1)


# 設定
VM_HOST = os.environ.get("VM_HTTP_HOST", "localhost")
VM_PORT = int(os.environ.get("VM_HTTP_PORT", "18765"))  # HOST → VM forward port
API_TOKEN = os.environ.get("BOT_API_TOKEN", "rakuten-room-v6-secret")
BASE_URL = f"http://{VM_HOST}:{VM_PORT}"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def is_alive(timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f"{BASE_URL}/healthz", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def get_status() -> dict:
    try:
        r = requests.get(f"{BASE_URL}/status", headers=HEADERS, timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def run(mode: str, limit: int = 100, batch: int = 1, force: bool = False) -> dict:
    payload = {"mode": mode, "limit": limit}
    if mode == "post":
        payload["batch"] = batch
    if force:
        payload["force"] = True
    try:
        r = requests.post(f"{BASE_URL}/run", json=payload, headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def abort(mode: str = None, all: bool = False) -> dict:
    payload = {}
    if mode: payload["mode"] = mode
    if all: payload["all"] = True
    try:
        r = requests.post(f"{BASE_URL}/abort", json=payload, headers=HEADERS, timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def get_heartbeat(mode: str) -> dict:
    try:
        r = requests.get(f"{BASE_URL}/heartbeat/{mode}", headers=HEADERS, timeout=3)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["post", "like", "follow", "followback"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--abort", metavar="MODE", help="abort specific mode")
    parser.add_argument("--abort-all", action="store_true")
    parser.add_argument("--heartbeat", metavar="MODE", help="show heartbeat for mode")
    args = parser.parse_args()

    # まず疎通確認
    if not is_alive():
        print("[ERROR] VM HTTP server unreachable. Is VM running and server started?")
        print(f"  URL: {BASE_URL}")
        print(f"  Recovery: VBoxManage startvm RoomBot, then start http_server.py in VM")
        return 2

    if args.status:
        print(json.dumps(get_status(), ensure_ascii=False, indent=2))
        return 0

    if args.abort:
        print(json.dumps(abort(mode=args.abort), ensure_ascii=False))
        return 0

    if args.abort_all:
        print(json.dumps(abort(all=True), ensure_ascii=False))
        return 0

    if args.heartbeat:
        print(json.dumps(get_heartbeat(args.heartbeat), ensure_ascii=False, indent=2))
        return 0

    if args.mode:
        # 2026-05-27 SSOT pacing: target 超過防止
        # 旧 orchestrator_v5 の effective_limit ロジックを vm_controller でも適用.
        # POST batch3 で 20/19 overshoot した問題対応.
        effective_limit = args.limit
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from shared.daily_pacer import get_pace_directive
            fn_map = {"post": "POST", "like": "LIKE",
                      "follow": "FOLLOW", "followback": "FB"}
            fn_key = fn_map.get(args.mode)
            if fn_key:
                d = get_pace_directive(fn_key)
                if d.get("action") == "stop":
                    print(json.dumps({
                        "status": "skipped",
                        "reason": "daily_target_reached",
                        "actual": d.get("actual"),
                        "target": d.get("target"),
                    }, ensure_ascii=False))
                    return 0
                remaining = d.get("remain_target", 0)
                if remaining > 0 and remaining < args.limit:
                    effective_limit = remaining
                    print(json.dumps({
                        "pacing": f"limit {args.limit} → {effective_limit} (remaining)",
                        "actual": d.get("actual"),
                        "target": d.get("target"),
                    }, ensure_ascii=False))
        except Exception as _pe:
            print(f"[pacing] fallback (no SSOT cap): {_pe}", file=sys.stderr)
        result = run(args.mode, limit=effective_limit, batch=args.batch, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if "error" not in result else 4

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
