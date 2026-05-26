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


def extract_follower_usernames_from_modal(page) -> dict:
    """フォロワー username を抽出 (modal or /followers page 両対応).

    2026-05-10 真因対応:
    - 旧 code は document 全体 (ページ header の "自分のprofile" リンク=ROOM_ID
      も含む) から拾っていた → modal 未 open でも 1 件返す誤動作
    - 修正: URL が /followers なら page 全体 minus header. modal なら scope.
    """
    return page.evaluate('''() => {
        const usernames = new Set();
        const diag = {url: window.location.href, mode: 'none', total_room_links: 0};
        diag.total_room_links = document.querySelectorAll('a[href^="/room_"], a[href^="/salt_"]').length;

        let scope = null;

        // (1) /followers URL → page main area
        if (window.location.pathname.includes('/followers')) {
            // page 全体 minus header / nav
            scope = document.querySelector('main') || document.querySelector('#__next') || document.body;
            diag.mode = 'followers-page';
        }
        // (2) Modal detection
        if (!scope) {
            const candidates = [
                ['[class*="popup-container"]', 'popup-container'],
                ['[role="dialog"]', 'dialog'],
                ['[data-testid*="modal-overlay"]', 'testid-overlay'],
                ['[class*="modal-content"]', 'modal-content'],
                ['[aria-modal="true"]', 'aria-modal'],
                ['[class*="modal"][aria-hidden="false"]', 'modal-aria'],
            ];
            for (const [sel, name] of candidates) {
                const el = document.querySelector(sel);
                if (el) { scope = el; diag.mode = name; break; }
            }
        }

        if (!scope) {
            diag.body_class = (document.body.className || '').substring(0, 100);
            return {names: [], diag};
        }
        diag.scope_class = (scope.className || '').substring(0, 80);
        scope.querySelectorAll('a[href^="/room_"], a[href^="/salt_"]').forEach(a => {
            const m = a.getAttribute('href').match(/^\\/(room_[a-zA-Z0-9_]+|salt_[a-zA-Z0-9_]+)/);
            if (m) usernames.add(m[1]);
        });
        return {names: Array.from(usernames), diag};
    }''')


def harvest_seed_followers(bm, seed: str, max_per_seed: int = 200) -> list[str]:
    """seed の フォロワー modal から username を抽出.

    2026-05-10 真因確定 + 修正:
    手動テスト (chrome_profile_post で /room_2389d5576a/items 直接実行) で判明:
    - modal は click で正しく開く (popup-container--*)
    - **初期 anchors=0 (空)**, scroll 約 14-15 iter (~7-8s) で lazy load 発火
    - 48 anchors → 16 unique users 抽出可能
    - 旧 8 iter × 0.7s (5s) では lazy load 起動前で empty → 全 seed pool=0 になっていた

    対応: scroll 12 iter × 0.7s = 8.4s (lazy load 確実発火) + early stop.
    """
    page = bm.page
    try:
        url_items = f"https://room.rakuten.co.jp/{seed}/items"
        # 2026-05-26 [REVERT]: 10s → 15s
        # 朝の #439 修正 (20s→10s) で全 seed が timeout → today=0 達成 → 即時 revert.
        # 楽天ROOM は通常 5-8s 程度だが、HOST 側ネットワークで 10-13s かかるケースが頻発.
        # 15s が妥当 (timeout も dead seed 検知も両立).
        page.goto(url_items, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # フォロワー button click (modal が開く)
        fb = page.locator('button:has-text("フォロワー"):not(:has-text("フォロー中"))').first
        if fb.count() == 0:
            return []
        fb.click(timeout=3000)
        page.wait_for_timeout(2500)

        # 2026-05-10: 12 iter × 0.7s = 8.4s. 14-15 iter で lazy load 発火確認済.
        # 12 iter で十分余裕 (load 後 stable で early stop)
        prev_count = 0
        stable_iters = 0
        for i in range(15):
            page.evaluate('''() => {
                // 2026-05-10: 真のスクロール target = popup 内の overflowY:auto 要素
                // 旧 selector では当たらず. 動的 detect.
                const popup = document.querySelector('[class*="popup-container"]');
                if (popup) {
                    popup.querySelectorAll('*').forEach(el => {
                        const cs = window.getComputedStyle(el);
                        if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.clientHeight > 100) {
                            el.scrollTop = el.scrollHeight;
                        }
                    });
                }
                window.scrollBy(0, 1000);
            }''')
            page.wait_for_timeout(700)
            # 早期終了: modal scope 内の anchor 数で判定 (header 等の document 全体ではない)
            # かつ少なくとも 6 iter は強制 (lazy load 発火待ち)
            try:
                cur = page.evaluate('''() => {
                    const popup = document.querySelector('[class*="popup-container"]');
                    return popup ? popup.querySelectorAll('a[href^="/room_"], a[href^="/salt_"]').length : 0;
                }''')
                if cur == prev_count:
                    stable_iters += 1
                    # iter 6 以下は break しない (lazy load が iter 14 前後)
                    if stable_iters >= 3 and i >= 6 and prev_count > 0:
                        break
                else:
                    stable_iters = 0
                    prev_count = cur
            except Exception:
                pass
        result = extract_follower_usernames_from_modal(page)
        names = result.get("names", []) if isinstance(result, dict) else []
        diag = result.get("diag", {}) if isinstance(result, dict) else {}
        own_id = getattr(config, "ROOM_ID", "")
        if not names and diag.get("mode") == "none":
            logger.info(f"[harvest:{seed}] no scope found. url={diag.get('url','?')} total_anchors={diag.get('total_room_links',0)}")
        else:
            logger.debug(f"[harvest:{seed}] mode={diag.get('mode')} anchors={diag.get('total_room_links','?')}")
        return [n for n in names if n != seed and n != own_id][:max_per_seed]
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
        # 2026-05-26 [REVERT]: 10s → 15s (follow click も 10s だと全 timeout)
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
        if follow_btn.count() == 0 or not follow_btn.is_visible(timeout=1500):
            return ("skipped", "no_btn_or_already_following")

        follow_btn.click(timeout=2000)
        # 2026-05-12 CEO 残月達成プラン: 待機 0.3-0.6 → 0.1-0.3 (高速化)
        time.sleep(random.uniform(0.1, 0.3))

        # Session/upgrade?
        if "login.account.rakuten.com/session/upgrade" in page.url:
            up = bm.handle_session_upgrade()
            if up.get("handled"):
                page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
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
    ap.add_argument("--ignore-pacer", action="store_true", help="daily_pacer の自動 stop/target を無視")
    args = ap.parse_args()

    deadline = time.time() + args.duration_min * 60
    target = args.target

    # 2026-05-14 CEO 指示「目標多すぎても少なすぎても NG・自動是正」:
    # daily_pacer に問合せ・action=stop なら exit, run なら per_cycle_target で上書き.
    if not args.ignore_pacer:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
            from shared.daily_pacer import get_pace_directive
            d = get_pace_directive("FOLLOW")
            logger.info(f"[pacer] {d['fn']} target={d['target']} actual={d['actual']} expected_now={d['expected_now']} action={d['action']} reason={d['reason']}")
            if d["action"] == "stop":
                logger.info(f"[pacer] stop: {d['reason']}")
                return 0
            # per_cycle_target でこの cycle の上限を上書き
            target = max(1, d["per_cycle_target"])
            logger.info(f"[pacer] target overridden by pacer: {args.target} → {target}")
        except Exception as e:
            logger.warning(f"[pacer] failed (fallback to args.target {args.target}): {e}")

    print(f"[startup] argparse OK target={target} duration={args.duration_min}min", flush=True)

    # Codex 5/16 9回目 review 反映 #7: rate gate を ブラウザ起動前 にチェック (無駄リソース回避)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    try:
        from shared.follow_rate_gate import (
            get_status as _rate_status,
            update_heartbeat as _hb_update,
            GateError,
            EXIT_RATE_LIMITED_NOOP,
            EXIT_RATE_LIMITED_PARTIAL,
            EXIT_GATE_INIT_ERROR,
            EXIT_CIRCUIT_BREAKER,  # v4 (Codex 29回目 #4)
            record_failure as _rate_record_failure,
            compute_backoff_sec as _rate_backoff,
        )
    except Exception as e:
        # import 自体が失敗 → fail-closed (Codex #2 反映で import 例外は無いはずだが念のため)
        logger.error(f"[rate_gate] import failed (fail-closed): {e}")
        return 21

    try:
        _initial_gate = _rate_status()
        logger.info(
            f"[rate_gate v4] min={_initial_gate['last_minute']}/{_initial_gate['minute_cap']}, "
            f"hour={_initial_gate['last_hour']}/{_initial_gate['hourly_cap']}, "
            f"day={_initial_gate['today']}/{_initial_gate['daily_cap']}, "
            f"circuit_open={_initial_gate['circuit_open']}"
        )
        if not _initial_gate["can_follow"]:
            # circuit breaker か rate cap か区別 (v4)
            if _initial_gate.get("circuit_open"):
                logger.error(
                    f"[rate_gate] CIRCUIT BREAKER OPEN before start: "
                    f"remaining {_initial_gate['circuit_remaining_sec']}s → noop"
                )
                _hb_update("circuit_breaker_open_at_start", problem=True,
                           circuit_remaining_sec=_initial_gate["circuit_remaining_sec"])
                return EXIT_CIRCUIT_BREAKER
            logger.warning(
                f"[rate_gate] RATE LIMITED before browser start: "
                f"min={_initial_gate['last_minute']}/{_initial_gate['minute_cap']}, "
                f"hour={_initial_gate['last_hour']}/{_initial_gate['hourly_cap']}, "
                f"day={_initial_gate['today']}/{_initial_gate['daily_cap']} "
                f"→ noop exit ({EXIT_RATE_LIMITED_NOOP})"
            )
            _hb_update("rate_limited_noop", problem=False,
                       minute_count=_initial_gate["last_minute"],
                       hourly_count=_initial_gate["last_hour"],
                       daily_count=_initial_gate["today"])
            return EXIT_RATE_LIMITED_NOOP
    except GateError as e:
        logger.error(f"[rate_gate] init error (fail-closed): {e}")
        _hb_update("gate_init_error", problem=True, error=str(e)[:200])
        return EXIT_GATE_INIT_ERROR

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
    # 2026-05-10: login_check intermittent timeout 対策 - 1 回 retry
    login_ok = False
    for attempt in range(2):
        try:
            if bm.check_login_status().get("logged_in"):
                login_ok = True
                break
        except Exception as e:
            logger.warning(f"[login_check attempt {attempt+1}] err: {e}")
        if attempt == 0:
            logger.info("login_check failed, waiting 10s and retrying...")
            time.sleep(10)
    if not login_ok:
        logger.error("not logged in (after 2 attempts)")
        # 2026-05-24: CEO 通知 (approval_needed) + 未ログイン理由を明示
        try:
            from notifier import notify, NotifyType
            notify(NotifyType.APPROVAL,
                   detail="フォローBOT: 未ログイン (cookie 期限切れ？) → python run.py login を実行してください")
        except Exception:
            pass
        bm.stop()
        return 1

    success = 0
    skipped = 0
    failed = 0
    consecutive_fails = 0  # v4 (Codex 29回目 #4): 指数バックオフ + circuit breaker 用
    visited_seeds = set()

    # 2026-05-12 残月達成プラン (CEO 指示): harvest 短縮 + pool target 軽量化で
    # follow phase に最大時間配分. 13 min trigger で 50-80 件 follow を狙う.
    candidate_pool: list[str] = []
    random.shuffle(seeds)
    pool_target = max(target, 40)  # 軽量化 (skip 50% 想定で 目標 + 余裕分)
    # 2026-05-26 改善: 3分 → 6分 (seed枯渇対策・2倍のseedを走査して fresh pool を拡大)
    harvest_time_cap = 360  # 6 分 (旧 3分・seed枯渇で fresh=0 が頻発していたため延長)
    harvest_start = time.time()
    # 2026-05-10 CEO 指示: harvest 結果を seed_investigation.json に incremental 反映
    seed_overlap_updates: dict[str, dict] = {}  # seed_user → {harvested, overlap}
    for seed in seeds:
        if time.time() > deadline: break
        if time.time() - harvest_start > harvest_time_cap:
            logger.info(f"[harvest] 6分 cap で打ち切り (pool={len(candidate_pool)})")
            break
        # 2026-05-09 18:55: 2nd hop seeds を skip しないよう修正.
        names = harvest_seed_followers(bm, seed, max_per_seed=120)
        visited_seeds.add(seed)
        fresh = [n for n in names if n not in already and n not in candidate_pool]
        candidate_pool.extend(fresh)
        sample = names[:3] if names else []
        # 2026-05-10: F (overlap) 計算 - このフォロワーのうち私が既 follow している数
        overlap = sum(1 for n in names if n in already)
        seed_overlap_updates[seed] = {
            "harvested": len(names), "followed_overlap": overlap, "fresh": len(fresh)
        }
        logger.info(f"[seed:{seed}] harvested={len(names)} fresh={len(fresh)} overlap={overlap} sample={sample} (pool={len(candidate_pool)})")
        if len(candidate_pool) >= pool_target:
            break

    # 2026-05-10 CEO 指示: harvest 結果を seed_investigation.json に書込み戻し
    try:
        if INVESTIGATION_FILE.exists() and seed_overlap_updates:
            data = json.loads(INVESTIGATION_FILE.read_text(encoding="utf-8"))
            updated = 0
            for r in data:
                su = r.get("seed_user")
                if su and su in seed_overlap_updates:
                    upd = seed_overlap_updates[su]
                    r["followed_overlap"] = upd["followed_overlap"]
                    r["last_harvest_at"] = datetime.now().isoformat(timespec="seconds")
                    r["last_harvested_count"] = upd["harvested"]
                    updated += 1
            INVESTIGATION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[seed_investigation] updated {updated} rows with overlap data")
    except Exception as e:
        logger.warning(f"seed_investigation update failed: {e}")

    logger.info(f"=== candidate pool (1st hop): {len(candidate_pool)} fresh ===")

    # 2026-05-26 改善3: BFS 2nd hop — 1st hop で pool が不足している場合に追加 harvest
    # fresh candidates をそのまま 2nd hop seed として使い、そのフォロワーを掘る
    # → 既存 447 seeds が枯渇していても指数的に新規 pool を獲得可能
    BFS_CAP_SEC = 120  # 2nd hop に最大 2分追加
    if len(candidate_pool) < pool_target and len(candidate_pool) > 0:
        hop2_seeds = list(candidate_pool[:20])  # fresh の先頭 20 名を 2nd hop seed に
        bfs_start = time.time()
        bfs_added = 0
        for hop2_seed in hop2_seeds:
            if time.time() - bfs_start > BFS_CAP_SEC:
                logger.info(f"[BFS hop2] 2分 cap で打ち切り (added={bfs_added} pool={len(candidate_pool)})")
                break
            names2 = harvest_seed_followers(bm, hop2_seed, max_per_seed=100)
            fresh2 = [n for n in names2 if n not in already and n not in candidate_pool]
            added = fresh2[:30]  # 1 seed あたり最大 30 名
            candidate_pool.extend(added)
            bfs_added += len(added)
            if added:
                logger.info(f"[BFS hop2:{hop2_seed}] +{len(added)} fresh pool={len(candidate_pool)}")
            if len(candidate_pool) >= pool_target:
                break
        if bfs_added > 0:
            logger.info(f"[BFS hop2] total added={bfs_added} final_pool={len(candidate_pool)}")
    elif len(candidate_pool) == 0:
        logger.warning("[BFS hop2] skip: 1st hop pool=0 (all seeds exhausted)")

    logger.info(f"=== candidate pool (final): {len(candidate_pool)} fresh ===")

    # Codex 5/16 9回目 review 反映 v3: exit_code 一貫管理 (Codex #3)
    from shared.follow_rate_gate import (
        can_follow as _rate_can_follow,
        record_follow as _rate_record,
        get_status as _rate_status,
    )
    # _hb_update, GateError, EXIT_* は main 上部で既に import 済 (early gate check で使用)
    exit_code = 0  # 0=ok, 20=rate_limited_noop, 21=gate_init_err

    _hb_update("follow_loop_start", problem=False)

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
        # 各 follow 直前 cap recheck (Codex 8回目 #2 + 10回目 #1: partial 区別)
        try:
            if not _rate_can_follow():
                _st = _rate_status()
                logger.warning(f"[rate_gate] cap mid-loop: hourly={_st['last_hour']}, daily={_st['today']} → stop")
                _hb_update("rate_limited_midloop", problem=False,
                           success_so_far=success,
                           hourly_count=_st["last_hour"], daily_count=_st["today"])
                # success==0 → NOOP (20), success>0 → PARTIAL (22). 虚偽成功 0 を排除.
                exit_code = EXIT_RATE_LIMITED_NOOP if success == 0 else EXIT_RATE_LIMITED_PARTIAL
                break
        except GateError as e:
            logger.error(f"[rate_gate] mid-loop error (fail-closed): {e}")
            _hb_update("gate_error_midloop", problem=True,
                       success_so_far=success, error=str(e)[:200])
            exit_code = EXIT_GATE_INIT_ERROR
            break

        status, reason = follow_one(bm, username)
        if status == "success":
            success += 1
            consecutive_fails = 0  # v4 reset on success
            already.add(username)
            append_followed(username, username)
            try:
                _rate_record()  # lock + recheck (GateError 可能)
                # Codex 30回目 #5: 成功時 直近失敗カウンタを reset
                try:
                    from shared.follow_rate_gate import record_success_resets_failures as _rate_reset_fails
                    _rate_reset_fails()
                except Exception:
                    pass
            except GateError as e:
                logger.warning(f"[rate_gate] record_follow lock cap reached: {e} → stop")
                _hb_update("rate_limited_record", problem=False,
                           success_so_far=success, error=str(e)[:200])
                # Codex 30回目 #4: 文字列マッチ廃止 → GateError.kind で構造判定
                if getattr(e, "kind", "") == "circuit":
                    exit_code = EXIT_CIRCUIT_BREAKER
                else:
                    # Codex 10回目 #1: success>0 でも非0で報告 (虚偽成功 0 排除)
                    exit_code = EXIT_RATE_LIMITED_PARTIAL
                break
            elapsed = time.time() - t_start
            rate = success / max(elapsed/60, 0.01)
            logger.info(f"[{success}/{target}] OK {username} ({elapsed:.0f}s, {rate:.1f}/min)")
            if success % 5 == 0:
                _hb_update("follow_running", problem=False,
                           seed=username, success_so_far=success)
        elif status == "skipped":
            skipped += 1
            already.add(username)
            if reason and "already_following" in str(reason):
                append_followed(username, username, source="skip_discover")
        else:
            # v4 (Codex 29回目 #4): 失敗 record → 連続失敗で circuit breaker 発動
            failed += 1
            consecutive_fails += 1
            logger.warning(f"failed {username}: {reason} (consecutive_fails={consecutive_fails})")
            try:
                fr = _rate_record_failure()
                if fr.get("circuit_triggered"):
                    logger.error(
                        f"[rate_gate] CIRCUIT BREAKER triggered "
                        f"(recent_failures={fr['recent_failures']}/{fr['fail_threshold']}) "
                        f"→ stop"
                    )
                    _hb_update("circuit_breaker_open", problem=True,
                               success_so_far=success,
                               recent_failures=fr.get("recent_failures"))
                    exit_code = EXIT_CIRCUIT_BREAKER
                    break
            except Exception as e:
                logger.warning(f"[rate_gate] record_failure err: {e}")
            # 指数バックオフ
            backoff = _rate_backoff(consecutive_fails)
            if backoff > 0:
                logger.info(f"[backoff] {backoff:.1f}s sleep (consecutive_fails={consecutive_fails})")
                time.sleep(backoff)

    _hb_update("follow_done", problem=False,
               success_total=success, skipped=skipped, failed=failed,
               exit_code=exit_code)

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
    logger.info(f"exit_code: {exit_code}")
    logger.info("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
