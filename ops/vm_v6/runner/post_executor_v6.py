#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 POST executor: queue_executor を Playwright で wrap."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .shared_logic import HeartbeatPusher, RateLimitDetector, SessionLogger, BASE_DIR
from .browser_manager_v6 import BrowserManagerV6


# 既存 queue_executor.py を利用するため path 追加
# 2026-05-24: VM では UNC path 経由 (parents[3] 無)
try:
    HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
    if not HOST_BOT_DIR.exists():
        raise FileNotFoundError(HOST_BOT_DIR)
except (IndexError, FileNotFoundError, ValueError):
    HOST_BOT_DIR = Path(r"\\vboxsvr\bot")
if HOST_BOT_DIR.exists():
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

            # QueueExecutor は BrowserManager (旧) を期待。BrowserManagerV6 を wrap してメソッド forward する。
            # 2026-05-07 Plan v6 Phase A-2: handle_session_upgrade を bm に forward することで
            # HOST post_executor.py の session/upgrade 検知 → 自動通過 が VM 側でも動作する。
            class CompatBM:
                def __init__(self, v6_bm: BrowserManagerV6):
                    self._v6 = v6_bm
                    self._context = v6_bm.context
                    self._page = v6_bm.page
                @property
                def page(self): return self._page
                def check_login_status(self) -> dict:
                    # VM では既に bm.is_logged_in() で確認済 → OK 返す
                    return {"logged_in": True, "method": "vm_v6_pre_checked",
                            "url": self._page.url, "title": "", "screenshot": ""}
                def start(self):
                    pass  # already started by external bm
                def take_screenshot(self, label):
                    try:
                        screenshot_dir = BASE_DIR / "screenshots" / datetime.now().strftime("%Y-%m-%d")
                        screenshot_dir.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now().strftime("%H%M%S")
                        path = screenshot_dir / f"{ts}_{label}.png"
                        self._page.screenshot(path=str(path), full_page=False)
                        return path
                    except Exception:
                        return None
                def save_session(self): pass
                def stop(self): pass
                # Plan v6 Phase A-2: HOST post_executor.py が呼ぶ handle_session_upgrade を v6 に forward
                def handle_session_upgrade(self, max_wait_sec: int = 15) -> dict:
                    return self._v6.handle_session_upgrade(max_wait_sec=max_wait_sec)

            compat = CompatBM(bm)
            # 2026-05-25 fix (Codex APPROVE 版):
            #   核心: 今日日付を明示的に渡すことで parked items (2099-12-31) を除外
            #   - zoneinfo で Asia/Tokyo 固定日付 (OS TZ依存排除)
            #   - 0件時は QueueExecutor がそのまま stop_reason="completed" を返す (既存 whitelist 互換)
            #   - DB アクセス不要: queue_date を決定するだけで QueueExecutor に委ねる
            from datetime import datetime as _dt
            try:
                from zoneinfo import ZoneInfo as _ZI
                _today_str = _dt.now(_ZI("Asia/Tokyo")).date().isoformat()
            except ImportError:  # Python 3.8 以下フォールバック (VM=Windows JST 固定)
                _today_str = _dt.now().strftime("%Y-%m-%d")
            log.log(f"queue_date (today, Asia/Tokyo): {_today_str}")

            # 今日日付を明示渡し → QueueExecutor が 0件なら posted=0/stop_reason="completed" を返す
            # (DB事前チェックは不要: false-success防止はQueueExecutorのロジックに委ねる)
            qe = QueueExecutor(queue_date=_today_str, limit=limit)
            qe._external_bm = compat  # may be unused; queue_executor opens own bm
            summary = qe.run()

            result["success"] = summary.get("posted", 0)
            result["fail"] = summary.get("failed", 0)
            result["skip"] = summary.get("skipped", 0)
            # QueueExecutor は "reason" キーを使用 ("stop_reason" ではない)
            # aborted=True + reason あり → reason をそのまま使用
            # aborted=False (正常完了) → "completed"
            _aborted = summary.get("aborted", False)
            _reason = summary.get("reason")
            result["stop_reason"] = (_reason if _reason else "aborted") if _aborted else "completed"
            log.log(f"queue_executor result: {result}")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.log(f"[ERROR] queue_executor: {e}\n{tb}")
            result["stop_reason"] = f"executor_error: {type(e).__name__}: {e}"

    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], skip=result["skip"], force=True)
        bm.stop()
        log.log(f"=== POST executor v6 end: {result} ===")

    return result
