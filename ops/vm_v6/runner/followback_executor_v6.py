#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 FOLLOWBACK executor: 既存 followback_executor を thin wrap.

2026-05-26 改善4: /my/followers 自律スキャン
  pending=0 の場合、bm.page で /my/followers を直接スキャンして
  followback_queue に新規 pending を INSERT → pool 自己補充で source_empty 回避
"""
from __future__ import annotations

import re as _re
import sys
from datetime import datetime as _dt
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger, emergency_disk_cleanup_once
from .browser_manager_v6 import BrowserManagerV6

# 2026-05-26: VM disk full → Chrome EPIPE 防止. import 時に1回 cleanup.
try:
    emergency_disk_cleanup_once()
except Exception as _e:
    print(f"[disk_cleanup_fb] err: {_e}")

# 2026-05-24: VM では UNC path 経由 (parents[3] 無)
try:
    HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
    if not HOST_BOT_DIR.exists():
        raise FileNotFoundError(HOST_BOT_DIR)
except (IndexError, FileNotFoundError, ValueError):
    HOST_BOT_DIR = Path(r"\\vboxsvr\bot")
if HOST_BOT_DIR.exists():
    sys.path.insert(0, str(HOST_BOT_DIR))

# 2026-05-26 fix: 楽天 ROOM のユーザー URL は 2 形式
#   1) /room_XXXXXXXX/items (system-generated)
#   2) /USERNAME/items (custom; ex: /sirochang/items /icco.com/items /hide.dice/items)
# 旧版は (1) しか拾わず seen=7 で停止 → 14 名の custom username を取り逃していた
_ROOM_ID_RE = _re.compile(r'^room_[0-9a-f]{8,}$|^room_[a-z0-9_.]{4,40}$')
_CUSTOM_USERNAME_RE = _re.compile(r'^[a-zA-Z0-9_.\-]{3,40}$')
# 除外パス (followers/items 自身や my/* 等)
_RESERVED_SEGMENTS = {
    "my", "items", "discover", "timeline", "u", "user", "users",
    "rebates", "search", "static", "ranking", "trends", "footer",
    "header", "help", "login", "logout", "signin", "signup",
    "tag", "category", "categories", "feed", "topic", "topics",
    "official", "campaign", "campaigns", "about", "contact",
    "terms", "privacy", "ad", "ads", "api", "https:", "http:",
}


def _scan_my_followers(page, con, log: SessionLogger, scan_limit: int = 400) -> int:
    """bm.page を使って /my/followers をスキャンし followback_queue に pending INSERT.

    Returns: 追加した pending 件数

    2026-05-26 改善: room_/custom の両 URL 形式を捕獲. 自分自身 (my own user_id) は除外.
    """
    try:
        page.goto("https://room.rakuten.co.jp/my/followers",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        if "grp01.id.rakuten.co.jp" in page.url or "login" in page.url:
            log.log("[scan_followers] not logged in → skip")
            return 0

        # 自分の user_id (URL から取得して除外する)
        my_user_id: str | None = None
        try:
            # ヘッダーの自分への link 等から抽出
            self_link = page.locator('a[href*="/items"]').first
            if self_link.count() > 0:
                href = self_link.get_attribute("href") or ""
                # /room_XXX/items or /USERNAME/items
                seg = href.lstrip("/").split("/", 1)[0]
                if seg and seg not in _RESERVED_SEGMENTS:
                    my_user_id = seg
        except Exception:
            pass

        # 2026-05-26: 'failed' も含めて取得 (再 INSERT 防止)
        # KAPIBARAN 既フォロワーは既に "フォロー中" 状態 → click 失敗 → status='failed'
        # これを除外しないと 30分後 scan で同じユーザーを 14名 重複 INSERT してしまう
        cur = con.cursor()
        cur.execute(
            "SELECT DISTINCT follower_user_id FROM followback_queue "
            "WHERE status IN ('pending', 'completed', 'failed', 'in_progress')"
        )
        already_queued: set[str] = {r[0] for r in cur.fetchall() if r[0]}

        # 自分がフォロー済みのユーザー (follow_log) を取得
        try:
            cur.execute(
                "SELECT DISTINCT target_user_id FROM follow_log WHERE status='success'"
            )
            already_following: set[str] = {r[0] for r in cur.fetchall() if r[0]}
        except Exception:
            already_following = set()

        skip_set = already_queued | already_following
        if my_user_id:
            skip_set.add(my_user_id)
            log.log(f"[scan_followers] my_user_id={my_user_id} (self-exclude)")

        collected: list[dict] = []
        seen: set[str] = set()
        last_h = 0
        stuck = 0

        for scroll_i in range(60):
            # 2026-05-26: 全 anchor を見て /SEG/items パターンを抽出
            anchors = page.query_selector_all('a[href*="/items"]')
            for a in anchors:
                href = (a.get_attribute("href") or "").strip()
                if not href.startswith("/") or "/items" not in href:
                    continue
                # /SEG/items または /SEG/items?xxx
                parts = href.lstrip("/").split("/")
                if not parts:
                    continue
                seg = parts[0].split("?")[0]
                if not seg or seg in _RESERVED_SEGMENTS:
                    continue
                # match either /room_XXX/ or /USERNAME/
                if not (_ROOM_ID_RE.match(seg) or _CUSTOM_USERNAME_RE.match(seg)):
                    continue
                if seg in seen:
                    continue
                seen.add(seg)
                if seg not in skip_set:
                    uname = (a.inner_text() or "").strip()[:60] or seg
                    collected.append({"user_id": seg, "username": uname})
            if len(collected) >= scan_limit:
                break
            h = page.evaluate("document.body.scrollHeight")
            if h == last_h:
                stuck += 1
                if stuck >= 4:
                    break
                try:
                    page.keyboard.press("End")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
            else:
                stuck = 0
            last_h = h
            page.evaluate("window.scrollBy(0, 2000)")
            page.evaluate("""
                const containers = document.querySelectorAll(
                    '[class*="scroll"],[class*="list"],[role="feed"]'
                );
                containers.forEach(c => {
                    if (c.scrollHeight > c.clientHeight) c.scrollTop = c.scrollHeight;
                });
            """)
            page.wait_for_timeout(1500)

        if not collected:
            log.log("[scan_followers] collected=0 (page may be empty or DOM changed)")
            return 0

        now_iso = _dt.now().isoformat()
        inserted = 0
        for c in collected:
            try:
                con.execute(
                    "INSERT OR IGNORE INTO followback_queue "
                    "(follower_user_id, follower_username, detected_at, status) "
                    "VALUES (?, ?, ?, 'pending')",
                    (c["user_id"], c["username"], now_iso)
                )
                inserted += 1
            except Exception as _ie:
                log.log(f"[scan_followers] INSERT skip {c['user_id']}: {_ie}")
        con.commit()
        log.log(f"[scan_followers] seen={len(seen)} new_pending={inserted}")
        return inserted

    except Exception as e:
        log.log(f"[scan_followers] error: {e}")
        return 0


def run_followback(limit: int = 30, hb: HeartbeatPusher = None, log: SessionLogger = None) -> dict:
    if hb is None:
        hb = HeartbeatPusher("followback")
    if log is None:
        log = SessionLogger("followback")

    log.log(f"=== FOLLOWBACK executor v6 start: limit={limit} ===")
    hb.write(phase="startup", force=True)

    # 2026-05-24: VM-native FB executor (HOST followback_rpa を回避)
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}
    hb.write(phase="startup", force=True)
    bm = BrowserManagerV6(action="followback")
    con = None  # 例外パスでも finally で close できるよう初期化

    try:
        import sqlite3
        db_path = Path(r"\\vboxsvr\bot\data\room_bot_v5.db")
        if not db_path.exists():
            db_path = Path(r"\\vboxsvr\vm_data\room_bot_v5.db")
        con = sqlite3.connect(str(db_path), timeout=10)
        con.execute("PRAGMA busy_timeout = 5000")

        # ── ブラウザ起動 & ログイン確認 ──
        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            con.close()
            result["stop_reason"] = "login_expired"
            return result

        page = bm.page

        # ── pending candidates を取得 ──
        rows = con.execute(
            "SELECT id, follower_user_id, follower_username FROM followback_queue "
            "WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
        ).fetchall()

        if not rows:
            # pending=0 → /my/followers を直接スキャンして pool 自己補充
            # (HOST の followback_source_feed に依存せず自律運転)
            log.log("[pool_empty] pending=0 → /my/followers 自動スキャン開始")
            hb.write(phase="pool_scan")
            inserted = _scan_my_followers(page, con, log, scan_limit=400)
            log.log(f"[pool_refresh] inserted={inserted} new pending candidates")
            if inserted > 0:
                rows = con.execute(
                    "SELECT id, follower_user_id, follower_username FROM followback_queue "
                    "WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
                ).fetchall()

        if not rows:
            result["stop_reason"] = "source_empty"
            log.log("[ABORT] no pending candidates (even after scan)")
            con.close()
            return result

        ids = [r[0] for r in rows]
        log.log(f"got {len(rows)} pending candidates")
        # mark in_progress
        con.execute(
            "UPDATE followback_queue SET status='in_progress' WHERE id IN ("
            + ",".join("?" * len(ids)) + ")", ids
        )
        con.commit()

        # ── followback 実行 ──
        hb.write(phase="followback_loop")
        success_ids = []
        already_following_ids = []  # 既フォロー済として completed 扱い (2026-05-26)
        fail_ids = {}
        for qid, user_id, username in rows:
            if not user_id:
                continue
            try:
                url = f"https://room.rakuten.co.jp/{user_id}/items"
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)
                # 2026-05-26: 「フォロー中」を先に検出 → 既フォロー済なら completed (no-op)
                # AngularJS 旧 ROOM では a[ng-if*="following"] / button 両方をカバー
                following_already = (
                    page.locator(
                        'button[aria-label="フォロー中"], button:has-text("フォロー中"), '
                        'a:has-text("フォロー中"), [class*="following"]'
                    ).count() > 0
                )
                if following_already:
                    already_following_ids.append(qid)
                    log.log(f"ALREADY [{len(already_following_ids)}] {user_id} (フォロー中)")
                    continue
                # follow ボタン (React + AngularJS 両対応)
                btn = page.locator(
                    'button[aria-label="フォローする"], button:has-text("フォローする"), '
                    'a:has-text("フォローする"), a.follow-button, button.follow-button'
                ).first
                if btn.count() == 0:
                    fail_ids[qid] = "no_follow_button"
                    log.log(f"FAIL {user_id}: no_follow_button")
                    continue
                try:
                    btn.click(timeout=5000)
                except Exception as e:
                    fail_ids[qid] = f"click_err:{type(e).__name__}"
                    log.log(f"FAIL {user_id}: click_err {type(e).__name__}")
                    continue
                page.wait_for_timeout(2000)
                # verify
                followed = (
                    page.locator(
                        'button[aria-label="フォロー中"], button:has-text("フォロー中"), '
                        'a:has-text("フォロー中"), [class*="following"]'
                    ).count() > 0
                )
                if followed:
                    success_ids.append(qid)
                    log.log(f"OK [{len(success_ids)}/{limit}] {user_id}")
                else:
                    fail_ids[qid] = "verify_failed"
                    log.log(f"FAIL {user_id}: verify_failed")
            except Exception as e:
                fail_ids[qid] = f"exception:{type(e).__name__}"
                log.log(f"FAIL {user_id}: exception {type(e).__name__}: {str(e)[:80]}")

        # ── DB update ──
        now_iso = _dt.now().isoformat()
        for sid in success_ids:
            con.execute(
                "UPDATE followback_queue SET status='completed', followed_at=? WHERE id=?",
                (now_iso, sid)
            )
        # 2026-05-26: 既フォロー済 (already_following) も completed 扱い → 再 INSERT 防止
        for aid in already_following_ids:
            try:
                con.execute(
                    "UPDATE followback_queue SET status='completed', followed_at=? WHERE id=?",
                    (now_iso, aid)
                )
            except Exception:
                pass
        for fid in fail_ids:
            try:
                con.execute(
                    "UPDATE followback_queue SET status='failed' WHERE id=?", (fid,)
                )
            except Exception:
                pass
        con.commit()
        con.close()
        result["success"] = len(success_ids)
        result["skip"] = len(already_following_ids)  # 既フォロー済を skip としてカウント
        result["fail"] = len(fail_ids)
        result["stop_reason"] = "completed"
        log.log(
            f"FB summary: success={len(success_ids)} already={len(already_following_ids)} "
            f"fail={len(fail_ids)} fail_reasons={list(set(fail_ids.values()))}"
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.log(f"[ERROR] FB vm-native: {e}\n{tb[:1000]}")
        result["stop_reason"] = f"executor_error: {type(e).__name__}: {e}"
    finally:
        try:
            if con:
                con.close()  # 例外/クラッシュ時の DB 接続リーク防止
        except Exception:
            pass
        try:
            bm.stop()
        except Exception:
            pass
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        log.log(f"=== FOLLOWBACK executor v6 end: {result} ===")

    return result
