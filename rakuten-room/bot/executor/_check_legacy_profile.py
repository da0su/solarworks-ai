"""chrome_profile (legacy) の login アカウントを確認.

5/5 まで正常稼働してた profile = 本来アカウント (商品 3500/フォロワー 18K) の可能性大.
これを確認 → OK なら他 profile (post/follow/like/fb) に cookies 複製.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT.parent))

import config


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    # config の DATA_DIR を見て legacy profile を直接使う
    from playwright.sync_api import sync_playwright
    legacy_profile = config.DATA_DIR / "chrome_profile"
    print(f"[check] legacy profile: {legacy_profile} (exists={legacy_profile.exists()})")
    if not legacy_profile.exists():
        print("ERR: legacy profile not found")
        return 1
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(legacy_profile),
            headless=True,
            channel="chrome",
        )
        page = ctx.new_page()
        # 先に warming goto (network 起動)
        try:
            page.goto("https://room.rakuten.co.jp/", timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[warm] {e}")
        import time
        time.sleep(3)
        from shared.profile_health import fetch_my_room_fingerprint
        fp = fetch_my_room_fingerprint(page, timeout_ms=60000)
        print(f"\n=== chrome_profile (legacy) fingerprint ===")
        for k, v in fp.items():
            print(f"  {k}: {v}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
