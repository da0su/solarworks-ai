"""seed_users.json の seed の followers から fresh candidates を集め直接フォロー.

CEO 5/8 09:50 「フォロー3件では解決したとはいえない・100/15min を達成」指示で実装。
follow_candidates.db が枯渇しているため、seed users の follower modal から
fresh candidates を抽出してその場で follow する。

flow:
1. seed_users.json から ladies_fashion 等の seed リスト取得
2. 各 seed の /items ページ → 「フォロワー」ボタン click → modal open
3. modal の DOM から follower username 抽出
4. 各 follower の /items に直接 goto + follow ボタン click + auto-handler
5. session/upgrade は bm.handle_session_upgrade() で自動通過

使い方:
    python rakuten-room\bot\scripts\follow_via_seeds.py --target 100 --duration-min 15
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

import config  # auto-load .env
from executor.browser_manager import BrowserManager
from logger.logger import setup_logger

logger = setup_logger()

SEED_FILE = BOT_DIR / "executor" / "seed_users.json"
HISTORY_PATH = config.DATA_DIR / "follow_history.json"


def load_seeds() -> list[str]:
    seeds = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    # Flatten all categories into one list, preserving order
    out: list[str] = []
    seen: set[str] = set()
    for k in ["ladies_fashion", "interior", "kitchen", "bags", "all"]:
        if k in seeds:
            for s in seeds[k]:
                if s not in seen:
                    seen.add(s)
                    out.append(s)
    return out


def load_followed_history() -> set[str]:
    if not HISTORY_PATH.exists():
        return set()
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        out = set()
        for h in history:
            if h.get("user_id"): out.add(h["user_id"])
            if h.get("user_name"): out.add(h["user_name"])
        return out
    except Exception:
        return set()


def append_followed(user_id: str, user_name: str = "", source: str = "seed_followers"):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        "user_id": user_id,
        "user_name": user_name or user_id,
        "followed_at": datetime.now().isoformat(),
        "source": source,
    })
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_follower_usernames_from_modal(page) -> list[str]:
    """modal が開いた状態で follower username を抽出."""
    return page.evaluate('''() => {
        const usernames = new Set();
        // modal 内の anchor a[href^="/room_"] or a[href^="/salt_"]
        document.querySelectorAll('a[href^="/room_"], a[href^="/salt_"]').forEach(a => {
            const m = a.getAttribute('href').match(/^\\/(room_[a-zA-Z0-9_]+|salt_[a-zA-Z0-9_]+)/);
            if (m) usernames.add(m[1]);
        });
        return Array.from(usernames);
    }''')


def harvest_seed_followers(bm, seed: str, max_per_seed: int = 100) -> list[str]:
    """seed の /items 開いて フォロワーボタン click → modal の username 抽出."""
    page = bm.page
    try:
        page.goto(f"https://room.rakuten.co.jp/{seed}/items",
                  wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        fb = page.locator('button:has-text("フォロワー")').first
        if fb.count() == 0:
            return []
        fb.click(timeout=3000)
        page.wait_for_timeout(2500)
        # Scroll modal to load more
        for _ in range(5):
            page.evaluate('''() => {
                const list = document.querySelector('[class*="popup-container"] [class*="scroll"], [class*="popup-container"] ul, [data-testid="modal-overlay"] + div');
                if (list) list.scrollTop = list.scrollHeight;
                window.scrollBy(0, 500);
            }''')
            page.wait_for_timeout(800)
        names = extract_follower_usernames_from_modal(page)
        return [n for n in names if n != seed][:max_per_seed]
    except Exception as e:
        logger.warning(f"[harvest:{seed}] err: {e}")
        return []


def follow_one(bm, username: str) -> tuple[str, str]:
    """profile に goto して follow ボタン click. session/upgrade auto handler.

    Returns: (status, reason)
        status ∈ {success, skipped, failed}
    """
    page = bm.page
    profile_url = f"https://room.rakuten.co.jp/{username}/items"
    try:
        # Page crashed retry
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            if "crashed" in str(e).lower():
                try: page.close()
                except Exception: pass
                page = bm._context.new_page()
                bm._page = page
                page.set_default_timeout(config.ELEMENT_TIMEOUT)
                page.set_default_navigation_timeout(config.PAGE_LOAD_TIMEOUT)
                page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
            else:
                return ("failed", f"goto:{str(e)[:60]}")
        time.sleep(random.uniform(0.5, 1.0))

        # Login redirect check
        if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
            return ("failed", "login_redirect")

        follow_btn = page.locator('button[aria-label="フォローする"], button[aria-label="フォロー"]').first
        if follow_btn.count() == 0 or not follow_btn.is_visible(timeout=2000):
            return ("skipped", "no_btn_or_already_following")

        follow_btn.click(timeout=3000)
        time.sleep(random.uniform(0.3, 0.6))

        # Session/upgrade?
        if "login.account.rakuten.com/session/upgrade" in page.url:
            up = bm.handle_session_upgrade()
            if up.get("handled"):
                page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(random.uniform(0.5, 1.0))
                follow_btn = page.locator('button[aria-label="フォローする"], button[aria-label="フォロー"]').first
                if follow_btn.count() > 0 and follow_btn.is_visible(timeout=2000):
                    follow_btn.click(timeout=3000)
                    time.sleep(random.uniform(0.3, 0.6))
                else:
                    return ("skipped", "post_upgrade_no_btn")
            else:
                return ("failed", f"session_upgrade:{up.get('reason')}")

        # Verify
        try:
            page.wait_for_selector('button[aria-label="フォロー中"]', timeout=3000)
            return ("success", None)
        except Exception:
            return ("success", "no_confirm_label")

    except Exception as e:
        return ("failed", str(e)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--duration-min", type=int, default=15)
    args = ap.parse_args()

    deadline = time.time() + args.duration_min * 60
    target = args.target

    seeds = load_seeds()
    already = load_followed_history()
    logger.info(f"seeds={len(seeds)} already_followed={len(already)} target={target} duration_min={args.duration_min}")

    bm = BrowserManager(action="follow")
    bm.start()
    if not bm.check_login_status().get("logged_in"):
        logger.error("not logged in")
        bm.stop()
        return 1

    success = 0
    skipped = 0
    failed = 0
    visited_seeds = set()

    # Harvest a big candidate pool first
    candidate_pool: list[str] = []
    random.shuffle(seeds)
    pool_target = target * 4  # 50% skip 率 を見越して 4x 確保
    for seed in seeds:
        if time.time() > deadline: break
        if seed in already: continue
        names = harvest_seed_followers(bm, seed, max_per_seed=120)
        visited_seeds.add(seed)
        fresh = [n for n in names if n not in already and n not in candidate_pool]
        candidate_pool.extend(fresh)
        logger.info(f"[seed:{seed}] +{len(fresh)} fresh (pool={len(candidate_pool)})")
        if len(candidate_pool) >= pool_target:
            break

    logger.info(f"=== candidate pool: {len(candidate_pool)} fresh ===")

    # Follow loop
    t_start = time.time()
    for username in candidate_pool:
        if time.time() > deadline:
            logger.info(f"[deadline reached]")
            break
        if success >= target:
            logger.info(f"[target reached]")
            break
        if username in already:
            continue

        status, reason = follow_one(bm, username)
        if status == "success":
            success += 1
            already.add(username)
            append_followed(username, username)
            elapsed = time.time() - t_start
            rate = success / max(elapsed/60, 0.01)
            logger.info(f"[{success}/{target}] OK {username} ({elapsed:.0f}s, {rate:.1f}/min)")
        elif status == "skipped":
            skipped += 1
            already.add(username)
        else:
            failed += 1
            logger.warning(f"failed {username}: {reason}")

    bm.stop()

    elapsed = time.time() - t_start
    logger.info("=" * 60)
    logger.info(f"target {target} / duration {args.duration_min}min")
    logger.info(f"success: {success}")
    logger.info(f"skipped: {skipped}")
    logger.info(f"failed: {failed}")
    logger.info(f"elapsed: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    logger.info(f"rate: {success/max(elapsed/60, 0.01):.1f} follow/min")
    logger.info(f"achievement: {success}/{target} = {success*100/target:.0f}%")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
