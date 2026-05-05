#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 LIKE executor: 既存 like_executor を thin wrap."""
from __future__ import annotations

import sys
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger
from .browser_manager_v6 import BrowserManagerV6

HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
sys.path.insert(0, str(HOST_BOT_DIR))


def run_like(limit: int = 100, hb: HeartbeatPusher = None, log: SessionLogger = None) -> dict:
    if hb is None: hb = HeartbeatPusher("like")
    if log is None: log = SessionLogger("like")

    log.log(f"=== LIKE executor v6 start: limit={limit} ===")
    hb.write(phase="startup", force=True)

    bm = BrowserManagerV6(action="like")
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}

    try:
        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            result["stop_reason"] = "login_expired"
            return result

        try:
            from executor.like_executor import LikeExecutor
            class CompatBM:
                def __init__(self, ctx, page):
                    self._context = ctx; self._page = page
                @property
                def page(self): return self._page
                def take_screenshot(self, label): return None
                def stop(self): pass

            compat = CompatBM(bm.context, bm.page)
            le = LikeExecutor(compat, limit=limit)
            summary = le.run()
            result["success"] = summary.get("liked", 0)
            result["fail"] = summary.get("failed", 0)
            result["skip"] = summary.get("skipped", 0)
            result["stop_reason"] = summary.get("abort_reason", "completed") or "completed"
            log.log(f"like_executor result: {result}")
        except Exception as e:
            log.log(f"[ERROR] like_executor: {e}")
            result["stop_reason"] = f"executor_error: {e}"
    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        bm.stop()
        log.log(f"=== LIKE executor v6 end: {result} ===")

    return result
