#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOWBACK source feed — ROOM /my/followers から followback_queue へ enqueue

Phase 4b補完:
  1. Playwright で persistent profile の /my/followers を開く
  2. フォロワー一覧から user_id / username を収集
  3. follow_log を参照し「こちらが既にフォローしているか」判定
  4. followback_executor.enqueue_followers() で pending INSERT

Usage:
    python -m rakuten-room.bot.executor.followback_source_feed --limit 100
    python -m rakuten-room.bot.executor.followback_source_feed --limit 50 --headless

Stop reasons observed in stdout:
    - ok: 収集成功
    - not_logged_in: ログインページに redirect された
    - network_error: page.goto 失敗
    - parse_error: DOM selector 未ヒット
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # type: ignore

# 2026-05-05 Phase A-2: profile 分離。followback 機能用 profile を使用
CHROME_PROFILE = config.get_chrome_profile("followback")
CHROME_EXE = getattr(config, "CHROME_EXECUTABLE_PATH", None)

DB_PATH_V5 = BOT_DIR / "data" / "room_bot_v5.db"
FRESH_WINDOW_H = 48


def get_already_following_user_ids() -> set[str]:
    """follow_log から既にフォロー済の user_id 集合を返す"""
    if not DB_PATH_V5.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{DB_PATH_V5}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT target_user_id FROM follow_log WHERE status='success'")
        ids = {r[0] for r in cur.fetchall() if r[0]}
        con.close()
        return ids
    except Exception:
        return set()


def collect_followers(page, limit: int = 100) -> list[dict]:
    """/my/followers から followers を scroll 収集"""
    results: list[dict] = []
    seen: set[str] = set()

    # Try /my/followers first; if 404/redirect, fall back to /my/follower (singular)
    followers_urls = [
        "https://room.rakuten.co.jp/my/followers",
        "https://room.rakuten.co.jp/my/follower",
    ]
    landed_ok = False
    for url in followers_urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            landed_ok = True
            break
        except Exception as e:
            print(f"[network_error] goto {url} failed: {e}", flush=True)
            continue
    if not landed_ok:
        return []

    # SPA hydration: wait for either follower anchor or empty-state element
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(5)
    print(f"[page_landed] url={page.url[:120]}", flush=True)
    # Debug screenshot (useful for /my/followers vs /my/ diagnosis)
    try:
        from pathlib import Path as _P
        shot_dir = _P(__file__).resolve().parents[1] / "data" / "debug"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / f"followback_feed_{datetime.now().strftime('%H%M%S')}.png"
        page.screenshot(path=str(shot), full_page=False)
        print(f"[screenshot] {shot}", flush=True)
    except Exception as _e:
        print(f"[screenshot_err] {_e}", flush=True)
    try:
        title = page.title()
        print(f"[page_title] {title[:80]}", flush=True)
    except Exception:
        pass
    # Not-logged-in check
    if "login.account.rakuten.com" in page.url or "grp01.id" in page.url:
        print(f"[not_logged_in] redirected to {page.url[:80]}", flush=True)
        return []

    # Debug: count anchors on initial page and sample hrefs
    try:
        total_a = len(page.query_selector_all('a'))
        room_a = len(page.query_selector_all('a[href*="/room/"]'))
        my_a = len(page.query_selector_all('a[href*="/my/"]'))
        print(f"[dom_probe] total_a={total_a} room_a={room_a} my_a={my_a}", flush=True)
        # sample first 15 hrefs
        hrefs = page.eval_on_selector_all('a', 'els => els.slice(0,15).map(e => e.href)')
        for h in hrefs:
            print(f"  href: {h[:100]}", flush=True)
    except Exception as e:
        print(f"[dom_probe_err] {e}", flush=True)

    # ROOM user_id pattern: room_XXXXXXXXXXXX (after "/" segment)
    # A single user card contains links to /room_XXX/items, /room_XXX/{item_id}, etc.
    # We extract the room_XXX prefix as the user_id.
    import re as _re
    UID_RE = _re.compile(r'^room_[0-9a-f]{8,}$')
    MY_ACCOUNT_ID = getattr(config, "ROOM_ID", "")

    last_h = 0
    stuck_count = 0
    for scroll_i in range(40):
        try:
            # Prefer anchors that point into a user's room
            # Use /room_ (works for both relative and absolute hrefs)
            anchors = page.query_selector_all('a[href*="/room_"]')
            if scroll_i == 0 or scroll_i % 5 == 0:
                print(f"[selector_probe] iter={scroll_i} matched_anchors={len(anchors)} collected={len(results)}", flush=True)
            for a in anchors:
                href = (a.get_attribute("href") or "").strip()
                # Extract the "room_XXX" segment (path segment starting with room_)
                # handle relative /room_XXX/... and absolute https://.../room_XXX/...
                if "/room_" not in href:
                    continue
                tail = href.split("/room_", 1)[1].split("/")[0].split("?")[0]
                tail = "room_" + tail
                if not UID_RE.match(tail):
                    continue
                uid = tail
                if uid == MY_ACCOUNT_ID or uid in seen:
                    continue
                # username = anchor text or ARIA label fallback
                txt = (a.inner_text() or "").strip() or uid
                results.append({
                    "user_id": uid,
                    "username": txt[:60],
                    "is_following_us": True,           # from /my/followers list
                    "we_are_following": False,         # filled below
                    "previously_unfollowed": False,
                    "is_fresh": True,
                })
                seen.add(uid)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
            # Scroll: try multiple strategies
            h = page.evaluate("document.body.scrollHeight")
            if h == last_h:
                stuck_count += 1
                # Strategy 1: PageDown key
                try:
                    page.keyboard.press("End")
                    time.sleep(1)
                except Exception:
                    pass
                # Strategy 2: scroll the last visible user anchor into view
                if stuck_count >= 2 and anchors:
                    try:
                        anchors[-1].scroll_into_view_if_needed(timeout=2000)
                        time.sleep(1)
                    except Exception:
                        pass
                # Strategy 3: click もっと見る
                if stuck_count >= 3:
                    try:
                        more = page.query_selector('a:has-text("もっと見る"), button:has-text("もっと見る"), a:has-text("次へ"), button:has-text("次へ")')
                        if more and more.is_visible():
                            more.click()
                            time.sleep(2)
                    except Exception:
                        pass
                if stuck_count >= 5:
                    print(f"[scroll_stuck] aborting after {stuck_count} stalls", flush=True)
                    break
            else:
                stuck_count = 0
            last_h = h
            # Standard window scroll
            page.evaluate("window.scrollBy(0, 1800)")
            # And trigger any virtual scroll container
            page.evaluate("""
                const containers = document.querySelectorAll('[class*=\"scroll\"],[class*=\"list\"],[role=\"feed\"]');
                containers.forEach(c => { if (c.scrollHeight > c.clientHeight) c.scrollTop = c.scrollHeight; });
            """)
            time.sleep(1.5)
        except Exception as e:
            print(f"[scroll_error] iter={scroll_i}: {e}", flush=True)
            break

    # Mark we_are_following
    following = get_already_following_user_ids()
    for r in results:
        if r["user_id"] in following:
            r["we_are_following"] = True

    return results


def main():
    """
    2026-05-01 CEO指示改善: followback_source_multi へ委譲
    source_multi はマルチソース(/{id}/followers + 通知 + 2-hop)で検出率を大幅改善。
    既存 Task Scheduler コマンド変更不要のままアップグレード。
    """
    import subprocess as _sp
    import sys as _sys

    # Pass all args through to source_multi
    multi_script = Path(__file__).resolve().parent / "followback_source_multi.py"
    cmd = [_sys.executable, str(multi_script)] + _sys.argv[1:]
    print(f"[source_feed→multi] delegating to source_multi: {' '.join(cmd[2:])}", flush=True)
    try:
        result = _sp.run(cmd, timeout=900)
        return result.returncode
    except Exception as e:
        print(f"[delegate_err] {e}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
