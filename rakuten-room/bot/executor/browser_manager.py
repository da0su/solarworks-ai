"""ROOM BOT v2 - ブラウザ管理（Playwright + 実機Chrome）

BOT専用プロファイル（data/chrome_profile）で persistent context を起動。
初回は python run.py login で手動ログインが必要。
楽天ログインURLへの自動遷移は一切しない。
"""

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page

# 親ディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger, get_screenshot_path

logger = setup_logger()

# 2026-05-07: 楽天は POST 等の sensitive 操作で
# https://login.account.rakuten.com/session/upgrade?... へ強制 redirect し
# password 再入力 (= session 昇格) を要求する仕様。
# bot 自律運用には .env の RAKUTEN_LOGIN_PASSWORD を読んで自動入力する必要がある。
SESSION_UPGRADE_URL_FRAGMENT = "login.account.rakuten.com/session/upgrade"

# 2026-05-07 P0-1: Cookie 複製 (Plan v5)
# 楽天ROOM ログイン session cookie 名 (これが無ければ未ログイン状態)
# 2026-05-07 ライブ調査結果: 楽天は OAuth/SSO に移行
#   旧: Rses/Raut/rr_session/Rat (廃止)
#   新: OSSO @ login.account.rakuten.com (主 SSO token, 1043b)
#       Im @ .id.rakuten.co.jp (id auth, 660b)
#       Re/Rg/Rz @ .rakuten.co.jp (Rakuten session)
#       s_user @ room.rakuten.co.jp (ROOM marker)
# legacy も互換のため残置 (古い profile 用 fallback)
SESSION_COOKIE_NAMES = (
    "OSSO", "ODID", "Im", "Re", "Rg", "Rz", "s_user",
    "Rses", "Raut", "rr_session", "Rat",  # legacy
)


class BrowserManager:
    """Playwright ブラウザ管理（persistent context版）

    2026-05-05 Phase A-2: action ベースで profile を分離
        POST / LIKE / FOLLOWBACK が同じ profile を共有していたため SingletonLock
        衝突が発生していた問題を修正。BrowserManager(action="like") のように指定する。
        action 未指定時は backward compat で "post" の profile を使う。
    """

    def __init__(self, action: str = "post"):
        """
        Args:
            action: "post", "like", "followback" のいずれか
                    未指定時は "post" の profile を使用 (backward compat)
        """
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._action = action
        self._profile_dir = config.get_chrome_profile(action)
        logger.info(f"BrowserManager 初期化: action={action} profile={self._profile_dir.name}")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("ブラウザが起動していません。start()を先に呼んでください。")
        return self._page

    def _cleanup_profile_locks(self) -> None:
        """プロファイルのロックファイルを削除する（自分の profile のみ）"""
        lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for lock_name in lock_files:
            lock_path = self._profile_dir / lock_name
            if lock_path.exists():
                try:
                    lock_path.unlink()
                    logger.info(f"ロックファイル削除 [{self._action}]: {lock_name}")
                except Exception as e:
                    logger.warning(f"ロックファイル削除失敗 [{self._action}] {lock_name}: {e}")

    @staticmethod
    def _read_cookie_names_from_sqlite(cookies_db: Path) -> list[str]:
        """Chrome の Cookies SQLite DB から rakuten host_key の cookie 名一覧を返す。

        失敗時は [] を返す (DB ロック中・破損・存在しない 等)。
        """
        if not cookies_db.exists():
            return []
        # DB は通常 Chrome 起動中 lock されているが、起動前なので読める想定
        names: list[str] = []
        try:
            con = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True, timeout=2)
            try:
                # 2026-05-07: login.account.rakuten.com (OSSO/ODID) を含めるため
                # `%rakuten.co.jp%` から `%rakuten%` に拡張
                cur = con.execute(
                    "SELECT name FROM cookies WHERE host_key LIKE '%rakuten%'"
                )
                names = [row[0] for row in cur.fetchall()]
            finally:
                con.close()
        except Exception as e:
            logger.debug(f"[profile_init] cookies DB 読み取り失敗 ({cookies_db}): {e}")
        return names

    def _has_session_cookies(self) -> bool:
        """自 profile に rakuten session cookie が含まれているか判定。"""
        cookies_db = self._profile_dir / "Default" / "Network" / "Cookies"
        names = self._read_cookie_names_from_sqlite(cookies_db)
        return any(n in SESSION_COOKIE_NAMES for n in names)

    def _copy_cookies_from(self, src_profile: Path) -> bool:
        """src_profile/Default/Network/Cookies* を自 profile に複製する。

        Phase A-2 で作った chrome_profile_post 等に session cookie が
        残っていないケースのフォールバック。Chrome 起動前にだけ呼ぶこと。

        Returns:
            True: 複製成功 / False: src 側に session cookie 不在 or 失敗
        """
        src_cookies = src_profile / "Default" / "Network" / "Cookies"
        src_names = self._read_cookie_names_from_sqlite(src_cookies)
        if not any(n in SESSION_COOKIE_NAMES for n in src_names):
            logger.warning(
                f"[profile_init] legacy {src_profile.name} にも session cookie 不在 "
                f"(detected={src_names[:10]}) → 手動 login 必要"
            )
            return False

        dst_dir = self._profile_dir / "Default" / "Network"
        dst_dir.mkdir(parents=True, exist_ok=True)

        # Cookies + Cookies-journal をまとめて複製 (atomic-ish: tmp → rename)
        copied: list[str] = []
        for fname in ("Cookies", "Cookies-journal"):
            src_path = src_profile / "Default" / "Network" / fname
            if not src_path.exists():
                continue
            dst_path = dst_dir / fname
            tmp_path = dst_dir / (fname + ".tmp_copy")
            try:
                shutil.copy2(src_path, tmp_path)
                # rename で atomic 置換 (Windows でも同一ボリュームならOK)
                tmp_path.replace(dst_path)
                copied.append(fname)
            except Exception as e:
                logger.error(f"[profile_init] {fname} 複製失敗: {e}")
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
                return False

        logger.info(
            f"[profile_init] {self._action} に legacy {src_profile.name} から "
            f"cookie 複製完了 (files={copied})"
        )
        return True

    def handle_session_upgrade(self, max_wait_sec: int = 15) -> dict:
        """楽天 session/upgrade ページに到達した時に password 自動入力で通過する。

        2026-05-07 P0-1.5 (Plan v5 補強):
            楽天は POST 等の sensitive 操作で session/upgrade に強制 redirect する。
            ID は Rakuten 側が既知 (OSSO で welcome XXX@gmail.com 表示済み)。
            password 入力 + 「次へ」クリック で upgraded session を取得する。

            password は rakuten-room/bot/.env の `RAKUTEN_LOGIN_PASSWORD` を使う。

        Returns:
            {handled: bool, reason: str, after_url: str}
            handled=True なら upgrade 通過済み (caller は処理続行可)。
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
        password = os.environ.get("RAKUTEN_LOGIN_PASSWORD") \
            or os.environ.get("RAKUTEN_PASSWORD")
        if not password:
            # .env を即座に再 load (dotenv があれば)
            try:
                from dotenv import load_dotenv  # type: ignore
                load_dotenv(config.PROJECT_ROOT / ".env")
                password = os.environ.get("RAKUTEN_LOGIN_PASSWORD") \
                    or os.environ.get("RAKUTEN_PASSWORD")
            except ImportError:
                pass
        if not password:
            # .env 無し → Chrome 内蔵 autofill を試行する fallback path に進む
            logger.warning(
                "[session_upgrade] RAKUTEN_LOGIN_PASSWORD 未設定 → Chrome autofill にフォールバック"
            )

        # password 入力欄を探す
        try:
            # 楽天 SSO の現行 form: input[type="password"]
            pw_input = self._page.locator('input[type="password"]').first
            pw_input.wait_for(state="visible", timeout=5000)
            if password:
                pw_input.fill(password)
                logger.info("[session_upgrade] password 入力完了 (.env)")
            else:
                # 2026-05-07: .env に password 無い場合、Chrome 内蔵の autofill を triggering する。
                # Chrome は profile に password を保存していれば、focus + Down + Enter で suggestion を選択可能。
                logger.info("[session_upgrade] .env 無し → Chrome autofill suggestion 試行")
                pw_input.click()
                self._page.wait_for_timeout(500)
                # Down arrow で autofill dropdown 開く / suggestion 選択
                pw_input.press("ArrowDown")
                self._page.wait_for_timeout(300)
                pw_input.press("Enter")
                self._page.wait_for_timeout(800)
                # 入力されたか確認
                cur_val = pw_input.input_value()
                if cur_val and len(cur_val) >= 4:
                    logger.info(f"[session_upgrade] Chrome autofill 成功 (len={len(cur_val)})")
                else:
                    logger.error("[session_upgrade] Chrome autofill 失敗 → password 取得不能")
                    return {"handled": False, "reason": "chrome_autofill_empty"}
            # 「次へ」button (label= 次へ / submit)
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
                # fallback: Enter キー送信
                pw_input.press("Enter")
                logger.info("[session_upgrade] Enter キーで submit")
            # redirect 待機
            self._page.wait_for_load_state("domcontentloaded", timeout=max_wait_sec * 1000)
            self._page.wait_for_timeout(2000)
            after = self._page.url
            logger.info(f"[session_upgrade] redirect 後 URL: {after[:120]}")
            ok = SESSION_UPGRADE_URL_FRAGMENT not in after
            if ok:
                logger.info("[session_upgrade] 通過成功 (upgraded session 取得)")
            else:
                logger.error(f"[session_upgrade] 通過失敗 (まだ upgrade ページ): {after[:120]}")
                # error message があれば取る
                try:
                    err = self._page.text_content("body") or ""
                    if "パスワード" in err or "正しい" in err:
                        logger.error(f"[session_upgrade] エラー文言: {err[:200]}")
                except Exception:
                    pass
            return {"handled": ok, "reason": "completed" if ok else "still_on_upgrade", "after_url": after}
        except Exception as e:
            logger.error(f"[session_upgrade] 例外: {e}")
            try:
                self.take_screenshot("session_upgrade_error")
            except Exception:
                pass
            return {"handled": False, "reason": f"exception:{e}"}

    def _ensure_session_cookies(self) -> None:
        """Chrome 起動前に session cookie を確認し、不足なら legacy から複製する。

        2026-05-07 P0-1 (Plan v5 真因 #1):
            Phase A-2 で作った chrome_profile_post に session cookie が
            一度も入らずに 5/6 09:00〜5/7 全 batch が "未ログイン" abort した。
            launch_persistent_context() の前に必ず check + copy する。
        """
        # follow profile は VM 側が独立 login するので skip (HOST 側 fallback のみ)
        if self._has_session_cookies():
            logger.debug(f"[profile_init] {self._action}: session cookie OK (skip copy)")
            return

        legacy = config.DATA_DIR / "chrome_profile"
        if not legacy.exists():
            logger.warning(
                f"[profile_init] {self._action}: session cookie 無し / "
                f"legacy {legacy} も存在しない → 手動 login 必要"
            )
            return

        logger.warning(
            f"[profile_init] {self._action}: session cookie 不在 → "
            f"legacy {legacy.name} から複製試行"
        )
        ok = self._copy_cookies_from(legacy)
        if not ok:
            logger.warning(
                f"[profile_init] {self._action}: cookie 複製失敗 → "
                "Chrome 起動するが「未ログイン」になる可能性大"
            )

    def start(self) -> None:
        """実機Chromeを persistent context で起動する。"""
        logger.info(f"ブラウザを起動中... [action={self._action}]")

        self._playwright = sync_playwright().start()

        user_data_dir = str(self._profile_dir)
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Chrome プロファイル: {user_data_dir}")

        # 2026-05-09 CEO 観察: bot Chrome が前面化で HOST 入力を奪う
        # → 環境変数 BOT_HEADLESS=1 (Task Scheduler 経由) なら headless 強制
        _bot_headless = os.environ.get("BOT_HEADLESS", "0") == "1"
        _effective_headless = _bot_headless or config.BROWSER_HEADLESS
        if _bot_headless:
            logger.info(f"[focus_safe] BOT_HEADLESS=1 → headless 強制 (HOST 入力非干渉)")

        # 2026-05-07 P0-1 (Plan v5 真因 #1): launch 前に session cookie の存在を保証
        # follow は VM 側 login なので host 側 chrome_profile_follow には触らない
        if self._action != "follow":
            try:
                self._ensure_session_cookies()
            except Exception as e:
                logger.error(f"[profile_init] _ensure_session_cookies 例外: {e}")

        chrome_path = config.CHROME_EXECUTABLE_PATH
        logger.info(f"Chrome 実行ファイル: {chrome_path}")

        launch_args = dict(
            user_data_dir=user_data_dir,
            executable_path=chrome_path,
            headless=_effective_headless,
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
            # 2026-05-07: 楽天 OAuth/SSO 移行に対応
            # 旧 (Rses/Raut/rr_session/Rat) は廃止 → SESSION_COOKIE_NAMES (OSSO/Im/Re/s_user 等) を確認
            # room.rakuten.co.jp の cookies だけでなく rakuten 系全 domain を見る
            cookies_room = self._context.cookies("https://room.rakuten.co.jp")
            cookies_login = self._context.cookies("https://login.account.rakuten.com")
            cookies_id = self._context.cookies("https://id.rakuten.co.jp")
            cookies_rakuten = self._context.cookies("https://www.rakuten.co.jp")
            all_cookies = cookies_room + cookies_login + cookies_id + cookies_rakuten
            cookie_names = list({c["name"] for c in all_cookies})
            has_session_cookie = any(
                name in cookie_names for name in SESSION_COOKIE_NAMES
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
        """スクリーンショットを保存。ブラウザクラッシュ時は例外を握りつぶしてパスのみ返す"""
        path = get_screenshot_path(label)
        if self._page:
            try:
                self._page.screenshot(path=str(path), full_page=False)
                logger.debug(f"スクリーンショット保存: {path}")
            except Exception as e:
                logger.warning(f"スクリーンショット取得失敗 ({label}): {e}")
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
        session_cookies = [n for n in cookie_names if n in SESSION_COOKIE_NAMES]

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
        session_cookies_after = [n for n in cookie_names_after if n in SESSION_COOKIE_NAMES]

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
