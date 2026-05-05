#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM 内 HTTP server (FastAPI) — HOST からの webhook 受信.

Plan v4 P1: VM 内で起動し、HOST からの run/abort/status 命令を受け付ける。
port 8765 (VM Guest) → port 18765 (HOST forward).

VM 内起動:
    cd C:\\Users\\cyber\\Desktop\\rakuten_room_bot\\server
    python http_server.py

または autorun で systemd-like Windows サービス化。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Header
    import uvicorn
except ImportError:
    print("[ERROR] fastapi / uvicorn が未インストール。VM 内で:")
    print("  pip install fastapi uvicorn")
    sys.exit(1)


# Auth token (env var で設定)
API_TOKEN = os.environ.get("BOT_API_TOKEN", "rakuten-room-v6-secret")

# 現在 mode が走っているかの簡易状態
RUNNING_MODES: dict[str, dict] = {}  # mode -> {pid, started_at}

# Path
RUNNER_PATH = Path(__file__).resolve().parent.parent / "runner" / "rakuten_room_runner.py"
HEARTBEAT_DIR = Path(__file__).resolve().parent.parent / "data"
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(title="rakuten_room_bot v6")


def check_auth(authorization: str = Header(None)):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/")
async def root():
    return {"status": "ok", "service": "rakuten_room_bot_v6"}


@app.get("/status")
async def status(authorization: str = Header(None)):
    check_auth(authorization)
    out = {"running": list(RUNNING_MODES.keys()), "heartbeats": {}}
    for mode in ["post", "like", "follow", "followback"]:
        hb_path = HEARTBEAT_DIR / f"heartbeat_{mode}.json"
        if hb_path.exists():
            try:
                hb = json.loads(hb_path.read_text(encoding="utf-8"))
                age = (datetime.now() - datetime.fromisoformat(hb["ts"])).total_seconds()
                out["heartbeats"][mode] = {**hb, "age_sec": int(age)}
            except Exception:
                out["heartbeats"][mode] = None
    return out


@app.post("/run")
async def run(payload: dict, authorization: str = Header(None)):
    check_auth(authorization)
    mode = payload.get("mode")
    if mode not in ["post", "like", "follow", "followback"]:
        raise HTTPException(status_code=400, detail="invalid mode")
    if mode in RUNNING_MODES:
        return {"status": "already_running", "pid": RUNNING_MODES[mode]["pid"]}

    args = [sys.executable, str(RUNNER_PATH), "--mode", mode,
            "--limit", str(payload.get("limit", 100))]
    if mode == "post":
        args += ["--batch", str(payload.get("batch", 1))]
    if payload.get("force"):
        args += ["--force"]

    NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.Popen(args, creationflags=NO_WIN)
    RUNNING_MODES[mode] = {"pid": proc.pid, "started_at": datetime.now().isoformat()}
    return {"status": "launched", "mode": mode, "pid": proc.pid}


@app.post("/abort")
async def abort(payload: dict, authorization: str = Header(None)):
    check_auth(authorization)
    mode = payload.get("mode")
    if mode and mode in RUNNING_MODES:
        try:
            import psutil
            p = psutil.Process(RUNNING_MODES[mode]["pid"])
            p.terminate()
            del RUNNING_MODES[mode]
            return {"status": "aborted", "mode": mode}
        except Exception as e:
            return {"status": "abort_failed", "error": str(e)}
    if payload.get("all"):
        # stop_flag を share folder に作成 (graceful)
        flag = Path(r"\\VBOXSVR\share\stop_flag_share.json")
        try:
            flag.write_text(json.dumps({"created_at": datetime.now().isoformat()}))
            return {"status": "stop_flag_created"}
        except Exception as e:
            return {"status": "abort_failed", "error": str(e)}
    return {"status": "no_op"}


@app.get("/heartbeat/{mode}")
async def heartbeat(mode: str, authorization: str = Header(None)):
    check_auth(authorization)
    hb_path = HEARTBEAT_DIR / f"heartbeat_{mode}.json"
    if not hb_path.exists():
        return {"status": "no_heartbeat"}
    return json.loads(hb_path.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz():
    """auth 不要のヘルスチェック."""
    return {"status": "ok"}


if __name__ == "__main__":
    print(f"[http_server] starting on port 8765, runner={RUNNER_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
