#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 Browser Manager: Playwright Chrome × 4 profile 統一管理

Plan v4 P1 (VB 完結化): action ごとに独立 chrome profile を起動。
旧 rakuten-room/bot/executor/browser_manager.py の VM v6 対応版。

2026-05-07 Plan v6 (VB 全機能移行):
  - SESSION_COOKIE_NAMES を OAuth/SSO 移行に対応 (OSSO/Im/Re/Rg/Rz/s_user/ODID)
  - handle_session_upgrade() 追加 (POST 等 sensitive 操作の password 再入力 自動通過)
  - is_logged_in() を modern cookie names + login.account.rakuten.com 含めて再判定
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page

from .shared_logic import is_vm_env, BASE_DIR, get_credential

logger = logging.getLogger(__name__)


# Chrome path
CHROME_EXEC = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 2026-05-07: 楽天 OAuth/SSO 移行に対応 (HOST 版 browser_manager.py と同期)
# 旧: Rses/Raut/rr_session/Rat (廃止)
# 新:  OSSO @ login.account.rakuten.com / Im @ .id.rakuten.co.jp / Re/Rg/Rz @ .rakuten.co.jp / s_user @ room
SESSION_COOKIE_NAMES = (
    "OSSO", "ODID", "Im", "Re", "Rg", "Rz", "s_user",
    "Rses", "Raut", "rr_session", "Rat",  # legacy compat
)

# session/upgrade fragment (POST 等の sensitive 操作で楽天が強制 redirect する URL)
# 2026-05-24: /sso/authorize?...sign_in 経路も追加 (POST mix で頻発)
SESSION_UPGRADE_URL_FRAGMENT = "login.account.rakuten.com/session/upgrade"
SSO_SIGN_IN_FRAGMENTS = (
    "login.account.rakuten.com/session/upgrade",
    "login.account.rakuten.com/sso/authorize",
    "login.account.rakuten.com/sso/sign_in",
    "grp01.id.rakuten.co.jp/sign_in",
)


def _is_sso_redirect(url: str) -> bool:
    return any(frag in url for frag in SSO_SIGN_IN_FRAGMENTS)


# Profile path 解決
def get_profile(action: str) -> Path:
    """action 名から profile path を返す."""
    if is_vm_env():
        # VM 内
        return BASE_DIR / "data" / f"chrome_profile_{action}"
    else:
        # HOST 側 (テスト用)
        return Path(r"C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\data") / f"chrome_profile_{action}"


PLAYWRIGHT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
    "--no-restore-last-session",
    "--disable-session-crashed-bubble",
    "--hide-crash-restore-bubble",
]


class BrowserManagerV6:
    """4 action 用 Chrome 起動・終了管理."""

    def __init__(self, action: str, headless: bool = False):
        self.action = action
        self.headless = headless
        self.profile = get_profile(action)
        self._pw = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        return self._ctx

    def _cleanup_locks(self):
        """SingletonLock 等の残置 lock を削除."""
        for lock in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            p = self.profile / lock
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    def _kill_orphan_chrome(self):
        """2026-05-27: 前 session 残置の Chrome zombie を kill.
        本日 18:40 で LIKE が起動直後に全 feed Page closed エラー →
        前 session の chrome.exe が profile を握りっぱなしの可能性。

        Codex REVIEW_NEEDED 反映:
        他の rakuten_room_runner.py が動作中なら skip (並列 chrome 競合回避).
        例: FB 19:20 がまだ動いている時に FOLLOW 19:30 が start →
            FB の chrome を taskkill しない。
        """
        import subprocess
        # 他 runner プロセス検知
        try:
            r = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
                capture_output=True, text=True, timeout=10
            )
            out = (r.stdout or "")
            my_pid = os.getpid()
            other_runner_count = 0
            for line in out.splitlines():
                if "rakuten_room_runner" in line and "--mode" in line:
                    other_runner_count += 1
            # 自分自身を除く: rakuten_room_runner 起動の python は自分を含む
            # → 2 以上なら他 runner が同時走行中
            if other_runner_count >= 2:
                print(f"[bm_v6] other runner detected (n={other_runner_count}) → "
                      f"skip chrome kill to avoid killing peer")
                return
        except Exception as _ce:
            # wmic 失敗時は安全側で skip (zombie 残置リスクより誤 kill 回避優先)
            print(f"[bm_v6] runner detect fail (skip kill): {_ce}")
            return

        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, text=True, timeout=10
            )
            print(f"[bm_v6] taskkill chrome.exe rc={r.returncode}")
        except Exception as e:
            print(f"[bm_v6] taskkill chrome.exe error (ignored): {e}")

    def start(self) -> Page:
        """Chrome 起動. 2026-05-27: Playwright EPIPE 対策で 1 回 retry."""
        self._kill_orphan_chrome()
        self._cleanup_locks()
        self.profile.mkdir(parents=True, exist_ok=True)

        # 2026-05-27 FB 19:20 EPIPE 対策: Playwright init 失敗時 1 回 retry
        # 観測: PipeTransport.send / new PlaywrightDispatcher で EPIPE
        last_err = None
        for attempt in range(2):
            try:
                self._pw = sync_playwright().start()
                self._ctx = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile),
                    executable_path=CHROME_EXEC,
                    headless=self.headless,
                    args=PLAYWRIGHT_ARGS,
                    viewport={"width": 1280, "height": 800},
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    ignore_default_args=["--enable-automation"],
                )
                break
            except Exception as _pe:
                last_err = _pe
                em = str(_pe)
                print(f"[bm_v6] playwright start fail (attempt {attempt+1}/2): {em[:200]}")
                # cleanup partial state before retry
                try:
                    if self._pw is not None:
                        self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                self._ctx = None
                if attempt == 0:
                    # Codex REJECT 反映: node.exe 全 kill は影響範囲広すぎる → 削除.
                    # retry 前に lock + chrome cleanup のみ再実行 + 短い wait.
                    import time as _t2
                    _t2.sleep(3)
                    try:
                        self._cleanup_locks()
                    except Exception:
                        pass
                    # 自分の Playwright サブプロセスだけ kill (node 全 kill 回避)
                    # → どうしても残置 node プロセスがあれば次回 trigger で
                    #   _kill_orphan_chrome の wmic 検査で判定 (peer なし時) 後 kill
        if self._ctx is None:
            raise RuntimeError(f"playwright start failed twice: {last_err}")

        # ダイアログ自動許可 (楽天の beforeunload 対策)
        self._ctx.on("page", lambda p: p.on("dialog", lambda d: d.accept()))

        if self._ctx.pages:
            self._page = self._ctx.pages[0]
        else:
            self._page = self._ctx.new_page()
        self._page.on("dialog", lambda d: d.accept())
        self._page.set_default_timeout(10000)
        self._page.set_default_navigation_timeout(30000)

        return self._page

    def is_logged_in(self) -> bool:
        """楽天 ROOM のログイン確認 (cookie ベース優先・navigation フォールバック).

        2026-05-28 fix: page.goto() が VM 高負荷時 (Chrome 複数同時起動) に hang する既知問題.
          → Step1: cookie のみで判定 (navigation 不要・instant). cookie 存在 → True で即 return.
          → Step2: cookie 不在時のみ navigation で redirect 確認 (timeout=10000 明示).
        2026-05-07 修正:
          - cookie names を OSSO/Im/Re/Rg/Rz/s_user/ODID に拡張
          - login.account.rakuten.com / .id.rakuten.co.jp / .rakuten.co.jp 全 domain を確認
        """
        try:
            # Step1: cookie だけで判定 (navigation なし・高速・hang 回避)
            cookies_room = self._ctx.cookies("https://room.rakuten.co.jp")
            cookies_login = self._ctx.cookies("https://login.account.rakuten.com")
            cookies_id = self._ctx.cookies("https://id.rakuten.co.jp")
            cookies_rakuten = self._ctx.cookies("https://www.rakuten.co.jp")
            all_cookies = cookies_room + cookies_login + cookies_id + cookies_rakuten
            cookie_names = {c["name"] for c in all_cookies}
            if any(n in cookie_names for n in SESSION_COOKIE_NAMES):
                return True  # session cookie 確認 → ログイン OK (navigation skip)

            # Step2: cookie 不在 → navigation で login redirect 確認 (timeout 明示)
            self._page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded",
                            timeout=10000)
            self._page.wait_for_timeout(1000)
            url = self._page.url
            # ログインページに redirect されたら NG (login form 表示)
            # 注意: session/upgrade は handle_session_upgrade() で別処理するのでここでは NG 扱いしない
            if ("grp01.id.rakuten.co.jp" in url or "/nid/" in url) and "session/upgrade" not in url:
                return False
            return False  # cookie なし かつ redirect なし → セッション不明 → 安全側 False
        except Exception:
            return False

    def handle_session_upgrade(self, max_wait_sec: int = 15) -> dict:
        """楽天 session/upgrade ページに到達した時に password 自動入力で通過する.

        2026-05-07 Plan v6 Phase A-1:
            HOST 版 browser_manager.py の handle_session_upgrade を移植。
            楽天は POST 等の sensitive 操作で session/upgrade に強制 redirect する。
            input[type=password] に shared_logic.get_credential('RAKUTEN_LOGIN_PASSWORD') を fill
            → 「次へ」 click → redirect 待機。

        Returns:
            {handled: bool, reason: str, after_url: str}
        """
        if self._page is None:
            return {"handled": False, "reason": "no_page"}

        try:
            cur = self._page.url
        except Exception as e:
            return {"handled": False, "reason": f"url_read_err:{e}"}

        if not _is_sso_redirect(cur):
            return {"handled": False, "reason": "not_session_upgrade_page"}

        logger.info(f"[session_upgrade] SSO redirect 検知: {cur[:120]}...")

        # 2026-05-24 DEBUG: 失敗時の調査用に DOM dump + screenshot
        try:
            from datetime import datetime as _dt
            from pathlib import Path as _P
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            dbg_dir = _P(r"\\vboxsvr\vm_data\screenshots") / _dt.now().strftime("%Y-%m-%d")
            dbg_dir.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(dbg_dir / f"{ts}_sso_entry.png"))
            html = self._page.content()
            (dbg_dir / f"{ts}_sso_entry.html").write_text(html[:30000], encoding="utf-8", errors="replace")
            # Log key inputs
            inputs = self._page.locator("input").all()[:10]
            for i, inp in enumerate(inputs):
                try:
                    name = inp.get_attribute("name") or ""
                    typ = inp.get_attribute("type") or ""
                    vis = inp.is_visible(timeout=500)
                    logger.info(f"[session_upgrade] input[{i}]: name={name!r} type={typ!r} visible={vis}")
                except Exception:
                    pass
        except Exception as _de:
            logger.warning(f"[session_upgrade] dbg dump err: {_de}")
        password = get_credential("RAKUTEN_LOGIN_PASSWORD") or get_credential("RAKUTEN_PASSWORD") \
            or os.environ.get("RAKUTEN_LOGIN_PASSWORD") or os.environ.get("RAKUTEN_PASSWORD")
        if not password:
            logger.error(
                "[session_upgrade] RAKUTEN_LOGIN_PASSWORD 未設定 → 自動通過不能。 "
                "vm_data/.env_vm に RAKUTEN_LOGIN_PASSWORD=<pw> を追記してください。"
            )
            return {"handled": False, "reason": "no_password_in_env"}

        try:
            # 2026-05-24: 楽天 SSO は JS widget で input 描画. networkidle まで待機.
            try:
                self._page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            self._page.wait_for_timeout(3000)

            # 何らかの input が visible になるまで待つ
            try:
                self._page.locator("input:visible").first.wait_for(state="visible", timeout=15000)
            except Exception:
                logger.warning("[session_upgrade] no visible input after 15s wait")

            # Step 1: email/userid 欄を多様な selector で探す
            email_addr = (get_credential("RAKUTEN_LOGIN_EMAIL")
                          or os.environ.get("RAKUTEN_LOGIN_EMAIL"))
            email_selectors = [
                'input[type="email"]',
                'input[name="loginid"]',
                'input[name="u"]',
                'input[name="username"]',
                'input[name="userid"]',
                'input[placeholder*="メールアドレス"]',
                'input[placeholder*="ユーザID"]',
                'input[aria-label*="メールアドレス"]',
                'input[aria-label*="ユーザID"]',
            ]
            email_filled = False
            if email_addr:
                for sel in email_selectors:
                    try:
                        loc = self._page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                            loc.first.fill(email_addr)
                            logger.info(f"[session_upgrade] email 入力完了 ({sel})")
                            email_filled = True
                            break
                    except Exception:
                        continue

                # 楽天 widget の場合 input 名がランダム化 → JS 注入で type=text を直接探す
                if not email_filled:
                    try:
                        # JavaScript で input[type=text] を取得
                        found = self._page.evaluate("""() => {
                            const inputs = Array.from(document.querySelectorAll('input'));
                            for (const inp of inputs) {
                                const t = (inp.type || '').toLowerCase();
                                const style = window.getComputedStyle(inp);
                                if ((t === 'text' || t === 'email' || t === '') && style.display !== 'none' && inp.offsetParent !== null) {
                                    return true;
                                }
                            }
                            return false;
                        }""")
                        if found:
                            self._page.evaluate(f"""() => {{
                                const inputs = Array.from(document.querySelectorAll('input'));
                                for (const inp of inputs) {{
                                    const t = (inp.type || '').toLowerCase();
                                    const style = window.getComputedStyle(inp);
                                    if ((t === 'text' || t === 'email' || t === '') && style.display !== 'none' && inp.offsetParent !== null) {{
                                        inp.focus();
                                        inp.value = {json.dumps(email_addr) if False else 'PLACEHOLDER'};
                                        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                                        return;
                                    }}
                                }}
                            }}""".replace("'PLACEHOLDER'", repr(email_addr)))
                            logger.info("[session_upgrade] email JS injection 試行")
                            email_filled = True
                    except Exception as e:
                        logger.warning(f"[session_upgrade] JS email fill err: {e}")

                if email_filled:
                    # 「次へ」 click
                    for sel in ['button:has-text("次へ")', 'button:has-text("ログイン")',
                                'button[type="submit"]', 'input[type="submit"]']:
                        try:
                            b = self._page.locator(sel)
                            if b.count() > 0 and b.first.is_visible(timeout=1500):
                                b.first.click()
                                logger.info(f"[session_upgrade] email submit: {sel}")
                                break
                        except Exception:
                            continue
                    self._page.wait_for_timeout(4000)
                    try:
                        self._page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

            # Step 2: password 入力
            pw_input = self._page.locator('input[type="password"]').first
            pw_input.wait_for(state="visible", timeout=15000)
            pw_input.fill(password)
            logger.info("[session_upgrade] password 入力完了")
            for sel in [
                'button:has-text("ログイン")',
                'button:has-text("次へ")',
                'button[type="submit"]',
                'input[type="submit"]',
            ]:
                btn = self._page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    logger.info(f"[session_upgrade] {sel} クリック")
                    break
            else:
                pw_input.press("Enter")
                logger.info("[session_upgrade] Enter キーで submit")
            self._page.wait_for_load_state("domcontentloaded", timeout=max_wait_sec * 1000)
            self._page.wait_for_timeout(2000)
            after = self._page.url
            ok = not _is_sso_redirect(after)
            logger.info(f"[session_upgrade] 通過 {'成功' if ok else '失敗'}: {after[:120]}")
            return {"handled": ok, "reason": "completed" if ok else "still_on_upgrade", "after_url": after}
        except Exception as e:
            logger.error(f"[session_upgrade] 例外: {e}")
            return {"handled": False, "reason": f"exception:{e}"}

    def stop(self):
        """Chrome 終了 + lock cleanup."""
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._ctx = None
            self._pw = None
        self._cleanup_locks()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


if __name__ == "__main__":
    # 自己テスト
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "follow"
    print(f"profile: {get_profile(action)}")
    print(f"profile exists: {get_profile(action).exists()}")
