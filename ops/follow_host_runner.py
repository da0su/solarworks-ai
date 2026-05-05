#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOW HOST RUNNER v2 - Main PC Playwright でフォロー実行 + follow_rpa_log.json 書込
2026-04-27 v2: follow_direct.py の動作実績ある実装を流用

VM 不要。Main PC の data/chrome_profile (永続セッション) を使用。
実行結果を follow_rpa_log.json に書き込み (canonical FOLLOW カウントとして反映)。

使い方:
  python ops/follow_host_runner.py --limit 100
  python ops/follow_host_runner.py --limit 200
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = REPO_ROOT / "rakuten-room" / "bot"
EXECUTOR_DIR = BOT_DIR / "executor"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = str(BOT_DIR / "data" / "chrome_profile")
# Use a SEPARATE log file to avoid being overwritten by the VM bot.
# follow_rpa_vm.py writes \\VBOXSVR\share\follow_rpa_log.json (= executor/follow_rpa_log.json)
# via a replace-write, which would clobber any host-runner entries in that file.
# patrol_with_sheet_sync.py sums BOTH files for the canonical follow count.
RPA_LOG = EXECUTOR_DIR / "follow_host_log.json"
VM_RPA_LOG = EXECUTOR_DIR / "follow_rpa_log.json"  # VM bot file (read-only reference)
SEED_USERS_PATH = EXECUTOR_DIR / "seed_users.json"
HISTORY_PATH = BOT_DIR / "data" / "follow_history.json"

RATE_LIMIT_TEXT = "ご利用上限数に達しています"
MAX_NO_NEW_SEC = 25  # これ以上新規フォローなければ次のユーザーへ（15→25秒に延長）


def load_rpa_log() -> list:
    if RPA_LOG.exists():
        try:
            return json.loads(RPA_LOG.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_rpa_log(entries: list):
    tmp = RPA_LOG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RPA_LOG)


def append_rpa_log_entry(success: int, fail_actionable: int, skip_total: int, stop_reason: str):
    """follow_rpa_log.json に 1エントリ追加 (follow_rpa_vm.py 互換フォーマット)"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "success": success,
        "fail_actionable": fail_actionable,
        "skip_total": skip_total,
        "fail_total_including_skip": fail_actionable + skip_total,
        "stop_reason": stop_reason,
        "fail_stats": {
            "already_followed": skip_total,
        },
        "screenshot_path": None,
        "metrics": {
            "source": "follow_host_runner_v2",
            "platform": "playwright_main_pc",
        },
    }
    entries = load_rpa_log()
    entries.append(entry)
    save_rpa_log(entries)
    print(f"[RPA_LOG] appended: success={success} stop={stop_reason} ts={entry['timestamp']}")


def get_seed_users(count: int = 8) -> list:
    """seed_users.json からランダム選択"""
    try:
        data = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
        all_users: list[str] = []
        for genre_users in data.values():
            if isinstance(genre_users, list):
                all_users.extend(u for u in genre_users if isinstance(u, str))
        random.shuffle(all_users)
        selected = all_users[:count]
        print(f"[seed] {len(selected)}/{len(all_users)} selected")
        return selected
    except Exception as e:
        print(f"[seed] error: {e}")
        return ["room_6af55dfa6e", "room_f9a9ae9bd7", "room_06e572ba22"]


def check_rate_limit(page) -> bool:
    try:
        return RATE_LIMIT_TEXT in page.inner_text("body")
    except Exception:
        return False


def setup_rate_limit_interceptor(page) -> dict:
    """ネットワーク応答を監視して 429 を確実に検知する。
    返り値の dict は rate_limited フラグを共有するための参照渡し用。
    """
    state = {"rate_limited": False}

    def on_response(response):
        if "/api/follow" in response.url and response.status == 429:
            state["rate_limited"] = True
            print(f"  [429] follow API rate limit detected: {response.url[:80]}")

    page.on("response", on_response)
    return state


def check_login(page) -> bool:
    try:
        page.goto("https://room.rakuten.co.jp/", timeout=20000)
        page.wait_for_timeout(2000)
        url = page.url
        if "login" in url or "sso" in url.lower() or "account.rakuten" in url:
            print(f"[login] NOT logged in: {url[:80]}")
            return False
        print(f"[login] OK: {url[:80]}")
        return True
    except Exception as e:
        print(f"[login] error: {e}")
        return False


def follow_from_user(page, seed_user: str, limit: int, current_count: int, history_entries: list, rl_state: dict, url_pattern: str = "/followers") -> dict:
    """seed_user のフォロワー/フォロー中リストからフォロー。
    url_pattern: "/followers" または "/following"
    rl_state: setup_rate_limit_interceptor() からの共有 dict (429 検知フラグ)
    """
    followed = current_count
    skipped = 0
    api_rejected = 0

    url = f"https://room.rakuten.co.jp/{seed_user}{url_pattern}"
    try:
        page.goto(url, timeout=20000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  [nav] error: {e}")
        return {"success": 0, "skipped": 0, "rate_limited": False}

    no_new_start = time.time()

    for scroll in range(200):
        if followed >= limit or rl_state["rate_limited"]:
            break

        if time.time() - no_new_start > MAX_NO_NEW_SEC:
            print(f"  [{seed_user}{url_pattern}] {MAX_NO_NEW_SEC}s 新規なし→次へ")
            break

        try:
            follow_els = page.query_selector_all("span.follow.icon-follow:not(.ng-hide)")
        except Exception:
            break

        if not follow_els:
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1000)
            continue

        for el in follow_els:
            if followed >= limit or rl_state["rate_limited"]:
                break
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=5000)
                # 600ms に短縮（元800ms）
                page.wait_for_timeout(600)

                if rl_state["rate_limited"]:
                    print(f"  [RATE_LIMIT/429] {followed - current_count} 件で上限検知 (network)")
                    break

                try:
                    cls_after = el.evaluate("e => e.className")
                    confirmed = "ng-hide" in cls_after
                except Exception:
                    confirmed = True

                if not confirmed:
                    api_rejected += 1
                    if api_rejected >= 3:
                        print(f"  [WARN] {api_rejected} 連続 API 拒否 → rate_limit の可能性")
                        rl_state["rate_limited"] = True
                        break
                    continue

                api_rejected = 0
                followed += 1
                no_new_start = time.time()
                print(f"  [follow] #{followed}/{limit}")
                history_entries.append({
                    "user_name": "",
                    "followed_at": datetime.now().isoformat(),
                    "source": f"host_{seed_user}{url_pattern}",
                })
                # 0.5-1.0s に短縮（元1.0-2.0s）
                time.sleep(random.uniform(0.5, 1.0))

            except Exception as e:
                skipped += 1

        if rl_state["rate_limited"]:
            break
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(700)

    return {"success": followed - current_count, "skipped": skipped, "rate_limited": rl_state["rate_limited"]}


def kill_orphan_chrome():
    """前回実行で残ったchrome_profile使用Chrome orphan のみを解放する（profile lock防止）。
    ※ taskkill /f /im chrome.exe は POST/LIKE/FB bot の Chrome も殺すため使わない。
    WMI で chrome_profile を CommandLine に持つプロセスだけを選択 kill する。"""
    import subprocess
    _NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*chrome_profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WIN
        )
        print(f"[cleanup] WMI selective kill chrome_profile: rc={r.returncode}")
    except Exception as e:
        print(f"[cleanup] WMI kill warn: {e}")
    # Remove lock files
    for lock_name in ["lockfile", "SingletonLock", ".parentlock"]:
        lf = Path(PROFILE) / lock_name
        if lf.exists():
            try:
                lf.unlink()
                print(f"[cleanup] {lock_name} removed")
            except Exception:
                pass
    # Clear Chrome "restore session" crash state to prevent restore dialog.
    # CRITICAL: must do BOTH:
    # (1) Delete Sessions/ + Last Session / Current Session files — these are what Chrome reads
    #     to determine "was previous run interrupted?". If absent, no restore bubble.
    # (2) Reset exit_type=Normal / exited_cleanly=true in Local State AND Default/Preferences.
    try:
        import shutil as _shutil
        sessions_dir = Path(PROFILE) / "Default" / "Sessions"
        if sessions_dir.exists():
            _shutil.rmtree(sessions_dir, ignore_errors=True)
            print("[cleanup] removed Sessions/ directory")
        for name in ["Current Session", "Current Tabs", "Last Session", "Last Tabs"]:
            f = Path(PROFILE) / "Default" / name
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception as e:
        print(f"[cleanup] sessions cleanup warn: {e}")
    try:
        import json as _json
        for path in [Path(PROFILE) / "Local State", Path(PROFILE) / "Default" / "Preferences"]:
            if not path.exists():
                continue
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
                prof = data.get("profile", {})
                prof["exit_type"] = "Normal"
                prof["exited_cleanly"] = True
                prof["crashed"] = False
                data["profile"] = prof
                path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
                print(f"[cleanup] {path.name}: reset exit_type/exited_cleanly")
            except Exception as e:
                print(f"[cleanup] {path.name} reset failed: {e}")
    except Exception:
        pass
    print(f"[cleanup] orphan Chrome cleanup done")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    limit = args.limit

    print(f"=== follow_host_runner v2 start: limit={limit} {datetime.now()} ===")

    # 起動前に前回残留Chromeプロセスを解放（profile lock防止）
    kill_orphan_chrome()
    time.sleep(1)  # 解放待機

    from playwright.sync_api import sync_playwright

    # Task Scheduler のタスクタイムアウトは30分以上に設定すること。
    # 深夜低c24帯（0-300件）では RL 率0%で70-150件/session が可能。
    # 起動オーバーヘッド分として90秒をバッファ確保。30min = 1800 sec。
    MAX_RUNTIME_SEC = 1800
    run_start = time.time()

    seed_users = get_seed_users(count=12)
    history_entries: list = []
    total_success = 0
    total_skip = 0
    rate_limited = False
    stop_reason = "target_reached"

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            executable_path=CHROME,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-restore-last-session",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ],
        )
        page = ctx.new_page()

        # Auto-accept all browser dialogs (beforeunload, alerts, confirms)
        # Rakuten ROOM triggers "ページを離れますか？" beforeunload which blocks navigation
        page.on("dialog", lambda dialog: dialog.accept())
        # Also handle dialogs on any new pages opened in this context
        ctx.on("page", lambda p: p.on("dialog", lambda d: d.accept()))

        if not check_login(page):
            print("[ABORT] Not logged in to Rakuten ROOM")
            append_rpa_log_entry(0, 0, 0, "not_logged_in")
            ctx.close()
            return 1

        # Set up network-level 429 rate limit interceptor (shared across all seed users)
        rl_state = setup_rate_limit_interceptor(page)

        # Phase 0: ランキング・トップページ・ジャンル別から直接フォロー（CEO指示C: 強化）
        DISCOVERY_PAGES = [
            "https://room.rakuten.co.jp/ranking",
            "https://room.rakuten.co.jp/ranking/follower",
            "https://room.rakuten.co.jp/ranking/post",
            "https://room.rakuten.co.jp/ranking/like",
            "https://room.rakuten.co.jp/",
            "https://room.rakuten.co.jp/genre/sweets",
            "https://room.rakuten.co.jp/genre/kids",
            "https://room.rakuten.co.jp/genre/household",
            "https://room.rakuten.co.jp/genre/kitchen",
            "https://room.rakuten.co.jp/genre/bags",
            "https://room.rakuten.co.jp/genre/fashion",
            "https://room.rakuten.co.jp/genre/beauty",
            "https://room.rakuten.co.jp/genre/interior",
            "https://room.rakuten.co.jp/genre/appliance",
            "https://room.rakuten.co.jp/genre/health",
            "https://room.rakuten.co.jp/genre/pet",
            "https://room.rakuten.co.jp/genre/handmade",
        ]
        for disc_url in DISCOVERY_PAGES:
            if total_success >= limit or rl_state["rate_limited"]:
                break
            if time.time() - run_start > MAX_RUNTIME_SEC:
                stop_reason = "runtime_limit"
                break
            try:
                print(f"[discovery] {disc_url}")
                page.goto(disc_url, timeout=20000)
                page.wait_for_timeout(3000)
                # 早期枯渇検知: 最初の5スクロールでボタンが1件もなければ skip
                # （全ページ50スクロール×0.8s=40s → 17ページ=680s → seed到達不能を防ぐ）
                disc_no_btn_count = 0
                disc_found_any = False
                # スクロールしながらフォローボタンを探す (50→100へ深堀り)
                for _ in range(50):
                    if rl_state["rate_limited"]:
                        break
                    els = page.query_selector_all("span.follow.icon-follow:not(.ng-hide)")
                    if not els:
                        disc_no_btn_count += 1
                        if not disc_found_any and disc_no_btn_count >= 5:
                            print(f"  [discovery] 5scroll空→早期skip: {disc_url[-30:]}")
                            break
                        page.evaluate("window.scrollBy(0, 800)")
                        page.wait_for_timeout(800)
                        continue
                    disc_found_any = True
                    disc_no_btn_count = 0
                    for el in els:
                        if total_success >= limit or rl_state["rate_limited"]:
                            break
                        try:
                            el.scroll_into_view_if_needed(timeout=3000)
                            el.click(timeout=5000)
                            page.wait_for_timeout(600)
                            if rl_state["rate_limited"]:
                                break
                            try:
                                confirmed = "ng-hide" in el.evaluate("e => e.className")
                            except Exception:
                                confirmed = True
                            if confirmed:
                                total_success += 1
                                history_entries.append({"user_name": "", "followed_at": datetime.now().isoformat(), "source": f"discovery_{disc_url[-20:]}"})
                                print(f"  [disc-follow] #{total_success}/{limit}")
                                time.sleep(random.uniform(0.5, 1.0))
                        except Exception:
                            pass
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(700)
                if rl_state["rate_limited"]:
                    rate_limited = True
                    stop_reason = "rate_limit_detected"
            except Exception as e:
                print(f"[discovery] error: {e}")

        # Phase 1: /followers と /following の両方を探索（2倍のユーザープール）
        runtime_exceeded = False
        for seed_user in seed_users:
            if time.time() - run_start > MAX_RUNTIME_SEC:
                runtime_exceeded = True
                stop_reason = "runtime_limit"
                break
            for url_pattern in ["/followers", "/following"]:
                if total_success >= limit or rl_state["rate_limited"]:
                    break
                if time.time() - run_start > MAX_RUNTIME_SEC:
                    runtime_exceeded = True
                    stop_reason = "runtime_limit"
                    break
                remaining = limit - total_success
                print(f"[seed] {seed_user}{url_pattern} (remaining={remaining})")
                result = follow_from_user(page, seed_user, limit, total_success, history_entries, rl_state, url_pattern)
                total_success += result["success"]
                total_skip += result["skipped"]
                if result["rate_limited"]:
                    rate_limited = True
                    stop_reason = "rate_limit_detected"
            if total_success >= limit or rl_state["rate_limited"] or runtime_exceeded:
                break

        if total_success >= limit:
            stop_reason = "target_reached"
        elif rate_limited:
            stop_reason = "rate_limit_detected"
        elif runtime_exceeded:
            stop_reason = "runtime_limit"
        else:
            stop_reason = "all_seeds_done"

        ctx.close()

    print(f"=== DONE: success={total_success} skip={total_skip} stop={stop_reason} ===")

    # Update follow_history.json
    if history_entries:
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
            history.extend(history_entries)
            tmp = HISTORY_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(HISTORY_PATH)
        except Exception as e:
            print(f"[history] write error: {e}")

    # Write to follow_rpa_log.json (canonical)
    append_rpa_log_entry(total_success, 0, total_skip, stop_reason)

    return 0 if total_success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
