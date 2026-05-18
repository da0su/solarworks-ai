# -*- coding: utf-8 -*-
"""WordPress 管理画面セッション管理（Playwright・persistent context）

使い方:
    from wp_session import wp_browser
    with wp_browser(headless=True) as (browser, page):
        page.goto("https://www.kapibaran.com/wp-admin/")
        # ログイン状態が維持されていればダッシュボードに到達

- 初回は wp-login.php に行ってログイン処理を実行
- Cookieは state/wp_session/ に永続化される
"""
from __future__ import annotations
from pathlib import Path
from contextlib import contextmanager
import sys
import time
import io

# Windows cp932 環境でも UTF-8 で出力 (capture mode 互換: reconfigure 優先)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
from credentials import load as load_creds

STATE_DIR = BASE / "state" / "wp_session"
SCREENSHOT_DIR = BASE / "screenshots"
LOG_DIR = BASE / "logs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "wp_session.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _snap(page, name: str):
    path = SCREENSHOT_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        _log(f"screenshot: {path.name}")
    except Exception as e:
        _log(f"screenshot失敗 ({name}): {e}")
    return path


def _is_logged_in(page) -> bool:
    """現在のページが wp-admin ダッシュボードかチェック"""
    try:
        # wp-admin ページには #wpadminbar が存在する
        return page.locator("#wpadminbar").count() > 0 and "wp-login" not in page.url
    except Exception:
        return False


def safe_goto(page, url: str, timeout: int = 30000):
    """指定URLに遷移し、ログイン画面に飛ばされたら自動で再ログインしてもう一度遷移する"""
    creds = load_creds()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    time.sleep(0.5)
    if "wp-login.php" in page.url or page.locator("input#user_login").count() > 0:
        _log(f"safe_goto: ログイン画面に redirect されました（{url}）→ 再ログイン実行")
        _do_login(page, creds)
        # 元のURLに改めて遷移
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        time.sleep(0.5)
    return page


def _do_login(page, creds: dict):
    _log("wp-login.php に遷移してログイン処理を実行")
    page.goto(creds["login_url"], wait_until="domcontentloaded", timeout=30000)
    _snap(page, "01_login_page")
    page.fill("input#user_login", creds["username"])
    page.fill("input#user_pass", creds["password"])
    page.click("input#wp-submit")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    # ダッシュボード到達まで待つ
    try:
        page.wait_for_selector("#wpadminbar", timeout=15000)
    except Exception:
        _snap(page, "02_login_failed")
        raise RuntimeError("ログイン失敗: ダッシュボードに到達できず")
    _snap(page, "02_dashboard")
    _log("ログイン成功")


@contextmanager
def wp_browser(headless: bool = True):
    from playwright.sync_api import sync_playwright
    creds = load_creds()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(STATE_DIR),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # ダッシュボードに直接アクセスしてログイン状態確認
        page.goto("https://www.kapibaran.com/wp-admin/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(1.0)
        if not _is_logged_in(page):
            _do_login(page, creds)
        else:
            _log("既存セッションでログイン済を確認")
        try:
            yield ctx, page
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    # 疎通確認: ログインしてダッシュボードのタイトルを取る
    with wp_browser(headless=True) as (ctx, page):
        page.goto("https://www.kapibaran.com/wp-admin/", wait_until="networkidle", timeout=30000)
        _snap(page, "99_final_dashboard")
        title = page.title()
        # WPバージョンを取得
        version = None
        try:
            v_el = page.locator("#footer-upgrade").inner_text(timeout=3000)
            version = v_el
        except Exception:
            pass
        _log(f"ダッシュボードtitle: {title}")
        _log(f"WPバージョン情報: {version}")
        print(f"\n=== ログイン疎通OK ===\ntitle: {title}\nversion: {version}\n")
