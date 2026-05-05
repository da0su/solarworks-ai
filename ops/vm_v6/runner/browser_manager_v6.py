#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 Browser Manager: Playwright Chrome × 4 profile 統一管理

Plan v4 P1 (VB 完結化): action ごとに独立 chrome profile を起動。
旧 rakuten-room/bot/executor/browser_manager.py の VM v6 対応版。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page

from .shared_logic import is_vm_env, BASE_DIR


# Chrome path
CHROME_EXEC = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


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
        """楽天 ROOM のログイン確認 (DOM ベース)."""
        try:
            self._page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded")
            self._page.wait_for_timeout(2000)

            url = self._page.url
            # ログインページにリダイレクトされたら NG
            if "grp01.id.rakuten.co.jp" in url or "/nid/" in url or "login.account" in url:
                return False

            # cookie 確認
            cookies = self._ctx.cookies("https://room.rakuten.co.jp")
            cookie_names = [c["name"] for c in cookies]
            return any(n in cookie_names for n in ["Rses", "Raut", "rr_session", "Rat"])
        except Exception:
            return False

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
