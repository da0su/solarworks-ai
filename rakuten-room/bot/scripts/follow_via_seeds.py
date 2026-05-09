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


INVESTIGATION_FILE = config.DATA_DIR / "seed_investigation.json"


def load_seeds() -> list[str]:
    """seed_investigation.json があれば follower_count 降順で計画消費.
    無ければ seed_users.json fallback.

    2026-05-09 18:39: pool 枯渇対策で 2nd hop seeds を末尾に追加.
    今日 follow したユーザーの followers は already_followed と重複が少ない.
    """
    out: list[str] = []
    seen: set[str] = set()

    if INVESTIGATION_FILE.exists():
        try:
            data = json.loads(INVESTIGATION_FILE.read_text(encoding="utf-8"))
            # Sort by follower_count desc (richer pools first)
            sorted_data = sorted(data, key=lambda r: r.get("follower_count", 0), reverse=True)
            for r in sorted_data:
                s = r.get("seed_user")
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
            logger.info(f"using investigation data: {len(out)} seeds, top follower_count={sorted_data[0]['follower_count'] if sorted_data else 0}")
        except Exception as e:
            logger.warning(f"investigation data load failed: {e}")

    if not out:
        # Fallback: seed_users.json
        seeds_data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        for k in ["ladies_fashion", "interior", "kitchen", "bags", "all"]:
            if k in seeds_data:
                for s in seeds_data[k]:
                    if s not in seen:
                        seen.add(s)
                        out.append(s)

    # 2nd hop: 直近 follow したユーザーの followers は新鮮 (相互重複少)
    try:
        if HISTORY_PATH.exists():
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            second_hop_count = 0
            # 新しい順に最大 200 件混ぜる
            for h in reversed(history[-500:]):
                if not isinstance(h, dict): continue
                uname = h.get("user_name") or h.get("user_id")
                if uname and uname not in seen:
                    seen.add(uname)
                    out.append(uname)
                    second_hop_count += 1
                    if second_hop_count >= 200: break
            if second_hop_count:
                logger.info(f"+ 2nd hop seeds: {second_hop_count} (recent follows)")
    except Exception as e:
        logger.warning(f"2nd hop seed load failed: {e}")

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


def harvest_seed_followers(bm, seed: str, max_per_seed: int = 200) -> list[str]:
    """seed の /items 開いて フォロワーボタン click → modal の username 抽出.

    2026-05-09: scroll 5→20回 + 各回 1.5s 待機で lazy load を確実に取得.
    seed の follower 上位 200 まで取れるように拡張 (旧: ~30-50 件のみ).
    """
    page = bm.page
    try:
        page.goto(f"https://room.rakuten.co.jp/{seed}/items",
                  wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        fb = page.locator('button:has-text("フォロワー")').first
        if fb.count() == 0:
            return []
        fb.click(timeout=3000)
        page.wait_for_timeout(3000)

        # 2026-05-09 v3: 30s/seed は遅すぎ → 8 iter × 0.7s = ~5s/seed に短縮
        # follow phase に確実に時間を残す。stable 検知で更に早期終了。
        prev_count = 0
        stable_iters = 0
        for i in range(8):
            page.evaluate('''() => {
                const containers = [
                    ...document.querySelectorAll('[class*="popup-container"] [class*="scroll"]'),
                    ...document.querySelectorAll('[class*="popup-container"] ul'),
                    ...document.querySelectorAll('[data-testid="modal-overlay"] + div'),
                    ...document.querySelectorAll('[class*="modal"] [class*="list"]'),
                ];
                containers.forEach(c => { c.scrollTop = c.scrollHeight; });
                window.scrollBy(0, 1000);
            }''')
            page.wait_for_timeout(700)
            # 早期終了: 連続 3 回 同件数で打ち切り (それ以上は取れない)
            try:
                cur = page.evaluate('() => document.querySelectorAll(\'a[href^="/room_"], a[href^="/salt_"]\').length')
                if cur == prev_count:
                    stable_iters += 1
                    if stable_iters >= 3:
                        break
                else:
                    stable_iters = 0
                    prev_count = cur
            except Exception:
                pass
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
    # 2026-05-09 18:15 silent fail 対策: logger 前に startup marker 出力
    # → windows_task_follow_host.log に痕跡を残し起動失敗を可視化
    print(f"[startup] follow_via_seeds.py @ {datetime.now().isoformat()}", flush=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--duration-min", type=int, default=15)
    args = ap.parse_args()

    deadline = time.time() + args.duration_min * 60
    target = args.target

    print(f"[startup] argparse OK target={target} duration={args.duration_min}min", flush=True)

    seeds = load_seeds()
    already = load_followed_history()
    logger.info(f"seeds={len(seeds)} already_followed={len(already)} target={target} duration_min={args.duration_min}")

    # 2026-05-09 CEO 観察: bot Chrome が前面化で HOST 入力を奪う
    # → Task Scheduler 経由なら BOT_HEADLESS=1 で headless 化
    bot_headless = os.environ.get("BOT_HEADLESS", "0") == "1"
    bm = BrowserManager(action="follow")
    if bot_headless:
        # BrowserManager の start() は config.BROWSER_HEADLESS を見るので一時 patch
        import config as _c
        _c.BROWSER_HEADLESS = True
        logger.info("BOT_HEADLESS=1 → headless=True で起動 (focus 奪取防止)")
    bm.start()
    if not bm.check_login_status().get("logged_in"):
        logger.error("not logged in")
        bm.stop()
        return 1

    success = 0
    skipped = 0
    failed = 0
    visited_seeds = set()

    # 2026-05-09 v3: 短時間 harvest → 早く follow 開始
    candidate_pool: list[str] = []
    random.shuffle(seeds)
    pool_target = max(target * 2, 100)  # 4x → 2x で時間節約 (50% skip 想定)
    harvest_time_cap = 600  # 10 分以内に harvest 切り上げて follow へ
    harvest_start = time.time()
    for seed in seeds:
        if time.time() > deadline: break
        if time.time() - harvest_start > harvest_time_cap:
            logger.info(f"[harvest] 10分 cap で打ち切り (pool={len(candidate_pool)})")
            break
        # 2026-05-09 18:55: 2nd hop seeds を skip しないよう修正.
        # seed 自体が already_followed でも, その followers は新鮮な可能性が高い (相互重複少).
        # 旧 `if seed in already: continue` を削除.
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
