"""楽天 ID 自動 login script (CEO 5/18 10:00 「A」選択).

【使い方】
1. CEO が credentials/rakuten.env に 1 行追加:
       RAKUTEN_LOGIN_EMAIL=xxxxx@example.com
2. password は rakuten-room/bot/.env の RAKUTEN_LOGIN_PASSWORD を使用 (既存)
3. このscript を実行:
       python rakuten-room/bot/scripts/auto_login.py
4. 完了後 sync_profile_cookies.py を自動で実行 (option) して 他 3 profile に複製

【楽天 ID login URL】
- https://login.account.rakuten.com/sso/authorize?... (SSO 直)
  もしくは https://room.rakuten.co.jp/ で 「ログイン」 button → SSO

【2FA / Captcha 対応】
- 自動 submit 後 30秒で my room に遷移しなければ 2FA 必要と判定
- headless=False で起動 → CEO が手動補助できる
- 検知時は Slack <!channel> 通知

【safety】
- source profile = chrome_profile_post 固定
- min_item_count=100 を満たしたら sync 実行 (空アカ複製防止)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT.parent))

import config
from executor.browser_manager import BrowserManager
from shared.profile_health import fetch_my_room_fingerprint, save_baseline


def _load_credentials() -> tuple[str | None, str | None]:
    """EMAIL = credentials/rakuten.env 優先, fallback .env
    PASSWORD = rakuten-room/bot/.env の RAKUTEN_LOGIN_PASSWORD"""
    email = os.environ.get("RAKUTEN_LOGIN_EMAIL")
    password = os.environ.get("RAKUTEN_LOGIN_PASSWORD")
    # rakuten.env (credentials/)
    env_path = ROOT.parent / "credentials" / "rakuten.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("RAKUTEN_LOGIN_EMAIL="):
                email = email or line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("RAKUTEN_LOGIN_PASSWORD="):
                password = password or line.split("=", 1)[1].strip().strip('"').strip("'")
    # .env (rakuten-room/bot/)
    bot_env = ROOT / "bot" / ".env"
    if bot_env.exists():
        for line in bot_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("RAKUTEN_LOGIN_EMAIL=") and not email:
                email = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("RAKUTEN_LOGIN_PASSWORD=") and not password:
                password = line.split("=", 1)[1].strip().strip('"').strip("'")
    return email, password


def _notify_slack(msg: str) -> None:
    try:
        sl = ROOT.parent / "ops" / "notifications" / "slack_reporter.py"
        subprocess.run([sys.executable, str(sl), msg], capture_output=True, timeout=20)
    except Exception as e:
        print(f"[slack] err: {e}", file=sys.stderr)


def attempt_auto_login(email: str, password: str,
                        profile: str = "chrome_profile_post",
                        headless: bool = False,
                        wait_2fa_sec: int = 120) -> dict:
    """楽天 ID auto login. 成功時 fingerprint 返す."""
    print(f"\n=== auto_login start ===")
    print(f"  profile: {profile}")
    print(f"  email: {email[:4]}***{email[-8:] if len(email) > 8 else ''}")
    print(f"  headless: {headless}")
    bm = BrowserManager(action="post")  # post profile を使う
    # action="post" 固定. 他 profile への複製は別 step.
    bm.start()
    page = bm.page

    try:
        # まず ROOM TOP へ
        page.goto("https://room.rakuten.co.jp/", timeout=60000, wait_until="domcontentloaded")
        time.sleep(2)

        # 既に login 済 check
        status = bm.check_login_status()
        if status.get("logged_in"):
            fp = fetch_my_room_fingerprint(page, timeout_ms=60000)
            print(f"  既 login: {fp}")
            if (fp.get("item_count") or 0) >= 100:
                print(f"  ✅ 既に正規アカウントに login 済 (商品 {fp['item_count']} 件)")
                return {"status": "already_logged_in", "fingerprint": fp}
            else:
                print(f"  ⚠ login 済だが商品 {fp.get('item_count')} 件 = 空アカウント. logout 必要")
                # logout: cookies 全削除して再試行
                ctx = page.context
                ctx.clear_cookies()
                print(f"  → cookies 全削除. 再 login を試行")

        # 楽天 ID login へ navigate (ROOM 経由 SSO)
        # 「ログイン」 button or link を探す
        login_url = "https://login.account.rakuten.com/sso/authorize?client_id=room_web&redirect_uri=https%3A%2F%2Froom.rakuten.co.jp%2F&response_type=code&scope=openid+profile+email"
        page.goto(login_url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(3)

        # Email 入力
        email_selectors = [
            'input[name="u"]',
            'input[type="email"]',
            'input[name="loginid"]',
            'input[id*="username"]',
            'input[id*="email"]',
        ]
        email_input = None
        for sel in email_selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=3000)
                email_input = loc
                print(f"  email input found: {sel}")
                break
            except Exception:
                continue
        if not email_input:
            return {"status": "email_input_not_found", "url": page.url, "title": page.title()}

        email_input.fill(email)
        time.sleep(1)
        # 次へ / 続行 button click
        next_buttons = [
            'button:has-text("次へ")',
            'button:has-text("続行")',
            'button[type="submit"]',
            'input[type="submit"]',
        ]
        for sel in next_buttons:
            try:
                btn = page.locator(sel).first
                if btn.is_visible():
                    btn.click()
                    print(f"  next clicked: {sel}")
                    break
            except Exception:
                continue
        time.sleep(3)

        # Password 入力
        pw_selectors = [
            'input[name="p"]',
            'input[type="password"]',
            'input[name="password"]',
        ]
        pw_input = None
        for sel in pw_selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=5000)
                pw_input = loc
                print(f"  password input found: {sel}")
                break
            except Exception:
                continue
        if not pw_input:
            return {"status": "password_input_not_found", "url": page.url, "title": page.title()}

        pw_input.fill(password)
        time.sleep(1)
        # ログイン submit
        login_buttons = [
            'button:has-text("ログイン")',
            'button:has-text("Sign in")',
            'button[type="submit"]',
            'input[type="submit"]',
        ]
        for sel in login_buttons:
            try:
                btn = page.locator(sel).first
                if btn.is_visible():
                    btn.click()
                    print(f"  login submitted: {sel}")
                    break
            except Exception:
                continue

        # 遷移待ち (2FA / Captcha 出る場合は手動操作猶予)
        print(f"\n  → 最大 {wait_2fa_sec}秒 待機 (2FA/Captcha があれば CEO の手動補助を期待)")
        _notify_slack(f"【楽天 ID auto_login】 2FA/Captcha が出たら 画面で操作してください ({wait_2fa_sec}s 待機中)")
        deadline = time.time() + wait_2fa_sec
        success_fp = None
        while time.time() < deadline:
            try:
                cur_url = page.url
                if "room.rakuten.co.jp" in cur_url and "login" not in cur_url and "sso" not in cur_url:
                    # ROOM に戻った可能性 = login 成功か?
                    fp = fetch_my_room_fingerprint(page, timeout_ms=30000)
                    item_count = fp.get("item_count") or 0
                    if item_count >= 100:
                        success_fp = fp
                        print(f"\n  ✅ login 成功 (商品 {item_count} 件)")
                        break
                    else:
                        print(f"  [wait] ROOM だが商品 {item_count} 件 (まだ login 反映待ち)")
            except Exception as e:
                print(f"  [wait err] {e}")
            time.sleep(5)

        if success_fp:
            return {"status": "logged_in", "fingerprint": success_fp}
        else:
            return {"status": "timeout_or_2fa", "url": page.url, "title": page.title()}
    finally:
        # bm.stop() で確実 close
        try:
            bm.stop()
        except Exception:
            pass


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true", help="login 成功でも 他 profile に複製しない")
    ap.add_argument("--headless", action="store_true", help="headless 起動 (default: False = 画面表示)")
    ap.add_argument("--wait-2fa", type=int, default=120, help="2FA/Captcha 手動操作待機秒数")
    args = ap.parse_args()

    email, password = _load_credentials()
    if not email:
        print("ERR: RAKUTEN_LOGIN_EMAIL が credentials/rakuten.env or .env に未設定")
        print("     credentials/rakuten.env を作成し以下 1 行追加してください:")
        print("       RAKUTEN_LOGIN_EMAIL=your@example.com")
        return 1
    if not password:
        print("ERR: RAKUTEN_LOGIN_PASSWORD 未設定")
        return 2

    result = attempt_auto_login(email, password, headless=args.headless, wait_2fa_sec=args.wait_2fa)
    print(f"\n=== result ===")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if result.get("status") in ("logged_in", "already_logged_in"):
        fp = result["fingerprint"]
        save_baseline(fp, note=f"auto_login {datetime.now().isoformat(timespec='seconds')}")
        print(f"\n✅ baseline 保存完了")
        _notify_slack(f"【auto_login 成功】商品 {fp.get('item_count')} / フォロワー {fp.get('follower_count')} / フォロー {fp.get('follow_count')} - baseline 保存済")
        if not args.no_sync:
            print(f"\n=== Step: sync_profile_cookies.py 自動実行 ===")
            sync_script = ROOT / "bot" / "scripts" / "sync_profile_cookies.py"
            r = subprocess.run([sys.executable, str(sync_script)], capture_output=False)
            return r.returncode
        return 0
    else:
        _notify_slack(f"【auto_login 失敗】status={result.get('status')} url={result.get('url')} → CEO 手動補助要")
        return 3


if __name__ == "__main__":
    sys.exit(main())
