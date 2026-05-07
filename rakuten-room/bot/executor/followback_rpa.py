#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOWBACK RPA click layer (Phase 4b).

Given a list of (user_id, username) tuples, open each profile page and click
the follow button. Mirrors follow_executor.py's pattern (React selector
`button[aria-label="フォロー"]`, verify by `button[aria-label="フォロー中"]`).

This layer is called from followback_executor.execute() when --execute is
invoked. It returns per-user results; the caller handles DB state transitions.

Design notes:
- Uses main-PC storage_state (data/state/storage_state.json) — same path as
  POST/LIKE. Followback is a mainPC action when we drive it locally; the VB
  variant will call this same module via remote launcher.
- login_redirect → immediately aborts the whole batch (session invalid).
- follow button absent on profile → skipped (already followed or account deleted).
- Click → wait up to 3s for "フォロー中" label → success else failed.
"""
from __future__ import annotations

import io
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                    errors="replace", line_buffering=True)

BOT_DIR = Path(__file__).resolve().parent.parent
STORAGE_STATE = BOT_DIR / "data" / "state" / "storage_state.json"


def _human_delay(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def do_followback_batch(targets: list[dict], headless: bool = False,
                        max_consecutive_failures: int = 5) -> dict:
    """
    targets: [{'user_id': ..., 'username': ..., 'queue_id': ...}, ...]
    Returns: {
        'session_id': str, 'processed': N, 'success': N, 'failed': N, 'skipped': N,
        'aborted': bool, 'abort_reason': str or None,
        'per_user': [{'user_id','username','queue_id','status','reason'}, ...]
    }
    """
    from playwright.sync_api import sync_playwright

    session_id = f"fb-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    result = {
        "session_id": session_id,
        "processed": 0, "success": 0, "failed": 0, "skipped": 0,
        "aborted": False, "abort_reason": None,
        "per_user": [],
    }

    if not STORAGE_STATE.exists():
        result["aborted"] = True
        result["abort_reason"] = "storage_state_missing"
        return result

    with sync_playwright() as pw:
        browser = None
        for attempt in (
            {"channel": "chrome", "headless": headless,
             "args": ["--disable-blink-features=AutomationControlled"],
             "ignore_default_args": ["--enable-automation"]},
            {"channel": "msedge", "headless": headless},
            {"headless": headless},
        ):
            try:
                browser = pw.chromium.launch(**attempt)
                break
            except Exception:
                continue
        if browser is None:
            result["aborted"] = True
            result["abort_reason"] = "chromium_launch_failed"
            return result

        ctx = browser.new_context(storage_state=str(STORAGE_STATE))
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.new_page()

        # Session validity probe: visit /home, check not redirected to login
        # 2026-04-23: bumped timeout 20s→45s; room SPA can be slow after heavy POST batch
        probe_ok = False
        for probe_attempt in range(2):
            try:
                page.goto("https://room.rakuten.co.jp/home",
                          wait_until="domcontentloaded", timeout=45000)
                if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
                    result["aborted"] = True
                    result["abort_reason"] = "session_invalid_login_redirect"
                    browser.close()
                    return result
                probe_ok = True
                break
            except Exception as e:
                last_err = str(e)[:80]
                time.sleep(3)
                continue
        if not probe_ok:
            result["aborted"] = True
            result["abort_reason"] = f"probe_failed:{last_err}"
            browser.close()
            return result

        consecutive_failures = 0

        for t in targets:
            uid = t.get("user_id")
            uname = t.get("username") or uid
            qid = t.get("queue_id")
            user_result = {"user_id": uid, "username": uname, "queue_id": qid,
                            "status": None, "reason": None}

            profile_url = f"https://room.rakuten.co.jp/{uname}/items"
            try:
                # 2026-04-23: bumped 15s→40s; ROOM SPA is slow, was timing out
                page.goto(profile_url, wait_until="domcontentloaded", timeout=40000)
                _human_delay(2.0, 4.0)

                if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
                    # session dropped mid-batch
                    user_result.update({"status": "aborted", "reason": "login_redirect"})
                    result["per_user"].append(user_result)
                    result["aborted"] = True
                    result["abort_reason"] = "session_invalid_mid_batch"
                    break

                # 2026-04-23 ROOM UI updated: actual button label is "フォローする"
                # (not "フォロー"). Accept both for forward compat.
                follow_btn = page.locator(
                    'button[aria-label="フォローする"], button[aria-label="フォロー"]'
                ).first
                has_btn = False
                try:
                    has_btn = follow_btn.count() > 0 and follow_btn.is_visible(timeout=2000)
                except Exception:
                    has_btn = False

                if not has_btn:
                    user_result.update({"status": "skipped", "reason": "no_follow_button_or_already_followed"})
                    result["skipped"] += 1
                    result["per_user"].append(user_result)
                    consecutive_failures = 0
                    _human_delay(3.0, 6.0)
                    continue

                follow_btn.click(timeout=3000)
                _human_delay(0.5, 1.5)

                # 2026-05-07 Plan v6 (FB session/upgrade hardening):
                # 楽天の新方針: follow click 後 login.account.rakuten.com/session/upgrade
                # に強制 redirect される (POST と同じ仕様)。
                # RAKUTEN_LOGIN_PASSWORD が .env にあれば auto-fill で通過し、
                # 元の profile に戻って follow 完了確認を続ける。
                if "login.account.rakuten.com/session/upgrade" in page.url:
                    import os
                    pw = os.environ.get("RAKUTEN_LOGIN_PASSWORD") \
                        or os.environ.get("RAKUTEN_PASSWORD")
                    if pw:
                        try:
                            pw_in = page.locator('input[type="password"]').first
                            pw_in.wait_for(state="visible", timeout=5000)
                            pw_in.fill(pw)
                            for sel in ['button:has-text("次へ")',
                                        'button[type="submit"]',
                                        'input[type="submit"]']:
                                btn2 = page.locator(sel)
                                if btn2.count() > 0 and btn2.first.is_visible():
                                    btn2.first.click()
                                    break
                            else:
                                pw_in.press("Enter")
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                            page.wait_for_timeout(2000)
                            # 通過後: 元の profile に戻って follow 完了確認
                            if "session/upgrade" not in page.url:
                                page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
                                page.wait_for_timeout(2000)
                        except Exception as e:
                            user_result.update({"status": "failed",
                                                "reason": f"session_upgrade_fail:{str(e)[:60]}"})
                            result["failed"] += 1
                            consecutive_failures += 1
                            result["per_user"].append(user_result)
                            if consecutive_failures >= max_consecutive_failures:
                                result["aborted"] = True
                                result["abort_reason"] = f"consecutive_failures={consecutive_failures}"
                                break
                            _human_delay(5.0, 12.0)
                            continue
                    else:
                        user_result.update({"status": "failed",
                                            "reason": "session_upgrade_no_password"})
                        result["failed"] += 1
                        consecutive_failures += 1
                        result["per_user"].append(user_result)
                        if consecutive_failures >= max_consecutive_failures:
                            result["aborted"] = True
                            result["abort_reason"] = "consecutive_failures_session_upgrade_no_password"
                            break
                        _human_delay(5.0, 12.0)
                        continue

                # Confirm: button label changes to "フォロー中" / "フォローを外す"
                try:
                    page.wait_for_selector(
                        'button[aria-label="フォロー中"], button[aria-label="フォローを外す"]',
                        timeout=5000,
                    )
                    user_result.update({"status": "success", "reason": None})
                    result["success"] += 1
                    consecutive_failures = 0
                except Exception:
                    user_result.update({"status": "failed", "reason": "no_confirm_label"})
                    result["failed"] += 1
                    consecutive_failures += 1

            except Exception as e:
                user_result.update({"status": "failed", "reason": f"exception:{str(e)[:80]}"})
                result["failed"] += 1
                consecutive_failures += 1

            result["processed"] += 1
            result["per_user"].append(user_result)

            if consecutive_failures >= max_consecutive_failures:
                result["aborted"] = True
                result["abort_reason"] = f"consecutive_failures={consecutive_failures}"
                break

            # Inter-user delay (human-like)
            _human_delay(5.0, 12.0)

        browser.close()

    return result


if __name__ == "__main__":
    # CLI smoke test: python -m rakuten-room.bot.executor.followback_rpa <uname1> [<uname2> ...]
    if len(sys.argv) < 2:
        print("usage: python -m rakuten-room.bot.executor.followback_rpa <username> [<username2> ...]")
        sys.exit(1)
    targets = [{"user_id": u, "username": u, "queue_id": None} for u in sys.argv[1:]]
    out = do_followback_batch(targets, headless=False)
    print(json.dumps(out, ensure_ascii=False, indent=2))
