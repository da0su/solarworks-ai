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


def get_seed_users(count: int = 12) -> list:
    """seed_users.json から count 件の seed をランダム選択."""
    if not SEED_USERS_PATH.exists():
        return []
    try:
        data = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
        # ジャンル別ユーザーリストから flatten
        all_users = []
        if isinstance(data, dict):
            for genre, users in data.items():
                if isinstance(users, list):
                    all_users.extend(users)
        elif isinstance(data, list):
            all_users = data
        random.shuffle(all_users)
        return all_users[:count]
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

    last_new_at = time.time()
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

        # 1個目をクリック
        try:
            btn = btns[0]
            user_id = btn.get_attribute("data-user-id") or ""
            if user_id and user_id in history:
                # スキップして次へ
                continue
            btn.click(timeout=3000)
            page.wait_for_timeout(random.uniform(1.0, 3.0))

            # 検証 (DOM ベース)
            page.wait_for_timeout(500)
            # クリック後に rate_limit が出るか確認
            if rate_detector.is_rate_limited(page):
                result["rate_limited"] = True
                return result

            result["success"] += 1
            if user_id:
                history.add(user_id)
            # 2026-05-27 重大バグ修正: follow_history.json に append
            # 旧版は in-memory のみ → SSOT が永久 0 表示・虚偽報告
            persist_ok = False
            try:
                persist_ok = _append_follow_history(user_id, seed_user, log=log)
            except Exception as _ae:
                log.log(f"[seed:{seed_user}] history append exception: {_ae}")
            if not persist_ok:
                # persist 失敗を success count に反映するか否か:
                # → 反映しない (Rakuten 側は follow されている事実は変わらない)
                # → ただし log に WARN 明示
                log.log(f"[seed:{seed_user}] WARN: follow OK but history NOT persisted user={user_id}")
            last_new_at = time.time()

            # heartbeat update
            hb.write(phase="navigate", current_target=seed_user,
                     success=current + result["success"], fail=result["fail"])

            log.log(f"[seed:{seed_user}] follow OK total={current + result['success']}")
        except Exception as e:
            result["fail"] += 1
            log.log(f"[seed:{seed_user}] click fail: {e}")
            if result["fail"] >= 5:
                log.log(f"[seed:{seed_user}] 5 consecutive fail, next seed")
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
