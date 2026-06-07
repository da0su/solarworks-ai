#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 FOLLOW executor: follow_host_runner.py をベースに Playwright で完結.

Plan v4 P1 の核心: pyautogui (follow_rpa_vm.py 2398行) を完全廃止し、
Playwright DOM ベースで follow を実行。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from .shared_logic import HeartbeatPusher, RateLimitDetector, SessionLogger, BASE_DIR, emergency_disk_cleanup_once
from .browser_manager_v6 import BrowserManagerV6

# 2026-05-26: VM disk full → Chrome EPIPE 防止. import 時に1回 cleanup.
try:
    emergency_disk_cleanup_once()
except Exception as _e:
    print(f"[disk_cleanup_follow] err: {_e}")


# 既存 follow_host_runner.py のロジックを VM v6 に移植
# 2026-05-24: VM では \\vboxsvr\bot 経由でアクセス (parents[3] が無い)
try:
    HOST_BOT_DIR = Path(__file__).resolve().parents[3] / "rakuten-room" / "bot"
    if not HOST_BOT_DIR.exists():
        raise FileNotFoundError(HOST_BOT_DIR)
except (IndexError, FileNotFoundError, ValueError):
    HOST_BOT_DIR = Path(r"\\vboxsvr\bot")
EXECUTOR_DIR = HOST_BOT_DIR / "executor"
SEED_USERS_PATH = EXECUTOR_DIR / "seed_users.json"
HISTORY_PATH = HOST_BOT_DIR / "data" / "follow_history.json"

RATE_LIMIT_TEXT = "ご利用上限数に達しています"
MAX_RUNTIME_SEC = 1800  # 30分
MAX_NO_NEW_SEC = 25  # 25秒新規 follow なければ次の seed へ


_HISTORY_LOCK_PATH = HISTORY_PATH.with_suffix(".lock")


def _acquire_history_lock(max_wait_sec: float = 5.0) -> bool:
    """簡易 file lock (.lock ファイル exists check). 並列書き込み防止."""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            # O_EXCL atomic create
            fd = os.open(str(_HISTORY_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # stale lock 検知 (>30s 古い lock は削除)
            try:
                if (time.time() - _HISTORY_LOCK_PATH.stat().st_mtime) > 30:
                    _HISTORY_LOCK_PATH.unlink()
                    continue
            except Exception:
                pass
            time.sleep(0.1)
    return False


def _release_history_lock() -> None:
    try:
        _HISTORY_LOCK_PATH.unlink()
    except Exception:
        pass


def _append_follow_history(user_id: str, seed_user: str = "",
                           log: "SessionLogger | None" = None) -> bool:
    """follow_history.json に entry append. 成功時 True. 失敗時 False (log 出力).

    Codex REJECT 反映 (fc102e9):
    - file lock (O_EXCL) で並列書き込み race 防止
    - 全 exception を明示的に log 出力 (虚偽成功防止)
    - 返り値で 呼び出し側が persist 失敗を把握可能
    """
    if not user_id:
        return False
    entry = {
        "user_id": user_id,
        "user_name": user_id,
        "followed_at": datetime.now().isoformat(),
        "source": "vm_v6_seed_followers",
        "seed": seed_user,
    }
    if not _acquire_history_lock():
        if log:
            log.log(f"[history_append] lock acquire fail for {user_id}")
        return False
    try:
        history_list: list = []
        if HISTORY_PATH.exists():
            try:
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    history_list = data
                else:
                    if log:
                        log.log(f"[history_append] WARN: history not list ({type(data).__name__})")
            except Exception as _re:
                # 既存ファイル読込失敗 → 上書きで履歴欠落リスク → log で警告
                if log:
                    log.log(f"[history_append] read fail (potential history loss): {_re}")
                return False  # 既存読めない時は append 諦め (上書きで欠落しない)
        history_list.append(entry)
        tmp = HISTORY_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(history_list, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(HISTORY_PATH)
            return True
        except Exception as _we:
            if log:
                log.log(f"[history_append] write fail for {user_id}: {_we}")
            try:
                tmp.unlink()
            except Exception:
                pass
            return False
    finally:
        _release_history_lock()


# ママ・ターゲティング (2026-06-07 CEo承認): 「濃い新米ママ」を集めるため、
# seed を mom-dense カテゴリ偏重で選ぶ。獲得フォロワーの質=シード account のフォロワーの質。
# 旧版は全カテゴリ(mens_fashion含む)を一緒くた flatten → 非ママ混入が breadth の質を毀損していた。
MOM_PRIMARY_CATS = ["kids"]                       # 育児=新米ママ直撃 (最優先)
MOM_ADJACENT_CATS = ["ladies_fashion", "sweets", "household",
                     "kitchen", "interior", "bags", "food"]  # ママが買う隣接ジャンル
MOM_EXCLUDE_CATS = ["mens_fashion"]               # 非ママ=集客から除外 (明確に非対象のみ)
MOM_VOLUME_FALLBACK_CATS = ["all"]                # 大型 BFS プール: 量の補完にのみ使用
MOM_PRIMARY_RATIO = 0.7                            # kids から 7 割, 隣接から 3 割 (量が足りる時)

# 設計 (2026-06-07 Codex REJECT 反映): mom-"排他"でなく mom-"加重"。
# 質(ママ濃密)を優先しつつ、kids+隣接が不足したら "all"(大型プール)で量を必ず補完し、
# 1日のフォロー量(breadth)を絶対に枯らさない。mens_fashion のみ除外。


def get_seed_users(count: int = 12) -> list:
    """seed_users.json から count 件を *mom 加重 + 量フォールバック* で選択 (ママ・ターゲティング).

    優先: kids(7割) > ママ隣接(3割) > [不足時] all(大型プール) > [なお不足] 全体(mens除外)。
    → ママ濃密に寄せつつ、フォロー量は決して枯らさない。
    """
    if not SEED_USERS_PATH.exists():
        return []
    try:
        data = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
        # 後方互換: list 形式ならそのまま (旧データ)
        if isinstance(data, list):
            users = list(dict.fromkeys(data))
            random.shuffle(users)
            return users[:count]
        if not isinstance(data, dict):
            return []

        def clean(cats):
            out = []
            for g in cats:
                v = data.get(g)
                if isinstance(v, list):
                    out.extend(v)
            return list(dict.fromkeys(out))

        primary = clean(MOM_PRIMARY_CATS)                       # kids
        adjacent_cats = [g for g in data
                         if g not in MOM_PRIMARY_CATS
                         and g not in MOM_EXCLUDE_CATS
                         and g not in MOM_VOLUME_FALLBACK_CATS]
        adjacent = [u for u in clean(adjacent_cats) if u not in set(primary)]
        seen = set(primary) | set(adjacent)
        volume = [u for u in clean(MOM_VOLUME_FALLBACK_CATS) if u not in seen]  # all (量補完)

        random.shuffle(primary); random.shuffle(adjacent); random.shuffle(volume)

        n_primary = int(round(count * MOM_PRIMARY_RATIO))
        picked = primary[:n_primary]
        picked += adjacent[:count - len(picked)]                 # 隣接
        if len(picked) < count:                                  # primary 残りで補完
            picked += primary[n_primary:n_primary + (count - len(picked))]
        if len(picked) < count:                                  # 量フォールバック: all
            picked += volume[:count - len(picked)]
        if len(picked) < count:                                  # 最終: 残り全部(mens除外済)
            rest = [u for u in (adjacent + volume) if u not in set(picked)]
            picked += rest[:count - len(picked)]
        picked = list(dict.fromkeys(picked))
        # Codex指摘#3対策: 量フォールバックがママ不足を誤魔化していないか可観測に。
        pset = set(picked)
        comp = {"kids": len(pset & set(primary)),
                "adjacent": len(pset & set(adjacent)),
                "volume_all": len(pset & set(volume))}
        try:
            print(f"[seed_compose] {comp} total={len(picked)}", flush=True)
        except Exception:
            pass
        random.shuffle(picked)
        return picked[:count]
    except Exception:
        return []


def follow_from_seed(page, seed_user: str, target_count: int, current: int,
                     history: set, hb: HeartbeatPusher, log: SessionLogger,
                     rate_detector: RateLimitDetector) -> dict:
    """1 seed の followers ページからフォロー実行."""
    result = {"success": 0, "fail": 0, "rate_limited": False}
    url = f"https://room.rakuten.co.jp/{seed_user}/followers"

    try:
        page.goto(url, timeout=20000)
        page.wait_for_timeout(3000)
    except Exception as e:
        log.log(f"[seed:{seed_user}] navigate fail: {e}")
        return result

    # 2026-05-28 バグ修正: btns[0] only → for loop で全ボタン走査
    # 旧版: `if user_id in history: continue` がwhileトップに戻り同じbtns[0]を永久ループ
    # → 10分間サイレント hang の真因。for ループで全ボタンを試してから scroll。
    _JS_USER_ID = """el => {
        const ROOM_ID = /^room_[a-f0-9]{8,40}$/i;
        const CUSTOM  = /^[A-Za-z0-9_.\\.\\-]{3,40}$/;
        const reserved = new Set([
            'items','my','discover','search','timeline','ranking',
            'register','login','categories','settings','campaigns',
            'about','help','users','followers','following','collections',
            'terms','privacy','feature','collectItemRank','likeItemRank',
            'tag','c','m','room'
        ]);
        const isValid = (s) => s && !reserved.has(s) && (ROOM_ID.test(s) || CUSTOM.test(s));
        const fromHref = (href) => {
            if (!href) return '';
            let p = href;
            if (p.startsWith('http')) {
                const m0 = p.match(/^https?:\\/\\/room\\.rakuten\\.co\\.jp(\\/.*)$/);
                if (!m0) return '';
                p = m0[1];
            }
            if (!p.startsWith('/')) return '';
            const m1 = p.match(/^\\/([^\\/?#]+)\\/(items|followers|following)/);
            if (m1 && isValid(m1[1])) return m1[1];
            return '';
        };
        if (el.getAttribute) {
            for (const a of ['data-user-id','data-userid','data-user']) {
                const d = el.getAttribute(a);
                if (isValid(d)) return d;
            }
        }
        const card = el.closest(
            'li, [class*=user-card], [class*=userCard], [class*=Card], ' +
            '[class*=user-info], [class*=followItem], [class*=follow-item]'
        );
        if (card) {
            for (const a of ['data-user-id','data-userid','data-user']) {
                const d = card.getAttribute(a);
                if (isValid(d)) return d;
            }
            const links = card.querySelectorAll('a[href]');
            for (const link of links) {
                const r = fromHref(link.getAttribute('href'));
                if (r) return r;
            }
        }
        return '';
    }"""

    last_new_at = time.time()
    consecutive_all_history = 0  # 全ボタン history 済みが続いた回数
    while current + result["success"] < target_count:
        if rate_detector.is_rate_limited(page):
            log.log(f"[seed:{seed_user}] RATE_LIMIT detected")
            result["rate_limited"] = True
            return result

        # フォローボタン取得 (Playwright DOM)
        try:
            btns = page.query_selector_all("span.follow.icon-follow:not(.ng-hide)")
        except Exception:
            btns = []
        if not btns:
            # スクロールして次のフォローボタンを探す
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(800)
            if time.time() - last_new_at > MAX_NO_NEW_SEC:
                log.log(f"[seed:{seed_user}] no new for {MAX_NO_NEW_SEC}s, next seed")
                break
            continue

        # 全ボタンを for ループで走査 (旧版: btns[0] のみ → 同ボタン永久ループ)
        clicked_one = False
        for btn in btns:
            if current + result["success"] >= target_count:
                break
            try:
                user_id = btn.get_attribute("data-user-id") or ""
                if not user_id:
                    try:
                        user_id = btn.evaluate(_JS_USER_ID) or ""
                    except Exception:
                        user_id = ""
                if user_id and user_id in history:
                    continue  # この continue は for ループ内 → 次ボタンへ (safe)
                btn.click(timeout=3000)
                page.wait_for_timeout(random.uniform(1.0, 3.0))
                page.wait_for_timeout(500)
                if rate_detector.is_rate_limited(page):
                    result["rate_limited"] = True
                    return result
                result["success"] += 1
                if user_id:
                    history.add(user_id)
                persist_ok = False
                try:
                    persist_ok = _append_follow_history(user_id, seed_user, log=log)
                except Exception as _ae:
                    log.log(f"[seed:{seed_user}] history append exception: {_ae}")
                if not persist_ok:
                    log.log(f"[seed:{seed_user}] WARN: follow OK but history NOT persisted user={user_id}")
                last_new_at = time.time()
                consecutive_all_history = 0
                hb.write(phase="navigate", current_target=seed_user,
                         success=current + result["success"], fail=result["fail"])
                log.log(f"[seed:{seed_user}] follow OK total={current + result['success']}")
                clicked_one = True
                break  # 1クリック後に btns を再取得
            except Exception as e:
                result["fail"] += 1
                log.log(f"[seed:{seed_user}] click fail: {e}")
                if result["fail"] >= 5:
                    log.log(f"[seed:{seed_user}] 5 consecutive fail, next seed")
                    return result

        if not clicked_one:
            # 全ボタンが history 済み or エラー → スクロールで新ボタン探す
            consecutive_all_history += 1
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(800)
            if time.time() - last_new_at > MAX_NO_NEW_SEC:
                log.log(f"[seed:{seed_user}] no new (all_hist={consecutive_all_history}) {MAX_NO_NEW_SEC}s, next seed")
                break

    return result


def run_follow(limit: int = 200, hb: HeartbeatPusher = None, log: SessionLogger = None,
               force: bool = False) -> dict:
    """FOLLOW 実行 (Playwright)."""
    if hb is None: hb = HeartbeatPusher("follow")
    if log is None: log = SessionLogger("follow")

    log.log(f"=== FOLLOW executor v6 start: limit={limit} force={force} ===")
    hb.write(phase="startup", force=True)

    bm = BrowserManagerV6(action="follow")
    rate_detector = RateLimitDetector()
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown"}
    history: set = set()

    # history.json から既フォロー user_id load
    if HISTORY_PATH.exists():
        try:
            h = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(h, list):
                for entry in h:
                    uid = entry.get("user_id") or entry.get("id")
                    if uid: history.add(str(uid))
        except Exception:
            pass

    try:
        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            result["stop_reason"] = "login_expired"
            return result

        seeds = get_seed_users(count=20)
        if not seeds:
            log.log("[ABORT] no seed users")
            result["stop_reason"] = "no_seeds"
            return result

        log.log(f"loaded {len(seeds)} seeds")
        run_start = time.time()

        for seed in seeds:
            if result["success"] >= limit:
                result["stop_reason"] = "target_reached"
                break
            if time.time() - run_start > MAX_RUNTIME_SEC:
                result["stop_reason"] = "runtime_limit"
                break

            sub = follow_from_seed(bm.page, seed, limit, result["success"],
                                   history, hb, log, rate_detector)
            result["success"] += sub["success"]
            result["fail"] += sub["fail"]
            if sub["rate_limited"]:
                result["stop_reason"] = "rate_limit_detected"
                break

        else:
            result["stop_reason"] = "all_seeds_done"

    finally:
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        bm.stop()
        log.log(f"=== FOLLOW executor v6 end: {result} ===")

    return result
