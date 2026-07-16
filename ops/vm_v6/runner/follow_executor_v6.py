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


# ============================================================
# 2026-07-16: フォロー実数検証
#   本 executor は「ボタンを押した = success」で history に記録していたため、
#   上限/制限でフォローが一切成立しなくなっても success を書き続けていた
#   (7/16 実測: 主張 162件 に対し API 実増加 95件、以降は 0件でも success 継続)。
#   DOM の成功後状態に依存せず、公開 API の following_users 実数で裏を取る。
# ============================================================
#   注意: 公開 API の following_users は**数分の反映遅延**がある (7/16 実測:
#   18:20 時点で 36,854 固定 → 18:35 に 36,954 へ +100 反映)。
#   そのため「直後に増えていない = 失敗」と即断してはいけない。
#   中断判定は「十分なクリック数」かつ「十分な経過時間」で 0 件のときのみ。
OWN_USER_ID = "1000006606047125"   # 自アカウント数値ID (公開 API 用)
VERIFY_EVERY = 25                  # N クリックごとに実数照合 (遅延を吸収するため粗め)
MIN_ELAPSED_FOR_STOP = 180         # 中断判定に必要な最低経過秒 (API 反映待ち)
MIN_CLICKS_FOR_STOP = 30           # 中断判定に必要な最低クリック数
VERIFY_SETTLE_SEC = 90             # 実行終了後、API 反映を待つ秒数


def _fetch_following_count(log=None, retries: int = 3):
    """公開 API から following_users の実数を取得. 失敗時 None.

    これが唯一の真値。history/DOM は信用しない。
    ネットワーク揺らぎ 1 回で None を返すと「検証不能 → 虚偽 success 復活」に
    繋がるため必ずリトライする。
    """
    import urllib.request
    for attempt in range(retries):
        url = f"https://room.rakuten.co.jp/api/{OWN_USER_ID}?_t={int(time.time() * 1000)}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Accept": "application/json",
                              "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8", "replace")).get("data") or {}
            v = data.get("following_users")
            if v is not None:
                return int(v)
        except Exception as e:
            if log and attempt == retries - 1:
                log.log(f"[verify] following_users 取得失敗 ({retries}回): {e}")
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    return None


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


# 2026-06-07 #1接続: 高意欲ママを直接フォロー (intent_targetsを優先消費). 完全自律GTM。
INTENT_TARGETS_PATH = HOST_BOT_DIR / "data" / "intent_targets.json"


def load_intent_targets() -> list:
    """data/intent_targets.json の高意欲ママ username 一覧 (score降順) を返す."""
    try:
        if not INTENT_TARGETS_PATH.exists():
            return []
        d = json.loads(INTENT_TARGETS_PATH.read_text(encoding="utf-8"))
        return [t.get("username") for t in (d.get("targets") or []) if t.get("username")]
    except Exception:
        return []


def follow_user_direct(page, username: str, history: set, log: SessionLogger,
                       rate_detector: RateLimitDetector) -> dict:
    """高意欲ターゲット1名を直接フォロー (Codex REJECT指摘反映で堅牢化)."""
    result = {"success": 0, "fail": 0, "rate_limited": False, "skipped": False, "skip": 0}
    if username in history:
        result["skipped"] = True; result["skip"] = 1
        return result
    try:
        page.goto(f"https://room.rakuten.co.jp/{username}/items", timeout=20000)
        time.sleep(random.uniform(2.0, 3.5))                      # Codex#2: msでなくtime.sleep(秒)
    except Exception as e:
        log.log(f"[intent:{username}] navigate fail: {e}")
        result["fail"] = 1
        return result
    if rate_detector.is_rate_limited(page):
        log.log(f"[intent:{username}] RATE_LIMIT detected before click")
        result["rate_limited"] = True
        return result
    try:
        btns = page.query_selector_all("span.follow.icon-follow:not(.ng-hide)")
    except Exception:
        btns = []
    if not btns:                                                  # 既フォロー/非公開/UI差異
        log.log(f"[intent:{username}] no follow button -> skip")
        result["skipped"] = True; result["skip"] = 1
        return result
    try:
        btns[0].click(timeout=3000)
        time.sleep(random.uniform(1.2, 2.5))                      # Codex#2
        if rate_detector.is_rate_limited(page):
            result["rate_limited"] = True
            return result
        # Codex#1 成功判定堅牢化: クリック後にfollow icon が消えた(=フォロー完了)ことを確認
        try:
            still = page.query_selector_all("span.follow.icon-follow:not(.ng-hide)")
        except Exception:
            still = btns                                          # 取得失敗時は変化検知失敗=fail扱い
        if len(still) >= len(btns):                               # follow ボタンが減らない=失敗の可能性大
            result["fail"] = 1
            log.log(f"[intent:{username}] click no DOM change -> fail")
            return result
        result["success"] = 1
        history.add(username)
        try:
            _append_follow_history(username, "intent_target", log=log)
        except Exception as e:
            log.log(f"[intent:{username}] history append exception: {e}")
        log.log(f"[intent:{username}] follow OK (high-intent target)")
    except Exception as e:
        result["fail"] = 1
        log.log(f"[intent:{username}] click fail: {e}")
    return result


def follow_from_seed(page, seed_user: str, target_count: int, current: int,
                     history: set, hb: HeartbeatPusher, log: SessionLogger,
                     rate_detector: RateLimitDetector) -> dict:
    """1 seed の followers ページからフォロー実行."""
    result = {"success": 0, "fail": 0, "rate_limited": False,
              "clicked": 0, "verified": None, "not_registering": False,
              "clicked_ids": []}
    url = f"https://room.rakuten.co.jp/{seed_user}/followers"

    # 2026-07-16: クリック前の実数を控える (これと比較して本当に増えたかを見る)
    base_following = _fetch_following_count(log)
    base_ts = time.time()
    if base_following is not None:
        log.log(f"[verify] base following={base_following}")

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
                result["clicked"] += 1

                # ── 実数検証 (VERIFY_EVERY クリックごと) ──
                # 「押した」ではなく「following が実際に増えた」かを API で確認する。
                # API に数分の遅延があるため、0 件でも即断せず
                # 「十分クリックした & 十分時間が経った」場合のみ中断する。
                if base_following is not None and result["clicked"] % VERIFY_EVERY == 0:
                    cur = _fetch_following_count(log)
                    if cur is not None:
                        verified = cur - base_following
                        elapsed = time.time() - base_ts
                        result["verified"] = verified
                        log.log(f"[verify] clicked={result['clicked']} verified={verified} "
                                f"(API following={cur}, elapsed={elapsed:.0f}s)")
                        if (verified <= 0
                                and result["clicked"] >= MIN_CLICKS_FOR_STOP
                                and elapsed >= MIN_ELAPSED_FOR_STOP):
                            log.log(f"[verify] STOP: clicked={result['clicked']} / "
                                    f"{elapsed:.0f}s 経過しても実数increase={verified}. "
                                    f"フォローが成立していない (上限/制限の疑い) "
                                    f"→ 虚偽 success を防ぐため中断")
                            result["not_registering"] = True
                            result["rate_limited"] = True
                            result["success"] = 0
                            return result

                if user_id:
                    # in-memory は即追加 (この run 内で同じボタンを再クリックしないため)
                    history.add(user_id)
                    # ファイル永続化は「実際に成立した分だけ」run 末尾で行う。
                    # クリック時に append すると、成立していないユーザーが
                    # 恒久的に「フォロー済み」扱いになり二度と再試行されない
                    # (7/16: 実成立195件に対し history 345件が書かれ履歴汚染)。
                    result["clicked_ids"].append((user_id, seed_user))
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

    # seed 単位ではここで確定しない (API 遅延で過小評価になるため)。
    # 実数の確定は run_follow の最後にまとめて行う。
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
    result = {"success": 0, "fail": 0, "skip": 0, "stop_reason": "unknown",
              "clicked": 0, "verified": None, "claimed": 0, "unverified_clicks": 0}
    history: set = set()
    clicked_ids: list = []   # 実成立分だけを history に永続化するためのバッファ

    # 2026-07-16: 実行開始時の following 実数 (最後にこれと比較して真の成立数を出す)
    run_base_following = _fetch_following_count(log)
    log.log(f"[verify] run base following={run_base_following}")

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

        # 2026-06-07 #1接続: 高意欲ターゲット優先消費。intent_targetsから "limit*0.5" 件まで先に直接フォロー
        # ※残りは従来通り seed のフォロワーから補充 (量を絶対に枯らさない=GTM設計)
        intent_targets = load_intent_targets()
        if intent_targets:
            # Codex#4: capは試行数(attempts)で厳守(成功数依存だと暴走)
            cap_attempts = max(1, int(limit * 0.5))
            attempts = 0; intent_success = 0
            result.setdefault("skip", 0)              # Codex#5: 防御的初期化
            log.log(f"[intent] {len(intent_targets)} targets, cap_attempts={cap_attempts}")
            for un in intent_targets:
                if attempts >= cap_attempts or intent_success >= cap_attempts:
                    break
                if result["success"] >= limit:
                    break
                if time.time() - run_start > MAX_RUNTIME_SEC:
                    break
                r = follow_user_direct(bm.page, un, history, log, rate_detector)
                attempts += 1
                result["success"] += r.get("success", 0)
                intent_success += r.get("success", 0)
                result["fail"] += r.get("fail", 0)
                result["skip"] += r.get("skip", 0)
                if r.get("rate_limited"):              # Codex#3: 全体停止に伝播
                    log.log("[intent] rate limited -> abort run")
                    result["stop_reason"] = "rate_limited"
                    return result
                time.sleep(random.uniform(2.5, 5.0))
            log.log(f"[intent] phase done attempts={attempts} success={intent_success}")

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
            result["clicked"] += sub.get("clicked", sub["success"])
            clicked_ids.extend(sub.get("clicked_ids", []))
            if sub.get("not_registering"):
                result["stop_reason"] = "follow_not_registering"
                break
            if sub["rate_limited"]:
                result["stop_reason"] = "rate_limit_detected"
                break

        else:
            result["stop_reason"] = "all_seeds_done"

    finally:
        bm.stop()
        # ── 実数確定 (2026-07-16) ──
        # クリック数をそのまま success にすると、成立していないフォローが
        # 実績・達成率・CEO 報告に載る (7/16: 主張345 vs 実増加195 = 43% 虚偽)。
        # 公開 API は反映が遅れるので settle 待ちしてから照合する。
        result["claimed"] = result["clicked"]
        if result["clicked"] > 0:
            final_following = None
            if run_base_following is not None:
                time.sleep(VERIFY_SETTLE_SEC)
                final_following = _fetch_following_count(log)

            if run_base_following is None or final_following is None:
                # 検証不能。ここで clicked を success として残すと虚偽が復活するので
                # success には計上せず unverified として明示する (CEO 要件: 数値は本物のみ)。
                result["success"] = 0
                result["verified"] = None
                result["unverified_clicks"] = result["clicked"]
                result["stop_reason"] = "verify_unavailable"
                log.log(f"[verify] WARN: API 検証不能。clicked={result['clicked']} を "
                        f"success に計上せず unverified 扱い (虚偽 success 防止)。"
                        f" history 永続化もスキップ")
            else:
                verified = max(0, final_following - run_base_following)
                result["verified"] = verified
                if verified != result["clicked"]:
                    log.log(f"[verify] 実数確定: clicked={result['clicked']} → "
                            f"実際に成立={verified} "
                            f"(不成立 {result['clicked'] - verified} 件 / "
                            f"API {run_base_following}→{final_following})")
                result["success"] = verified
                # 成立した分だけ history に永続化 (未成立を書くと恒久的にスキップされる)
                persisted = 0
                for uid, seed_u in clicked_ids[:verified]:
                    try:
                        if _append_follow_history(uid, seed_u, log=log):
                            persisted += 1
                    except Exception as _ae:
                        log.log(f"[verify] history append exception {uid}: {_ae}")
                log.log(f"[verify] history 永続化: {persisted}/{verified} 件 "
                        f"(clicked {result['clicked']} 件中の成立分のみ)")
        hb.write(phase="shutdown", success=result["success"], fail=result["fail"], force=True)
        log.log(f"=== FOLLOW executor v6 end: {result} ===")

    return result
