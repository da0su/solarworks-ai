"""楽天ID直接ログイン helper (2026-05-07 P0-1 補助).

ROOMトップ経由でログインボタンが押せない・automation検知で弾かれる場合の
fallback として、楽天IDログインページを直接開いて手動ログインさせる。

使い方:
    # default: 楽天ID直接
    python rakuten-room\bot\scripts\login_direct.py

    # 別 URL 試したいとき
    python rakuten-room\bot\scripts\login_direct.py 2  # nid_legacy
    python rakuten-room\bot\scripts\login_direct.py 3  # myrakuten
    python rakuten-room\bot\scripts\login_direct.py "https://任意URL"

login 完了後 cmd で Enter → cookie が chrome_profile_post に保存される。
"""
from __future__ import annotations

import sys
from pathlib import Path

# bot/ をパスに
BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

from executor.browser_manager import BrowserManager  # noqa: E402
from logger.logger import setup_logger  # noqa: E402

logger = setup_logger()

# 候補 URL (ROOM トップでログインできない場合の代替)
# 2026-05-07: login.rakuten.co.jp は ERR_NAME_NOT_RESOLVED → 廃止 / 内部 only と判明。
# myroom 経由で楽天 SSO に自動 redirect されるのが最も堅実。
CANDIDATES = [
    ("1_room_myroom",
     "https://room.rakuten.co.jp/myroom"),
    ("2_room_my",
     "https://room.rakuten.co.jp/my"),
    ("3_grp01_nid",
     "https://grp01.id.rakuten.co.jp/rms/nid/vc?service_id=p17&return_url=https%3A%2F%2Froom.rakuten.co.jp%2F"),
    ("4_account_sso",
     "https://login.account.rakuten.com/sso/authorize?client_id=rakuten_room_consumer_web&redirect_uri=https%3A%2F%2Froom.rakuten.co.jp%2F&response_type=code&scope=openid"),
    ("5_myrakuten",
     "https://my.rakuten.co.jp/"),
    ("6_room_top",
     "https://room.rakuten.co.jp/"),
]


def _resolve_url(arg: str | None) -> tuple[str, str]:
    if arg is None:
        label, url = CANDIDATES[0]
        return label, url
    # 数値で候補番号
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(CANDIDATES):
            return CANDIDATES[idx]
        raise SystemExit(f"index {arg} 範囲外 (1..{len(CANDIDATES)})")
    # 任意 URL
    if arg.startswith("http"):
        return ("custom", arg)
    raise SystemExit(f"引数が解釈不能: {arg!r}")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    label, url = _resolve_url(arg)

    print("=" * 60)
    print("楽天ROOM 直接ログイン helper")
    print(f"  選択候補: {label}")
    print(f"  URL: {url}")
    print()
    print("候補一覧 (引数で番号指定可):")
    for i, (lbl, u) in enumerate(CANDIDATES, 1):
        mark = " ← 今回" if lbl == label else ""
        print(f"  {i}. {lbl:<24} {u}{mark}")
    print("=" * 60)
    print()

    bm = BrowserManager(action="post")
    bm.start()

    # 2026-05-07: ERR_NAME_NOT_RESOLVED 等で navigate 失敗時は自動で次候補へ fallback
    tried = []
    for try_label, try_url in [(label, url)] + [c for c in CANDIDATES if c[0] != label]:
        try:
            bm.page.goto(try_url, wait_until="domcontentloaded", timeout=15000)
            bm.page.wait_for_timeout(2000)
            cur = bm.page.url
            tried.append((try_label, try_url, "ok", cur))
            if not cur.startswith("chrome-error"):
                print(f"[navigate] {try_label} OK → {cur}")
                break
            print(f"[navigate] {try_label} chrome-error → fallback")
        except Exception as e:
            tried.append((try_label, try_url, f"err:{e}", ""))
            print(f"[navigate] {try_label} 失敗: {str(e)[:120]} → fallback")
    else:
        print("[navigate] 全候補失敗")

    print()
    print("[試行履歴]")
    for t in tried:
        print(f"  {t[0]:<20} → {t[2][:60]}")

    try:
        cur_url = bm.page.url
        cur_title = bm.page.title()
    except Exception:
        cur_url, cur_title = "?", "?"

    cookies = bm._context.cookies("https://room.rakuten.co.jp")
    cookie_names = [c["name"] for c in cookies]
    session = [n for n in cookie_names if n in ("Rses", "Raut", "rr_session", "Rat")]
    print(f"[現状]")
    print(f"  URL: {cur_url}")
    print(f"  Title: {cur_title}")
    print(f"  Cookie: {cookie_names[:10]}")
    print(f"  Session present: {session}")
    print()
    print("[手順]")
    print("  1. このChromeで楽天IDとパスワードでログイン")
    print("  2. 必要なら手動で room.rakuten.co.jp に戻ってください")
    print("  3. ここに戻って Enter を押す → cookie 保存 → 全 profile へ複製")
    print()

    input(">>> ログイン完了後に Enter を押してください: ")

    # 状態確認
    try:
        cookies_after = bm._context.cookies("https://room.rakuten.co.jp")
        names_after = [c["name"] for c in cookies_after]
        session_after = [n for n in names_after if n in ("Rses", "Raut", "rr_session", "Rat")]
        print()
        print(f"[ログイン後]")
        print(f"  URL: {bm.page.url}")
        print(f"  Cookie 件数: {len(names_after)}")
        print(f"  Session present: {session_after}")
        if not session_after:
            print("  ! session cookie がまだ無いので保存しても POST 復旧しません。")
            print("    別の候補番号で再実行を試してください。")
    except Exception as e:
        print(f"[ERROR] 状態確認失敗: {e}")

    bm.save_session()
    print("[OK] storage_state を保存しました")
    bm.stop()
    print("[OK] Chrome 終了 (persistent context が cookie を chrome_profile_post に保存)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
