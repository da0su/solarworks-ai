#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 FOLLOWBACK executor: 既存 followback_executor を thin wrap."""
from __future__ import annotations

import sys
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger
from .browser_manager_v6 import BrowserManagerV6

HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
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
            from executor.followback_executor import FollowbackExecutor
            class CompatBM:
                def __init__(self, ctx, page):
                    self._context = ctx; self._page = page
                @property
                def page(self): return self._page
                def take_screenshot(self, label): return None
                def stop(self): pass

            compat = CompatBM(bm.context, bm.page)
            fbe = FollowbackExecutor(compat)
            summary = fbe.execute(limit=limit) if hasattr(fbe, 'execute') else fbe.run(limit=limit)
            result["success"] = summary.get("success", summary.get("followed", 0))
            result["fail"] = summary.get("fail", 0)
            result["stop_reason"] = summary.get("stop_reason", "completed")
            log.log(f"followback_executor result: {result}")
        except Exception as e:
            log.log(f"[ERROR] followback_executor: {e}")
            result["stop_reason"] = f"executor_error: {e}"
    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        bm.stop()
        log.log(f"=== FOLLOWBACK executor v6 end: {result} ===")

    return result
