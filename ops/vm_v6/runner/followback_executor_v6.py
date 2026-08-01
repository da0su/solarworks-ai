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
from urllib.parse import urlparse as _urlparse

# Known Rakuten login hostnames (module-level constant for reuse)
_RAKUTEN_LOGIN_HOSTS = frozenset({
    "grp01.id.rakuten.co.jp",
    "rlogin.rakuten.co.jp",
    "login.account.rakuten.co.jp",
    "login.account.rakuten.com",
})

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


def _is_login_redirect(url: str) -> bool:
    """Return True if url is a Rakuten login redirect (session expired).

    Shared by main flow and fallback URL check.
    session/upgrade is excluded (it's an in-session page, not a full logout).
    """
    try:
        _p = _urlparse(url)
        _host = (_p.hostname or "").lower()
        _path = _p.path.lower()
    except Exception:
        _host = ""
        _path = url.lower()
    return (
        (_host in _RAKUTEN_LOGIN_HOSTS or "/nid/" in _path)
        and "session/upgrade" not in _path
    )


def _scan_my_followers(page, con, log: SessionLogger, scan_limit: int = 400) -> int:
    """bm.page を使って /my/followers をスキャンし followback_queue に pending INSERT.

    Returns: 追加した pending 件数

    2026-05-26 改善: room_/custom の両 URL 形式を捕獲. 自分自身 (my own user_id) は除外.
    """
    try:
        page.goto("https://room.rakuten.co.jp/my/followers",
                  wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        _url = page.url
        if _is_login_redirect(_url):
            log.log(f"[scan_followers] login redirect detected ({_url[:80]}) → session expired")
            return -1  # sentinel: login_expired (caller checks for -1)

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

        # 2026-05-28 fix: FOLLOW executor は follow_log ではなく follow_history.json に書き込む。
        # follow_log 681 件だけでは FOLLOW 済みユーザーを取り逃し false positive INSERT する。
        try:
            import json as _fb_json
            _fh_path = HOST_BOT_DIR / "data" / "follow_history.json"
            if _fh_path.exists():
                with open(str(_fh_path), "r", encoding="utf-8") as _fhf:
                    _fh_data = _fb_json.load(_fhf)
                _fh_ids = {e.get("user_id") for e in _fh_data if e.get("user_id")}
                log.log(f"[scan_followers] follow_history.json: {len(_fh_ids)} entries → merged into already_following")
                already_following |= _fh_ids
        except Exception as _fhe:
            log.log(f"[scan_followers] follow_history.json load err: {_fhe}")

        skip_set = already_queued | already_following
        if my_user_id:
            skip_set.add(my_user_id)
            log.log(f"[scan_followers] my_user_id={my_user_id} (self-exclude)")
        log.log(f"[scan_followers] skip_set={len(skip_set)} (queued={len(already_queued)} following={len(already_following)})")

        collected: list[dict] = []
        seen: set[str] = set()
        last_h = 0
        stuck = 0

        # Wait for follower list DOM content to appear before scanning
        try:
            page.wait_for_selector(
                'a[href$="/items"], a[href^="/room_"], [class*="follower"], [class*="Follower"]',
                timeout=8000
            )
        except Exception:
            log.log("[scan_followers] wait_for_selector timeout (proceeding anyway)")

        _OWN_ID = my_user_id or ""  # exclude own profile from JS scan
        # 2026-05-28 fix: extend to capture custom usernames (non-room_ prefix)
        # Rakuten ROOM follower cards link to /username/items OR /room_xxx/items.
        # Old code only matched ROOM_ID_RE (/^room_/) → missed all custom-username followers.
        # Fix: allowCustom=true when href matches /seg/items (profile link pattern).
        _JS_SCAN = """
            (own_id) => {
                const ROOM_ID_RE = /^room_[a-z0-9_.]{4,40}$|^room_[0-9a-f]{8,}$/;
                const CUSTOM_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,39}$/;
                const RESERVED = new Set(['my','items','discover','timeline','u','user','users',
                    'rebates','search','static','ranking','trends','footer','header','help',
                    'login','logout','signin','signup','tag','category','categories','feed',
                    'topic','topics','official','campaign','campaigns','about','contact',
                    'terms','privacy','ad','ads','api','https:','http:','1700','nid',
                    'auth','session','account','register','settings','notification','notifications',
                    'followers','following','likes','liked','posts','favorites','ranking']);
                const results = new Map();

                function tryAdd(seg, name, allowCustom) {
                    if (!seg || seg.length < 3) return;
                    if (RESERVED.has(seg.toLowerCase())) return;
                    if (seg === own_id) return;
                    if (/^\\d+$/.test(seg)) return;
                    // 2026-07-16: room_ + 長い純数字 は内部数値IDでプロフィール URL として無効。
                    // ROOM_ID_RE の [a-z0-9] が数字を通すため、ここで明示的に弾く。
                    // 実在スラッグは room_ + 8-12 桁の hex (例 room_ceab03401f)。
                    // 混入すると存在しないプロフィールを開き続け 100% verify_failed になる。
                    if (/^room_\\d{13,}$/.test(seg)) return;
                    if (ROOM_ID_RE.test(seg)) {
                        if (!results.has(seg)) results.set(seg, (name || seg).substring(0, 60));
                        return;
                    }
                    // Accept custom usernames only from /username/items profile links
                    if (allowCustom && CUSTOM_RE.test(seg)) {
                        if (!results.has(seg)) results.set(seg, (name || seg).substring(0, 60));
                    }
                }

                // Method 1: extract user IDs from all href attributes
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const clean = href.replace(/^https?:\\/\\/[^\\/]+/, '');
                    const parts = (clean.startsWith('/') ? clean : '/' + clean).split('/').filter(Boolean);
                    if (parts.length > 0) {
                        const seg = parts[0].split('?')[0];
                        const name = a.textContent ? a.textContent.trim() : '';
                        // /seg/items is definitely a room profile link → allow custom username
                        const isItemsLink = parts.length >= 2 && parts[1].split('?')[0] === 'items';
                        tryAdd(seg, name, isItemsLink);
                        // also regex match anywhere in href for room_ IDs
                        const m = href.match(/(?:^|\\/)((room_[a-z0-9_.]{4,40}|room_[0-9a-f]{8,}))(?:\\/|\\?|$)/);
                        if (m) tryAdd(m[1], name, false);
                    }
                });

                // Method 2: data attributes
                document.querySelectorAll('[data-userid],[data-user-id],[data-roomid],[data-room-id]').forEach(el => {
                    const uid = (el.dataset.userid || el.dataset.userId || el.dataset.roomid || el.dataset.roomId || '').trim();
                    if (uid) tryAdd(uid, el.textContent ? el.textContent.trim() : '', true);
                });

                // Method 3: scan user/follower class elements for room_ IDs
                document.querySelectorAll('[class*="user"],[class*="User"],[class*="follower"],[class*="Follower"],[class*="room"],[class*="Room"]').forEach(el => {
                    if (el.children.length > 5) return;
                    const txt = el.textContent ? el.textContent.trim() : '';
                    const m = txt.match(/^(room_[a-z0-9_.]{4,40}|room_[0-9a-f]{8,})$/);
                    if (m) tryAdd(m[1], m[1], false);
                });

                return Array.from(results.entries()).map(([uid, name]) => ({uid, name}));
            }
        """
        _JS_NON_SELF_HREFS = """
            (own_id) => Array.from(new Set(
                Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href'))
                    .filter(h => h && !h.includes(own_id) && !h.startsWith('#'))
                    .slice(0, 30)
            ))
        """
        for scroll_i in range(30):
            js_users = page.evaluate(_JS_SCAN, _OWN_ID)

            # Debug on first scroll
            if scroll_i == 0:
                log.log(f"[scan_followers] scroll0: js_users={len(js_users)} skip_set={len(skip_set)}")
                # Also log ALL unique href patterns for diagnosis
                all_hrefs = page.evaluate(_JS_NON_SELF_HREFS, _OWN_ID)
                log.log(f"[scan_followers] scroll0: non-self hrefs sample={all_hrefs[:10]}")

            for item in js_users:
                seg = (item.get("uid") or "").strip()
                uname = (item.get("name") or seg)[:60]
                if not seg or seg in seen:
                    continue
                seen.add(seg)
                if seg not in skip_set:
                    collected.append({"user_id": seg, "username": uname})

            if len(collected) >= scan_limit:
                break

            # Scroll to load more followers
            h = page.evaluate("document.body.scrollHeight")
            page.evaluate("""
                window.scrollBy(0, 3000);
                document.querySelectorAll('[class*="scroll"],[class*="Scroll"],[class*="list"],[class*="List"],[role="feed"],[class*="follow"],[class*="Follow"]').forEach(c => {
                    if (c.scrollHeight > c.clientHeight) {
                        c.scrollTop += 3000;
                    }
                });
            """)
            page.wait_for_timeout(2000)
            h2 = page.evaluate("document.body.scrollHeight")
            if h2 == h and scroll_i >= 3:
                # body not growing, check containers
                container_grew = page.evaluate("""
                    (() => {
                        let grew = false;
                        document.querySelectorAll('[class*="follow"],[class*="Follow"],[role="feed"],[class*="list"]').forEach(c => {
                            if (c.scrollTop < c.scrollHeight - c.clientHeight - 10) grew = true;
                        });
                        return grew;
                    })()
                """)
                if not container_grew:
                    stuck += 1
                    if stuck >= 5:
                        break
                else:
                    stuck = 0
            else:
                stuck = 0

        if not collected and my_user_id:
            # Fallback: try personalized URL /room_XXX/followers (may render differently)
            log.log(f"[scan_followers] collected=0 on /my/followers → retry /{my_user_id}/followers")
            try:
                page.goto(
                    f"https://room.rakuten.co.jp/{my_user_id}/followers",
                    wait_until="networkidle", timeout=30000
                )
                page.wait_for_timeout(5000)
                # Guard: shared helper (same logic as main-flow login check)
                _fb_url = page.url
                if _is_login_redirect(_fb_url):
                    log.log("[scan_followers] fallback login redirect → login_expired")
                    return -1  # propagate as login_expired (same sentinel as main flow)
                # Path adjacency check: /{my_user_id}/followers (adjacent segments)
                # my_user_id is non-None (guaranteed by outer if-guard)
                try:
                    _fb_path = _urlparse(_fb_url).path.lower()
                except Exception:
                    _fb_path = _fb_url.lower()
                _segs = [s for s in _fb_path.strip("/").split("/") if s]
                _uid_lc = my_user_id.lower()
                _fb_path_ok = any(
                    _segs[i] == _uid_lc and i + 1 < len(_segs) and _segs[i + 1] == "followers"
                    for i in range(len(_segs))
                )
                if not _fb_path_ok:
                    log.log(f"[scan_followers] fallback URL mismatch → skip (url={_fb_url[:80]})")
                else:
                    try:
                        page.wait_for_selector('a[href$="/items"], a[href^="/room_"]', timeout=8000)
                    except Exception:
                        pass
                    fb_hrefs = page.evaluate(_JS_NON_SELF_HREFS, _OWN_ID)
                    # 2026-08-01 真因修正: fallback は1回スキャンするだけで
                    # スクロールしていなかった。実測 (_probe_followers_scroll.py) では
                    #   /my/followers        → 7件で固定・スクロールしても増えない (壊れている)
                    #   /{own_id}/followers  → 19→36→52→69→86→104 と正常に増える
                    # つまり「増える方」を1回しか読まないため先頭20件しか見えず、
                    # skip_set(36,562) に全部入っていて collected=0 になっていた。
                    # → 本体と同じスクロールループを回す。
                    fb_seen_before = len(seen)
                    fb_stuck = 0
                    for fb_i in range(30):
                        fb_js_users = page.evaluate(_JS_SCAN, _OWN_ID)
                        if fb_i == 0:
                            log.log(f"[scan_followers] fallback: js_users={len(fb_js_users)} "
                                    f"hrefs_count={len(fb_hrefs)} sample={fb_hrefs[:5]}")
                        before_n = len(seen)
                        for item in fb_js_users:
                            seg = (item.get("uid") or "").strip()
                            uname = (item.get("name") or seg)[:60]
                            if not seg or seg in seen:
                                continue
                            seen.add(seg)
                            if seg not in skip_set:
                                collected.append({"user_id": seg, "username": uname})
                        if len(collected) >= scan_limit:
                            log.log(f"[scan_followers] fallback: scan_limit到達 (scroll={fb_i})")
                            break
                        # 新規 uid が増えなくなったら打ち切り (無駄なスクロールを避ける)
                        if len(seen) == before_n and fb_i >= 3:
                            fb_stuck += 1
                            if fb_stuck >= 5:
                                log.log(f"[scan_followers] fallback: 新規なしが続くため終了 (scroll={fb_i})")
                                break
                        else:
                            fb_stuck = 0
                        page.evaluate("""
                            () => {
                                window.scrollBy(0, 3000);
                                document.querySelectorAll('*').forEach(c => {
                                    const s = getComputedStyle(c);
                                    if (/(auto|scroll)/.test(s.overflowY)
                                        && c.scrollHeight > c.clientHeight + 50) {
                                        c.scrollTop = c.scrollHeight;
                                    }
                                });
                            }
                        """)
                        page.wait_for_timeout(2000)
                    log.log(f"[scan_followers] fallback collected={len(collected)} "
                            f"(uid走査 {len(seen) - fb_seen_before} 件増)")
            except Exception as _fe:
                log.log(f"[scan_followers] fallback error: {_fe}")

        if not collected:
            log.log("[scan_followers] collected=0 (both URLs returned empty)")
            return 0

        now_iso = _dt.now().isoformat()
        inserted = 0
        # Codex REJECT 反映 (2026-05-26): inserted は rowcount で正確に判定
        # INSERT OR IGNORE は重複時にエラーを投げず rowcount=0 を返すため
        # try/except 内の inserted += 1 では「重複skip も加算」してしまう (虚偽報告)
        duplicate_skipped = 0
        for c in collected:
            try:
                cur = con.execute(
                    "INSERT OR IGNORE INTO followback_queue "
                    "(follower_user_id, follower_username, detected_at, status) "
                    "VALUES (?, ?, ?, 'pending')",
                    (c["user_id"], c["username"], now_iso)
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    duplicate_skipped += 1  # UNIQUE 制約で IGNORE された
            except sqlite3.IntegrityError as _ie:
                # UNIQUE 以外の制約違反 (CHECK/NOT NULL/FK) は明示的に区別
                log.log(f"[scan_followers] INTEGRITY skip {c['user_id']}: {_ie}")
            except Exception as _e:
                log.log(f"[scan_followers] ERROR insert {c['user_id']}: {_e}")
        con.commit()
        log.log(f"[scan_followers] insert_breakdown: new={inserted} dup={duplicate_skipped}")
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

    # 2026-05-28 fix: LIKE(HH:10/40)/FOLLOW(HH:15/45) と同時起動すると Chrome 3 本同時
    # 起動になり VM リソース競合で goto() が hang する. bm.start() 前に既存 runner の
    # heartbeat が stale (>120s 更新なし) になるまで最大 10 分待つ.
    # subprocess/WMIC 不使用 → 共有 heartbeat ファイルの mtime を参照するだけ.
    import time as _fb_time
    from pathlib import Path as _fb_P
    _hb_share = _fb_P(r"\\vboxsvr\share")
    # 2026-06-08 修正(CEoFB目標150化で発覚): 旧 AND 条件 + 10分待ち は FOLLOW がほぼ常時稼働の
    # 環境で永遠に成立せず、待機だけで終了→FB成功0 の根本原因。
    # 新: (1) like_age>120 が満たされれば進む(LIKEは間欠的なので主条件) (2) FOLLOWは長期稼働で
    # heartbeat 鮮度だけでは判断不能なので必須条件から外す (3) 最大待機を 180s に短縮し残時間を投稿に使う。
    _wait_start = _fb_time.time()
    while _fb_time.time() - _wait_start < 180:  # max 3 min (旧 10 min → 短縮)
        _like_age = 9999.0
        _follow_age = 9999.0
        try:
            _lf = _hb_share / "heartbeat_like.json"
            if _lf.exists():
                _like_age = _fb_time.time() - _lf.stat().st_mtime
        except Exception:
            pass
        try:
            _ff = _hb_share / "heartbeat_follow.json"
            if _ff.exists():
                _follow_age = _fb_time.time() - _ff.stat().st_mtime
        except Exception:
            pass
        if _like_age > 120:  # LIKE が静かなら FOLLOW と同時起動の Chrome 競合は起きにくい
            log.log(f"[fb_wait] like_age={_like_age:.0f}s follow_age={_follow_age:.0f}s → proceed")
            break
        log.log(f"[fb_wait] like_age={_like_age:.0f}s follow_age={_follow_age:.0f}s → wait 20s")
        _fb_time.sleep(20)

    # 2026-05-24: VM-native FB executor (HOST followback_rpa を回避)
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}
    hb.write(phase="startup", force=True)
    # 2026-05-28 debug: hang 真因究明のため各 step を log で trace
    log.log("[trace] step1: import sqlite3 + db_path check")
    con = None
    bm = None  # finally の bm.stop() で NameError 防止
    try:
        import sqlite3
        db_path = Path(r"\\vboxsvr\bot\data\room_bot_v5.db")
        log.log(f"[trace] step2: db_path.exists() = ?")
        if not db_path.exists():
            db_path = Path(r"\\vboxsvr\vm_data\room_bot_v5.db")
        log.log(f"[trace] step3: db_path={db_path}")
        con = sqlite3.connect(str(db_path), timeout=10)
        log.log("[trace] step4: sqlite3 connected")
        con.execute("PRAGMA busy_timeout = 5000")
        log.log("[trace] step5: PRAGMA set, now create BrowserManagerV6")

        bm = BrowserManagerV6(action="followback")
        log.log("[trace] step6: BrowserManagerV6 instance created, calling bm.start()...")
        # ── ブラウザ起動 & ログイン確認 ──
        bm.start()
        log.log("[trace] step7: bm.start() returned")
        log.log("[trace] step8: calling hb.write + is_logged_in...")
        hb.write(phase="login_check")
        log.log("[trace] step9: calling bm.is_logged_in()")
        logged_in = bm.is_logged_in()
        log.log(f"[trace] step10: is_logged_in={logged_in}")
        if not logged_in:
            log.log("[ABORT] not logged in")
            con.close()
            result["stop_reason"] = "login_expired"
            return result

        page = bm.page
        log.log("[trace] step11: page OK, querying pending rows")

        # ── pending candidates を取得 ──
        rows = con.execute(
            "SELECT id, follower_user_id, follower_username FROM followback_queue "
            "WHERE status='pending' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        log.log(f"[trace] step12: pending_rows={len(rows)}")

        if not rows:
            # pending=0 → /my/followers を直接スキャンして pool 自己補充
            # (HOST の followback_source_feed に依存せず自律運転)
            log.log("[pool_empty] pending=0 → /my/followers 自動スキャン開始")
            hb.write(phase="pool_scan")
            inserted = _scan_my_followers(page, con, log, scan_limit=400)
            log.log(f"[pool_refresh] inserted={inserted} new pending candidates")
            if inserted == -1:
                # _scan_my_followers が login redirect を検出 → session expired
                result["stop_reason"] = "login_expired"
                return result
            if inserted > 0:
                rows = con.execute(
                    "SELECT id, follower_user_id, follower_username FROM followback_queue "
                    "WHERE status='pending' ORDER BY id DESC LIMIT ?", (limit,)
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
                # 2026-05-28 拡張: profile ページは「フォローをやめる」等も表示する可能性あり
                _ALREADY_SEL = (
                    'button[aria-label="フォロー中"], button:has-text("フォロー中"), '
                    'a:has-text("フォロー中"), [class*="following"], '
                    'button:has-text("フォローをやめる"), a:has-text("フォローをやめる"), '
                    'button:has-text("アンフォロー"), a:has-text("アンフォロー"), '
                    '[aria-label="フォローをやめる"], [aria-label="アンフォロー"]'
                )
                following_already = page.locator(_ALREADY_SEL).count() > 0
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
                # 2026-05-28: verify 方式を変更
                # fixed 5s wait の代わりに「再ナビゲートして最新状態を確認」(サーバー確定状態)
                # 2026-05-28 fix: 2000ms → 4000ms (Rakuten API の処理遅延対応)
                # 2000ms では API 未コミット状態で re-nav し verify_failed になるケースあり
                page.wait_for_timeout(4000)  # API 処理待ち (4s)
                # 再ナビゲートで fresh page (AngularJS キャッシュを回避)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                # 再ナビゲート後: フォロー中 / フォローをやめる / アンフォロー / フォローするボタン消滅
                followed = page.locator(_ALREADY_SEL).count() > 0
                if not followed:
                    still_on_room = "room.rakuten.co.jp" in page.url
                    follow_btn_gone = page.locator(
                        'button:has-text("フォローする"), a:has-text("フォローする"), '
                        '[aria-label="フォローする"]'
                    ).count() == 0
                    if follow_btn_gone and still_on_room:
                        followed = True
                        log.log(f"[verify] フォローするボタン消滅 → 成功判定 {user_id}")
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
        # 2026-05-27 CRITICAL fix: follow_log にも INSERT (pacer/SSOT 集計対象)
        # 旧版は followback_queue のみ update → daily_pacer の
        # "SELECT COUNT FROM follow_log WHERE action='followback'" が永久 0
        for sid in success_ids:
            con.execute(
                "UPDATE followback_queue SET status='completed', followed_at=? WHERE id=?",
                (now_iso, sid)
            )
            # follow_log にも記録 (SSOT 用)
            try:
                # follower_user_id を取得
                row = con.execute(
                    "SELECT follower_user_id FROM followback_queue WHERE id=?", (sid,)
                ).fetchone()
                if row and row[0]:
                    con.execute(
                        "INSERT OR IGNORE INTO follow_log "
                        "(target_user_id, source, action, status, followed_at) "
                        "VALUES (?, 'vm_v6_followback', 'followback', 'success', ?)",
                        (row[0], now_iso)
                    )
            except Exception as _le:
                log.log(f"[follow_log INSERT fail sid={sid}]: {_le}")
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
            if bm:
                bm.stop()
        except Exception:
            pass
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        log.log(f"=== FOLLOWBACK executor v6 end: {result} ===")

    return result
