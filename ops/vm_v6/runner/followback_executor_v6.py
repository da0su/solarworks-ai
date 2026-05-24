#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 FOLLOWBACK executor: 既存 followback_executor を thin wrap."""
from __future__ import annotations

import sys
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger
from .browser_manager_v6 import BrowserManagerV6

# 2026-05-24: VM では UNC path 経由 (parents[3] 無)
try:
    HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
    if not HOST_BOT_DIR.exists():
        raise FileNotFoundError(HOST_BOT_DIR)
except (IndexError, FileNotFoundError, ValueError):
    HOST_BOT_DIR = Path(r"\\vboxsvr\bot")
if HOST_BOT_DIR.exists():
    sys.path.insert(0, str(HOST_BOT_DIR))


def run_followback(limit: int = 30, hb: HeartbeatPusher = None, log: SessionLogger = None) -> dict:
    if hb is None: hb = HeartbeatPusher("followback")
    if log is None: log = SessionLogger("followback")

    log.log(f"=== FOLLOWBACK executor v6 start: limit={limit} ===")
    hb.write(phase="startup", force=True)

    bm = BrowserManagerV6(action="followback")
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}

    try:
        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            result["stop_reason"] = "login_expired"
            return result

        try:
            # 2026-05-24: followback_executor は module-level execute() 関数
            # (class FollowbackExecutor は存在しない)
            from executor import followback_executor as fbe_mod
            summary = fbe_mod.execute(limit=limit, include_unfollowed=False, dry_run=False)
            result["success"] = summary.get("success", summary.get("followed", 0))
            result["fail"] = summary.get("fail", summary.get("processed", 0) - summary.get("success", 0))
            result["stop_reason"] = summary.get("stop_reason", summary.get("status", "completed"))
            log.log(f"followback_executor result: {result}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.log(f"[ERROR] followback_executor: {e}\n{tb}")
            result["stop_reason"] = f"executor_error: {type(e).__name__}: {e}"
    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        bm.stop()
        log.log(f"=== FOLLOWBACK executor v6 end: {result} ===")

    return result
