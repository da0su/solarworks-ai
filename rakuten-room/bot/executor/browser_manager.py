"""ROOM BOT v2 - ブラウザ管理（Playwright + 実機Chrome）

BOT専用プロファイル（data/chrome_profile）で persistent context を起動。
初回は python run.py login で手動ログインが必要。
楽天ログインURLへの自動遷移は一切しない。
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page

# 親ディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger, get_screenshot_path

logger = setup_logger()


class BrowserManager:
    """Playwright ブラウザ管理（persistent context版）"""

    def __init__(self):
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("ブラウザが起動していません。start()を先に呼んでください。")
        return self._page

    def _cleanup_profile_locks(self) -> None:
        """プロファイルのロックファイルを削除する"""
        lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for lock_name in lock_files:
            lock_path = config.CHROME_USER_DATA_DIR / lock_name
            if lock_path.exists():
                try:
                    lock_path.unlink()
                    logger.info(f"ロックファイル削除: {lock_name}")
                except Exception as e:
                    logger.warning(f"ロックファイル削除失敗 {lock_name}: {e}")

    def start(self) -> None:
        """実機Chromeを persistent context で起動する。"""
        logger.info("ブラウザを起動中...")

        self._playwright = sync_playwright().start()

        user_data_dir = str(config.CHROME_USER_DATA_DIR)
        config.CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Chrome プロファイル: {user_data_dir}")

        chrome_path = config.CHROME_EXECUTABLE_PATH
        logger.info(f"Chrome 実行ファイル: {chrome_path}")

        launch_args = dict(
            user_data_dir=user_data_dir,
            executable_path=chrome_path,
            headless=config.BROWSER_HEADLESS,
            slow_mo=config.BROWSER_SLOW_MO,
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation", "--no-sandbox"],
        )

        try:
            self._context = self._playwright.chromium.launch_persistent_context(**launch_args)
        except Exception as e:
            logger.warning(f"初回起動失敗: {e}")
            logger.info("ロックファイル削除後にリトライ...")
            self._cleanup_profile_locks()
            time.sleep(2)
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(**launch_args)

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

        self._page.set_default_timeout(config.ELEMENT_TIMEOUT)
        self._page.set_default_navigation_timeout(config.PAGE_LOAD_TIMEOUT)

        logger.info("ブラウザ起動完了")

    def _get_page_debug_info(self) -> dict:
        """現在のページのデバッグ情報を取得"""
        info = {
            "url": "",
            "title": "",
            "body_text_preview": "",
        }
        try:
            info["url"] = self._page.url
            info["title"] = self._page.title()
            body = self._page.text_content("body") or ""
            # 最初の200文字だけ記録
            info["body_text_preview"] = body.strip()[:200].replace("\n", " ")
        except Exception as e:
            info["body_text_preview"] = f"(取得失敗: {e})"
        return info

    def check_login_status(self) -> dict:
        """楽天ROOMにログインしているか複合条件で確認する。

        ROOMトップへ遷移して判定。
        room.rakuten.co.jp 配下（/items, /myroom, /feed 等）もログイン済みとして扱う。
        """
        logger.info("ログイン状態を確認中...")
        result = {
            "logged_in": False,
            "method": "unknown",
            "url": "",
            "title": "",
            "screenshot": "",
        }

        try:
            self._page.goto(config.ROOM_BASE_URL, wait_until="domcontentloaded")
            self._page.wait_for_timeout(4000)

            result["url"] = self._page.url
            result["title"] = self._page.title()
            logger.info(f"  URL: {result['url']}")
            logger.info(f"  Title: {result['title']}")

            is_on_room = "room.rakuten.co.jp" in result["url"]

            # --- Cookie確認 ---
            cookies = self._context.cookies("https://room.rakuten.co.jp")
            cookie_names = [c["name"] for c in cookies]
            has_session_cookie = any(
                name in cookie_names for name in ["Rses", "Raut", "rr_session", "Rat"]
            )
            logger.info(f"  Cookie: {cookie_names[:10]}")
            if has_session_cookie:
                logger.info(f"  -> セッションcookie検出")

            # --- ログインページにリダイレクトされていないか ---
            is_on_login_page = "grp01.id.rakuten.co.jp" in result["url"] or "/nid/" in result["url"]
            if is_on_login_page:
                logger.warning("  -> ログインページにリダイレクトされました")
                result["method"] = "url_redirect_to_login"
                result["screenshot"] = str(self.take_screenshot("login_check_redirect"))
                return result

            # --- Cookie + ROOMドメイン → ログイン済み ---
            if has_session_cookie and is_on_room:
                logger.info("  -> Cookie + URL判定: ログイン済み")
                result["logged_in"] = True
                result["method"] = "cookie+url"
                return result

            # --- DOM: ログイン済みユーザー専用要素 ---
            # 「ROOMをはじめる」ボタンが見える = 未ログイン
            # 「my ROOM」「フィード」等が見える = ログイン済み
            logged_in_selectors = [
                'a[href*="/my/"]:has-text("my")',
                'a:has-text("my ROOM")',
                'a:has-text("フィード")',
                'img[alt*="プロフィール"]',
                '.user-icon',
                '[class*="avatar"]',
            ]
            for sel in logged_in_selectors:
                try:
                    el = self._page.locator(sel)
                    if el.first.is_visible(timeout=1000):
                        logger.info(f"  -> DOM判定: ログイン済み ({sel})")
                        result["logged_in"] = True
                        result["method"] = f"dom:{sel}"
                        return result
                except Exception:
                    continue

            # --- 未ログインの明確な兆候 ---
            try:
                not_logged_in = self._page.locator(
                    'a:has-text("ROOMをはじめる"), '
                    'a:has-text("新規登録・ログイン"), '
                    'button:has-text("ROOMをはじめる")'
                )
                if not_logged_in.first.is_visible(timeout=2000):
                    logger.warning("  -> 未ログイン要素検出（ROOMをはじめる等）")
                    result["method"] = "not_logged_in_element"
                    result["screenshot"] = str(self.take_screenshot("login_check_not_logged_in"))
                    # デバッグ情報
                    debug = self._get_page_debug_info()
                    logger.warning(f"  body_preview: {debug['body_text_preview']}")
                    return result
            except Exception:
                pass

            # --- ROOMドメイン上にいれば投稿を試みる ---
            if is_on_room:
                logger.info("  -> ROOMドメイン上、フォールバックでログイン済みとして続行")
                result["logged_in"] = True
                result["method"] = "fallback_room_accessible"
                return result

            # --- 全判定で確証なし ---
            logger.warning("  -> 全判定で確証なし")
            result["method"] = "all_checks_inconclusive"
            result["screenshot"] = str(self.take_screenshot("login_check_unknown"))
            debug = self._get_page_debug_info()
            logger.warning(f"  url: {debug['url']}")
            logger.warning(f"  title: {debug['title']}")
            logger.warning(f"  body_preview: {debug['body_text_preview']}")
            return result

        except Exception as e:
            logger.error(f"ログイン確認中にエラー: {e}")
            result["method"] = f"error:{e}"
            try:
                result["screenshot"] = str(self.take_screenshot("login_check_error"))
            except Exception:
                pass
            return result

    def save_session(self) -> None:
        """storage_state.json にバックアップする。"""
        if self._context:
            try:
                state_dir = config.SESSION_STATE_PATH.parent
                state_dir.mkdir(parents=True, exist_ok=True)
                self._context.storage_state(path=str(config.SESSION_STATE_PATH))
                logger.info(f"セッションバックアップ: {config.SESSION_STATE_PATH}")
            except Exception as e:
                logger.warning(f"セッション保存スキップ: {e}")

    def take_screenshot(self, label: str) -> Path:
        """スクリーンショットを保存"""
        path = get_screenshot_path(label)
        if self._page:
            self._page.screenshot(path=str(path), full_page=False)
            logger.debug(f"スクリーンショット保存: {path}")
        return path

    def stop(self) -> None:
        """ブラウザを終了"""
        logger.info("ブラウザを終了中...")
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"終了時エラー（無視）: {e}")
        finally:
            self._page = None
            self._context = None
            self._playwright = None
        self._cleanup_profile_locks()
        logger.info("ブラウザ終了完了")

    def login_manual(self) -> None:
        """CEOが手動でログインするためのヘルパー。

        ROOMトップを開いて待機。楽天ログインURLへは一切遷移しない。
        ユーザーがブラウザ内で通常導線（ROOMトップの「ログイン」ボタン等）から
        手動でログインし、ROOMに戻ったらEnterを押す。
        persistent contextなのでログイン後のcookieは自動保存される。
        """
        logger.info("ROOMトップを開きます。手動でログインしてください。")

        # ROOMトップを開く（楽天ログインURLは絶対に使わない）
        self._page.goto(config.ROOM_BASE_URL, wait_until="domcontentloaded")
        self._page.wait_for_timeout(3000)

        # 現在の状態をログに記録
        current_url = self._page.url
        current_title = self._page.title()
        ss_path = self.take_screenshot("login_manual_start")

        cookies = self._context.cookies("https://room.rakuten.co.jp")
        cookie_names = [c["name"] for c in cookies]
        session_cookies = [n for n in cookie_names if n in ["Rses", "Raut", "rr_session", "Rat"]]

        logger.info(f"  URL: {current_url}")
        logger.info(f"  Title: {current_title}")
        logger.info(f"  Cookie: {session_cookies}")
        logger.info(f"  Screenshot: {ss_path}")

        print("\n" + "=" * 60)
        print("BOT専用ChromeでROOMトップを開きました。")
        print(f"  URL:        {current_url}")
        print(f"  Title:      {current_title}")
        print(f"  Cookie:     {session_cookies if session_cookies else 'none'}")
        print(f"  Screenshot: {ss_path}")
        print("")
        print("[手順]")
        print("  1. ブラウザ上部のアドレスバーに")
        print("     https://room.rakuten.co.jp/ と入力してEnter")
        print("     または画面上の「ログイン」ボタンをクリック")
        print("  2. 楽天IDとパスワードでログイン")
        print("  3. ROOMのページが表示されたら")
        print("  4. ここに戻って Enter を押してください")
        print("")
        print("※ cookieは自動保存されます。次回以降は login 不要です。")
        print("=" * 60 + "\n")

        input(">>> ログイン完了後に Enter を押してください: ")

        # Enter後: 最新タブ取得 + 短い固定待機のみ（ハング防止）
        logger.info("Enter押下後、ページ情報を取得中...")
        self._page = self._context.pages[-1] if self._context.pages else self._page

        # domcontentloaded を短い timeout で試す（失敗しても続行）
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

        # 固定待機 1.5秒のみ（networkidle は使わない）
        self._page.wait_for_timeout(1500)

        # URL/title/screenshotを取得（1回だけリトライ）
        after_url = ""
        after_title = ""
        after_ss = None
        for attempt in range(2):
            try:
                after_url = self._page.url
                after_title = self._page.title()
                after_ss = self.take_screenshot("login_manual_after")
                break
            except Exception as e:
                logger.warning(f"  ページ情報取得 試行{attempt+1}: {e}")
                self._page = self._context.pages[-1] if self._context.pages else self._page
                self._page.wait_for_timeout(1500)

        # cookie再チェック
        cookies_after = self._context.cookies("https://room.rakuten.co.jp")
        cookie_names_after = [c["name"] for c in cookies_after]
        session_cookies_after = [n for n in cookie_names_after if n in ["Rses", "Raut", "rr_session", "Rat"]]

        logger.info(f"  Login後 URL: {after_url}")
        logger.info(f"  Login後 Title: {after_title}")
        logger.info(f"  Login後 Cookie: {session_cookies_after}")
        logger.info(f"  Login後 Screenshot: {after_ss}")

        # デバッグ: ページのbody先頭200文字
        try:
            debug = self._get_page_debug_info()
            logger.info(f"  body_preview: {debug['body_text_preview']}")
        except Exception as e:
            logger.warning(f"  body_preview取得失敗: {e}")

        print(f"\n  URL:        {after_url}")
        print(f"  Title:      {after_title}")
        print(f"  Cookie:     {session_cookies_after if session_cookies_after else 'none'}")
        print(f"  Screenshot: {after_ss}")

        # ROOMの会員向けページにいるか確認
        is_on_room = "room.rakuten.co.jp" in after_url
        is_on_login = "grp01.id.rakuten.co.jp" in after_url

        if is_on_room and not is_on_login:
            print("\n  -> ROOMページ上にいます。")
        elif is_on_login:
            print("\n  -> まだログインページにいます。ログイン完了後に再実行してください。")
        else:
            print(f"\n  -> 現在のURL: {after_url}")
            print("     ROOMページではありませんが、cookieは保存されている可能性があります。")

        # セッション保存
        self.save_session()
        logger.info("セッションが保存されました。")
