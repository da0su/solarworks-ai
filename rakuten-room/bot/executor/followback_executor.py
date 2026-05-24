#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOWBACK executor — follow back users who followed us.

Phase 4 scope (MVP):
  1. enqueue: scan followers list → insert into followback_queue with priority
  2. execute: pop pending → follow on VM (via VBoxManage or shared RPA layer)
  3. record:  update followback_queue.status + insert follow_log(action='followback')

Source priority (from マーケ 2026-04-23 directive):
    1.  fresh followers (detected_at within 48h)          priority=100
    2.  regular followers not yet followed back           priority=50
    3.  previously unfollowed (caution — may be intentional)  priority=10 (skipped by default)

Stop reasons:
    - source_empty          : followback_queue 空
    - rate_limit_detected   : followback でも rate_limit は適用
    - vb_lock_busy          : VB machine 他のアクションが占有
    - preflight_blocked     : preflight で critical NG
    - target_limit_reached  : --limit 件数到達（正常完了）
    - runtime_error         : 想定外例外

Usage:
    python -m rakuten-room.bot.executor.followback_executor --enqueue
    python -m rakuten-room.bot.executor.followback_executor --execute --limit 30
    python -m rakuten-room.bot.executor.followback_executor --status

Orchestration: orchestrator_v5 calls this executor under VbLock('followback').
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 2026-05-24: UNC path (VM 経由) では parents[3] が無いため try/except
try:
    REPO_ROOT = Path(__file__).resolve().parents[3]
    if not (REPO_ROOT / "rakuten-room").exists():
        raise FileNotFoundError(REPO_ROOT)
except (IndexError, FileNotFoundError, ValueError):
    # VM 経由: \\vboxsvr\bot\executor\followback_executor.py
    #   parents[1] = \\vboxsvr\bot  → そこが bot dir
    bot_dir = Path(__file__).resolve().parent.parent
    DB_PATH = bot_dir / "data" / "room_bot_v5.db"
    REPO_ROOT = bot_dir.parent.parent if bot_dir.parent.parent.exists() else bot_dir
else:
    DB_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"

STOP_REASONS = {
    "source_empty",
    "rate_limit_detected",
    "vb_lock_busy",
    "preflight_blocked",
    "target_limit_reached",
    "runtime_error",
}

# Priority tiers (higher = more urgent)
PRIORITY_FRESH = 100     # detected within FRESH_WINDOW_H hours
PRIORITY_REGULAR = 50    # standard followback
PRIORITY_UNFOLLOWED = 10 # previously unfollowed — skip by default

FRESH_WINDOW_H = 48


# ---- enqueue -----------------------------------------------------------------

def enqueue_followers(follower_list: list[dict]) -> dict:
    """
    Insert followers into followback_queue if not already present.
    follower_list: [{'user_id':..., 'username':..., 'is_following_us':True,
                     'we_are_following':False/True, 'previously_unfollowed':False/True}, ...]
    Returns counts dict.
    """
    if not DB_PATH.exists():
        return {"error": "v5 DB missing"}
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    inserted = skipped = 0
    now = datetime.now().isoformat()
    for f in follower_list:
        uid = f.get("user_id")
        if not uid:
            continue
        # Priority assignment
        if f.get("previously_unfollowed"):
            priority = PRIORITY_UNFOLLOWED
        else:
            priority = PRIORITY_FRESH if f.get("is_fresh") else PRIORITY_REGULAR
        try:
            cur.execute(
                """INSERT INTO followback_queue
                      (follower_user_id, follower_username, detected_at,
                       is_already_following, is_previously_unfollowed,
                       priority, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (uid, f.get("username"), now,
                 1 if f.get("we_are_following") else 0,
                 1 if f.get("previously_unfollowed") else 0,
                 priority),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    con.commit()
    con.close()
    return {"inserted": inserted, "skipped_duplicates": skipped, "total_submitted": len(follower_list)}


# ---- queue operations --------------------------------------------------------

def queue_status() -> dict:
    if not DB_PATH.exists():
        return {"error": "v5 DB missing"}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = con.cursor()
    stats = {}
    for status_val in ("pending", "in_progress", "completed", "failed", "skipped"):
        cur.execute("SELECT COUNT(*) FROM followback_queue WHERE status=?", (status_val,))
        stats[status_val] = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM followback_queue WHERE status='pending' AND is_previously_unfollowed=0"
    )
    stats["pending_safe"] = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM follow_log WHERE action='followback' "
        "AND followed_at >= date('now','localtime')"
    )
    stats["today_followback"] = cur.fetchone()[0]
    con.close()
    return stats


def _pop_pending(limit: int, include_unfollowed: bool = False) -> list[tuple]:
    """Return up to `limit` pending rows ordered by priority desc, detected_at asc."""
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    where = "status='pending'"
    if not include_unfollowed:
        where += " AND is_previously_unfollowed=0"
    cur.execute(
        f"""SELECT id, follower_user_id, follower_username, priority,
                   is_already_following, is_previously_unfollowed
              FROM followback_queue
             WHERE {where}
             ORDER BY priority DESC, detected_at ASC
             LIMIT ?""",
        (limit,),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def _mark_in_progress(ids: list[int]) -> None:
    if not ids:
        return
    con = sqlite3.connect(str(DB_PATH))
    qmarks = ",".join(["?"] * len(ids))
    con.execute(f"UPDATE followback_queue SET status='in_progress' WHERE id IN ({qmarks})", ids)
    con.commit()
    con.close()


def _mark_completed(queue_id: int, followed_at: str) -> None:
    con = sqlite3.connect(str(DB_PATH))
    con.execute(
        "UPDATE followback_queue SET status='completed', followed_at=? WHERE id=?",
        (followed_at, queue_id),
    )
    con.commit()
    con.close()


def _mark_failed(queue_id: int, reason: str) -> None:
    con = sqlite3.connect(str(DB_PATH))
    con.execute(
        "UPDATE followback_queue SET status='failed', followed_at=NULL WHERE id=?",
        (queue_id,),
    )
    con.commit()
    con.close()


def _log_followback(user_id: str, username: Optional[str], session_id: str) -> None:
    con = sqlite3.connect(str(DB_PATH))
    now = datetime.now().isoformat()
    try:
        con.execute(
            """INSERT INTO follow_log
                  (target_user_id, target_username, source, action,
                   followed_at, session_id, status)
               VALUES (?, ?, 'followback_queue', 'followback', ?, ?, 'success')""",
            (user_id, username, now, session_id),
        )
        con.commit()
    except sqlite3.IntegrityError:
        pass
    con.close()


# ---- execute -----------------------------------------------------------------

def execute(limit: int, include_unfollowed: bool = False,
            session_id: Optional[str] = None, dry_run: bool = False,
            headless: bool = False) -> dict:
    """
    Pop and follow back up to `limit` candidates.

    2026-04-23 Phase 4b: wired to followback_rpa.do_followback_batch.
    Flow:
      1. _pop_pending → rows (pending candidates)
      2. _mark_in_progress(ids)
      3. Call RPA batch (Playwright click on each profile)
      4. Per result: _mark_completed / _mark_failed + _log_followback on success
      5. Return aggregate stop_reason
    """
    session_id = session_id or f"fb-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    rows = _pop_pending(limit, include_unfollowed=include_unfollowed)
    if not rows:
        return {"status": "ok", "stop_reason": "source_empty",
                "processed": 0, "success": 0, "session_id": session_id}
    ids = [r[0] for r in rows]
    _mark_in_progress(ids)

    if dry_run:
        for qid, uid, uname, prio, is_following, prev_unf in rows:
            print(f"  [DRY] would followback uid={uid} uname={uname} prio={prio}")
        # Dry-run: DO NOT mark completed or log — state remains in_progress
        # so callers can re-run the real execute and pick up the same candidates.
        return {"status": "ok", "stop_reason": "dry_run_complete",
                "processed": len(rows), "success": 0, "failed": 0,
                "session_id": session_id}

    # Real execution via RPA (import lazily to avoid Playwright cost for --status)
    import importlib, os
    # Ensure executor/ is importable regardless of how we were launched
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    rpa_mod = importlib.import_module("followback_rpa")
    do_followback_batch = rpa_mod.do_followback_batch

    targets = [{"user_id": uid, "username": uname, "queue_id": qid}
               for qid, uid, uname, prio, is_following, prev_unf in rows]

    batch_result = do_followback_batch(targets, headless=headless)

    success = 0
    failed = 0
    skipped = 0
    for u in batch_result.get("per_user", []):
        qid = u.get("queue_id")
        uid = u.get("user_id")
        uname = u.get("username")
        status = u.get("status")
        reason = u.get("reason") or ""
        if status == "success":
            _log_followback(uid, uname, session_id)
            _mark_completed(qid, datetime.now().isoformat())
            success += 1
        elif status == "skipped":
            # Count as skipped (already followed / no button) — still mark completed
            # so the queue advances.
            _mark_completed(qid, datetime.now().isoformat())
            skipped += 1
        else:
            _mark_failed(qid, reason[:120])
            failed += 1

    if batch_result.get("aborted"):
        stop_reason = batch_result.get("abort_reason") or "aborted"
    elif success + skipped + failed >= limit:
        stop_reason = "target_limit_reached"
    elif success + skipped + failed >= len(rows):
        stop_reason = "source_exhausted"
    else:
        stop_reason = "partial"

    return {
        "status": "ok" if not batch_result.get("aborted") else "aborted",
        "stop_reason": stop_reason,
        "processed": len(rows),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "session_id": session_id,
    }


# ---- CLI ---------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="FOLLOWBACK executor")
    parser.add_argument("--enqueue", action="store_true",
                        help="enqueue followers from stdin JSON list (testing/seeding)")
    parser.add_argument("--execute", action="store_true",
                        help="execute pending queue up to --limit")
    parser.add_argument("--status", action="store_true",
                        help="show queue counts + today_followback")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--include-unfollowed", action="store_true",
                        help="also process previously_unfollowed targets (default: skip)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do not mark completed; log only")
    parser.add_argument("--headless", action="store_true",
                        help="run Chromium headless (default: headed for observability)")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(queue_status(), ensure_ascii=False, indent=2))
        return 0

    if args.enqueue:
        try:
            payload = json.loads(sys.stdin.read())
        except Exception as e:
            print(f"stdin JSON parse error: {e}", file=sys.stderr)
            return 1
        result = enqueue_followers(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.execute:
        result = execute(args.limit, include_unfollowed=args.include_unfollowed,
                         dry_run=args.dry_run, headless=args.headless)
        # 2026-04-23: Playwright teardown on Windows may close stdout;
        # fall back to stderr so orchestrator can still parse the JSON tail.
        line = json.dumps(result, ensure_ascii=False)
        try:
            print(line)
            sys.stdout.flush()
        except (ValueError, OSError):
            try:
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
            except Exception:
                pass
        return 0 if result.get("status") == "ok" else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
