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
SESSION_UPGRADE_URL_FRAGMENT = "login.account.rakuten.com/session/upgrade"


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

    def start(self) -> Page:
        """Chrome 起動."""
        self._cleanup_locks()
        self.profile.mkdir(parents=True, exist_ok=True)

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
        """楽天 ROOM のログイン確認 (cookie ベース・OAuth/SSO 対応).

        2026-05-07 修正:
          - cookie names を OSSO/Im/Re/Rg/Rz/s_user/ODID に拡張
          - login.account.rakuten.com / .id.rakuten.co.jp / .rakuten.co.jp 全 domain を確認
        """
        try:
            self._page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded")
            self._page.wait_for_timeout(2000)

            url = self._page.url
            # ログインページに redirect されたら NG (login form 表示)
            # 注意: session/upgrade は handle_session_upgrade() で別処理するのでここでは NG 扱いしない
            if ("grp01.id.rakuten.co.jp" in url or "/nid/" in url) and "session/upgrade" not in url:
                return False

            # cookie 確認 (modern OAuth/SSO 対応)
            cookies_room = self._ctx.cookies("https://room.rakuten.co.jp")
            cookies_login = self._ctx.cookies("https://login.account.rakuten.com")
            cookies_id = self._ctx.cookies("https://id.rakuten.co.jp")
            cookies_rakuten = self._ctx.cookies("https://www.rakuten.co.jp")
            all_cookies = cookies_room + cookies_login + cookies_id + cookies_rakuten
            cookie_names = {c["name"] for c in all_cookies}
            return any(n in cookie_names for n in SESSION_COOKIE_NAMES)
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

        if SESSION_UPGRADE_URL_FRAGMENT not in cur:
            return {"handled": False, "reason": "not_session_upgrade_page"}

        logger.info(f"[session_upgrade] 検知: {cur[:120]}...")
        password = get_credential("RAKUTEN_LOGIN_PASSWORD") or get_credential("RAKUTEN_PASSWORD") \
            or os.environ.get("RAKUTEN_LOGIN_PASSWORD") or os.environ.get("RAKUTEN_PASSWORD")
        if not password:
            logger.error(
                "[session_upgrade] RAKUTEN_LOGIN_PASSWORD 未設定 → 自動通過不能。 "
                "vm_data/.env_vm に RAKUTEN_LOGIN_PASSWORD=<pw> を追記してください。"
            )
            return {"handled": False, "reason": "no_password_in_env"}

        try:
            pw_input = self._page.locator('input[type="password"]').first
            pw_input.wait_for(state="visible", timeout=5000)
            pw_input.fill(password)
            logger.info("[session_upgrade] password 入力完了")
            for sel in [
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
            ok = SESSION_UPGRADE_URL_FRAGMENT not in after
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
