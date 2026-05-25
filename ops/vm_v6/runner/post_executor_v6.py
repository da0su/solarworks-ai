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
            # 2026-05-24 fix: QueueExecutor は (queue_date, limit, ...) 仕様
            # 2026-05-25 fix v3 (Codex REVIEW_NEEDED/REJECT 対応):
            #   - today 完全一致 COUNT(*) で queued 件数を事前確認
            #   - queued=0 なら QueueExecutor を呼ばず即 early-return (NO-OP)
            #     → 虚偽 success 報告リスクを完全排除
            #   - DB 接続は with context で確実 close
            import sqlite3 as _sqlite3
            from datetime import datetime as _dt
            # システム localtime を使用 (VM=Windows JST 固定のため pytz 不要)
            _today_str = _dt.now().strftime("%Y-%m-%d")
            _db = HOST_BOT_DIR / "data" / "room_bot.db"
            _today_cnt = 0
            _db_err = None
            try:
                with _sqlite3.connect(str(_db), timeout=5, isolation_level=None) as _con:
                    _row = _con.execute(
                        "SELECT COUNT(*) FROM post_queue WHERE status='queued' AND queue_date=?",
                        (_today_str,)
                    ).fetchone()
                    _today_cnt = _row[0] if _row else 0
                log.log(f"queue_date={_today_str}: {_today_cnt} items queued")
            except Exception as _qd_err:
                _db_err = _qd_err
                log.log(f"[ERROR] queue_date detect: {_qd_err}")

            if _db_err is not None:
                # DB 接続失敗 → 安全側に倒す (false-success 報告を防ぐ)
                result["stop_reason"] = f"db_connect_error: {_db_err}"
                raise RuntimeError(f"queue_date DB check failed: {_db_err}")

            if _today_cnt == 0:
                # 今日 queued 行なし → QueueExecutor を呼ばず即終了 (NO-OP)
                log.log(f"[INFO] queue_date={_today_str} queued=0 → NO-OP (early return)")
                result["stop_reason"] = "no_queue_today"
                # finally ブロックで heartbeat/stop が呼ばれる
            else:
                qe = QueueExecutor(queue_date=_today_str, limit=limit)
                qe._external_bm = compat  # may be unused; queue_executor opens own bm
                summary = qe.run()

                result["success"] = summary.get("posted", 0)
                result["fail"] = summary.get("failed", 0)
                result["skip"] = summary.get("skipped", 0)
                result["stop_reason"] = summary.get("stop_reason", "completed")
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
