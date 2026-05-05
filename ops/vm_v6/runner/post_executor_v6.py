#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 POST executor: queue_executor を Playwright で wrap."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from .shared_logic import HeartbeatPusher, RateLimitDetector, SessionLogger, BASE_DIR
from .browser_manager_v6 import BrowserManagerV6


# 既存 queue_executor.py を利用するため path 追加
HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
sys.path.insert(0, str(HOST_BOT_DIR))


def run_post(limit: int = 50, batch: int = 1, hb: HeartbeatPusher = None, log: SessionLogger = None) -> dict:
    """POST 実行. Plan v4 P1: chrome_profile_post で稼働."""
    if hb is None:
        hb = HeartbeatPusher("post")
    if log is None:
        log = SessionLogger("post")

    log.log(f"=== POST executor v6 start: limit={limit} batch={batch} ===")
    hb.write(phase="startup", force=True)

    bm = BrowserManagerV6(action="post")
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}

    try:
        bm.start()
        hb.write(phase="login_check")

        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            result["stop_reason"] = "login_expired"
            return result

        # 既存 queue_executor を呼ぶ (BrowserManager 互換 wrapper)
        try:
            from planner.queue_executor import QueueExecutor
            log.log("queue_executor imported")

            # QueueExecutor は BrowserManager (旧) を期待。bm._ctx, bm._page を再利用
            class CompatBM:
                def __init__(self, ctx, page):
                    self._context = ctx
                    self._page = page
                @property
                def page(self): return self._page
                def take_screenshot(self, label): return None
                def save_session(self): pass
                def stop(self): pass

            compat = CompatBM(bm.context, bm.page)
            qe = QueueExecutor(compat, target_count=limit)
            summary = qe.execute()

            result["success"] = summary.get("posted", 0)
            result["fail"] = summary.get("failed", 0)
            result["skip"] = summary.get("skipped", 0)
            result["stop_reason"] = summary.get("stop_reason", "completed")
            log.log(f"queue_executor result: {result}")
        except Exception as e:
            log.log(f"[ERROR] queue_executor: {e}")
            result["stop_reason"] = f"executor_error: {e}"

    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], skip=result["skip"], force=True)
        bm.stop()
        log.log(f"=== POST executor v6 end: {result} ===")

    return result
