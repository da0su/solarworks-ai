#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 LIKE executor: 既存 like_executor を thin wrap."""
from __future__ import annotations

import sys
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger, emergency_disk_cleanup_once
from .browser_manager_v6 import BrowserManagerV6

# 2026-05-26: VM disk full → Chrome EPIPE 防止. import 時に1回 cleanup.
try:
    emergency_disk_cleanup_once()
except Exception as _e:
    print(f"[disk_cleanup_like] err: {_e}")

# 2026-05-24: VM では runner が \\vboxsvr\vm_v6\runner にあり parents[3] が無い
# rakuten_room_runner.py で sys.path に vm_bot を追加済なので、ここで追加不要
try:
    HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
    if HOST_BOT_DIR.exists():
        sys.path.insert(0, str(HOST_BOT_DIR))
except (IndexError, ValueError):
    pass


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
            # 2026-05-24 fix: LikeExecutor は外部 BrowserManager 受けるよう修正済
            # bm を直接渡す (CompatBM は check_login_status, page, take_screenshot, stop が必要)
            class CompatBM:
                def __init__(self, ctx, page, action):
                    self._context = ctx
                    self._page = page
                    self._action = action
                @property
                def page(self):
                    return self._page
                def check_login_status(self) -> dict:
                    # VM では既に bm.is_logged_in() で確認済 → OK 返す
                    return {"logged_in": True, "method": "vm_v6_pre_checked",
                            "url": self._page.url, "title": "", "screenshot": ""}
                def take_screenshot(self, label):
                    try:
                        # 2026-05-29: 明示的 timeout (default 30s → 10s)。
                        # extra_scroll hang 防止。
                        return self._page.screenshot(timeout=10000)
                    except Exception:
                        return None
                def stop(self): pass

            compat = CompatBM(bm.context, bm.page, "like")
            le = LikeExecutor(limit=limit)
            summary = le.run(bm=compat)
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
