"""CEO の Chrome user data (Default profile) から bot profile へ Cookies 等を複製.

【CEO 5/20 03:30 緊急】
CEO「自分で確認してできるようになりなさい。成長して」
→ CEO 手動 login 待たず、CEO の chrome user data から bot profile へ
   cookies / storage を 複製して bot を本来アカウント login 状態にする.

【前提】
- 同じ Windows user account = DPAPI 暗号化 cookies が復号可能
- CEO Chrome を一旦閉じる必要 (User Data lock 競合)

【source / target】
source: C:\\Users\\infoa\\AppData\\Local\\Google\\Chrome\\User Data\\Default
target: rakuten-room/bot/data/chrome_profile_post/Default
        rakuten-room/bot/data/chrome_profile_follow/Default
        rakuten-room/bot/data/chrome_profile_like/Default
        rakuten-room/bot/data/chrome_profile_followback/Default

【使い方】
1. CEO に Chrome 一時停止 お願い (Slack)
2. python rakuten-room/bot/scripts/sync_from_user_chrome.py
3. CEO Chrome 再開 OK
4. 私が auto verify 実行
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT.parent))

import config

SOURCE_DIR = Path("C:/Users/infoa/AppData/Local/Google/Chrome/User Data/Default")

# Cookies / Storage の core (Chrome v117+ では Network/ サブディレクトリに Cookies が移動)
COOKIE_FILES = [
    "Network/Cookies",
    "Network/Cookies-journal",
    "Cookies",
    "Cookies-journal",
    "Local Storage",
    "Session Storage",
    "IndexedDB",
]

# encryption key (上位ディレクトリ)
LOCAL_STATE = "Local State"

ALL_PROFILES = [
    "chrome_profile_post",
    "chrome_profile_follow",
    "chrome_profile_like",
    "chrome_profile_followback",
]


def _check_chrome_running() -> bool:
    """Chrome.exe が走っているか check."""
    import subprocess
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "chrome.exe" in r.stdout.lower()
    except Exception:
        return False


def _backup_target(profile_path: Path) -> Path | None:
    if not profile_path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = profile_path.parent / f"{profile_path.name}.bak_user_chrome_{ts}"
    try:
        shutil.copytree(profile_path, bak)
        return bak
    except Exception as e:
        print(f"  [backup err] {e}")
        return None


def _copy_assets(src: Path, dst: Path) -> list[str]:
    """source の Cookies/Storage を dst にコピー."""
    copied = []
    for rel in COOKIE_FILES:
        sp = src / rel
        dp = dst / rel
        if not sp.exists():
            continue
        try:
            dp.parent.mkdir(parents=True, exist_ok=True)
            if sp.is_dir():
                if dp.exists():
                    shutil.rmtree(dp)
                shutil.copytree(sp, dp)
            else:
                shutil.copy2(sp, dp)
            copied.append(rel)
        except Exception as e:
            print(f"    [skip] {rel}: {e}")
    return copied


def _copy_local_state(src_user_data: Path, dst_user_data: Path) -> bool:
    """User Data 直下の Local State (DPAPI 暗号化 key) もコピー."""
    sp = src_user_data / LOCAL_STATE
    dp = dst_user_data / LOCAL_STATE
    if not sp.exists():
        return False
    try:
        dp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sp, dp)
        return True
    except Exception as e:
        print(f"  [Local State copy err] {e}")
        return False


def verify(profile_name: str) -> dict:
    """profile を起動して my ROOM 指紋取得."""
    from playwright.sync_api import sync_playwright
    profile_path = config.DATA_DIR / profile_name
    if not profile_path.exists():
        return {"_error": "profile not exists"}
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path), headless=True, channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.new_page()
            try:
                page.goto("https://room.rakuten.co.jp/my/items", timeout=60000, wait_until="domcontentloaded")
                import time; time.sleep(3)
                from shared.profile_health import fetch_my_room_fingerprint
                fp = fetch_my_room_fingerprint(page, timeout_ms=60000)
            finally:
                ctx.close()
        return fp
    except Exception as e:
        return {"_error": str(e)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Chrome running でも強行")
    ap.add_argument("--min-items", type=int, default=100)
    args = ap.parse_args()

    print(f"=== CEO Chrome user data → bot profile 複製 ===")
    print(f"source: {SOURCE_DIR}")
    print(f"target: {ALL_PROFILES}")
    print()

    # Chrome running check
    if _check_chrome_running() and not args.force:
        print("❌ Chrome.exe が走っています. 一旦閉じてから再実行してください.")
        print("   (CEO chrome window を閉じる -> このスクリプト再実行 -> 完了後 chrome 再起動 OK)")
        print("   または --force で強行 (Chrome 動作中だと cookies が読めない可能性大)")
        return 3
    if _check_chrome_running():
        print("⚠ Chrome 動作中だが --force で強行します (失敗 likely)")

    # Step 1: source 存在 check
    if not SOURCE_DIR.exists():
        print(f"❌ source {SOURCE_DIR} not found")
        return 1
    src_user_data = SOURCE_DIR.parent

    print(f"=== Step 1: 全 {len(ALL_PROFILES)} profile に複製 ===")
    for t in ALL_PROFILES:
        tp = config.DATA_DIR / t
        print(f"\n  [{t}]")
        # backup
        if tp.exists():
            bak = _backup_target(tp)
            if bak:
                print(f"    backup: {bak.name}")
        else:
            tp.mkdir(parents=True, exist_ok=True)
            print(f"    新規 profile dir 作成")
        # Default profile に複製
        default_dst = tp / "Default"
        default_dst.mkdir(parents=True, exist_ok=True)
        copied = _copy_assets(SOURCE_DIR, default_dst)
        print(f"    Default に copied: {copied}")
        # Local State (User Data 直下)
        ls_ok = _copy_local_state(src_user_data, tp)
        print(f"    Local State copied: {ls_ok}")

    print(f"\n=== Step 2: 各 profile fingerprint 検証 ===")
    results = {}
    for t in ALL_PROFILES:
        fp = verify(t)
        ic = fp.get("item_count", 0) or 0
        ok = "✅" if ic >= args.min_items else "❌"
        print(f"  {ok} {t}: items={fp.get('item_count')}, followers={fp.get('follower_count')}, follow={fp.get('follow_count')}")
        results[t] = fp

    # Step 3: baseline 保存 (post profile 成功なら)
    post_fp = results.get("chrome_profile_post", {})
    if (post_fp.get("item_count") or 0) >= args.min_items:
        try:
            from shared.profile_health import save_baseline
            save_baseline(post_fp, note=f"sync_from_user_chrome {datetime.now().isoformat(timespec='seconds')}")
            print(f"\n✅ baseline 保存完了")
        except Exception as e:
            print(f"  [baseline save err] {e}")

    print(f"\n=== 完了 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
