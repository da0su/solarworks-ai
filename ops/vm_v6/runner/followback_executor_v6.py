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

    # 2026-05-24: VM-native FB executor (HOST followback_rpa を回避)
    # HOST followback_rpa は storage_state.json (古い) 依存 → KAPIBARAN session 切れで失敗
    # → VM の chrome_profile_followback (KAPIBARAN session 有り) で直接 follow click
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}
    hb.write(phase="startup", force=True)
    bm = BrowserManagerV6(action="followback")

    try:
        # DB から pending candidates を取得
        import sqlite3
        from datetime import datetime as _dt
        db_path = Path(r"\\vboxsvr\bot\data\room_bot_v5.db")
        if not db_path.exists():
            db_path = Path(r"\\vboxsvr\vm_data\room_bot_v5.db")
        con = sqlite3.connect(str(db_path), timeout=10)
        con.execute("PRAGMA busy_timeout = 5000")
        rows = con.execute(
            "SELECT id, follower_user_id, follower_username FROM followback_queue "
            "WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        if not rows:
            result["stop_reason"] = "source_empty"
            log.log("[ABORT] no pending candidates")
            return result
        ids = [r[0] for r in rows]
        log.log(f"got {len(rows)} pending candidates")
        # mark in_progress
        con.execute("UPDATE followback_queue SET status='in_progress' WHERE id IN ("
                    + ",".join("?" * len(ids)) + ")", ids)
        con.commit()

        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            # revert in_progress to pending
            con.execute("UPDATE followback_queue SET status='pending' WHERE id IN ("
                        + ",".join("?" * len(ids)) + ")", ids)
            con.commit()
            con.close()
            result["stop_reason"] = "login_expired"
            return result

        page = bm.page
        success_ids = []
        fail_ids = {}
        for qid, user_id, username in rows:
            if not user_id:
                continue
            try:
                url = f"https://room.rakuten.co.jp/{user_id}/items"
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)
                # follow ボタン (React)
                btn = page.locator('button[aria-label="フォローする"], button:has-text("フォローする")').first
                if btn.count() == 0:
                    # 既に follow 済か account 削除
                    fail_ids[qid] = "no_follow_button"
                    continue
                try:
                    btn.click(timeout=5000)
                except Exception as e:
                    fail_ids[qid] = f"click_err:{e}"
                    continue
                page.wait_for_timeout(2000)
                # verify
                followed = page.locator('button[aria-label="フォロー中"], button:has-text("フォロー中")').count() > 0
                if followed:
                    success_ids.append(qid)
                    log.log(f"OK [{len(success_ids)}/{limit}] {user_id}")
                else:
                    fail_ids[qid] = "verify_failed"
            except Exception as e:
                fail_ids[qid] = f"exception:{type(e).__name__}"

        # DB update
        now_iso = _dt.now().isoformat()
        for sid in success_ids:
            con.execute("UPDATE followback_queue SET status='completed', followed_at=? WHERE id=?",
                        (now_iso, sid))
        for fid, reason in fail_ids.items():
            # failed_reason column may not exist - use simple status only
            try:
                con.execute("UPDATE followback_queue SET status='failed' WHERE id=?", (fid,))
            except Exception:
                pass
        con.commit()
        con.close()
        result["success"] = len(success_ids)
        result["fail"] = len(fail_ids)
        result["stop_reason"] = "completed"
        log.log(f"FB summary: success={len(success_ids)} fail={len(fail_ids)}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.log(f"[ERROR] FB vm-native: {e}\n{tb[:1000]}")
        result["stop_reason"] = f"executor_error: {type(e).__name__}: {e}"
    finally:
        try:
            bm.stop()
        except Exception:
            pass
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        log.log(f"=== FOLLOWBACK executor v6 end: {result} ===")

    return result
