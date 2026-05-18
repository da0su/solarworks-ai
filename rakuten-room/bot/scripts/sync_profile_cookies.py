"""1 つの profile に login した状態の cookies を 他 profile に複製.

【使い方】
CEO が chrome_profile_post に手動 login (本来 アカウント 3500/18K) 完了後:
    python rakuten-room/bot/scripts/sync_profile_cookies.py
→ chrome_profile_post の Cookies/LocalStorage を
   chrome_profile_follow, chrome_profile_like, chrome_profile_followback
   に複製.
→ 完了後 baseline (my ROOM 指紋) を 4 profile 全部について確認・保存.

【source profile 変更】
    python rakuten-room/bot/scripts/sync_profile_cookies.py --source chrome_profile_follow
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

# Cookie / Storage の core file (Chrome user data dir 構成)
COOKIE_FILES = [
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Local Storage",  # dir
    "Default/Session Storage",  # dir
    "Default/IndexedDB",  # dir
    "Default/Local State",  # encryption key で重要
]

ALL_PROFILES = [
    "chrome_profile_post",
    "chrome_profile_follow",
    "chrome_profile_like",
    "chrome_profile_followback",
]


def _backup(profile_path: Path) -> Path:
    """target profile を backup (元に戻せるよう)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = profile_path.parent / f"{profile_path.name}.bak_{ts}"
    if profile_path.exists():
        shutil.copytree(profile_path, bak, dirs_exist_ok=False)
    return bak


def _copy_cookie_assets(src: Path, dst: Path) -> list[str]:
    """Cookie / Storage 関連を src → dst にコピー."""
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
            print(f"  [skip] {rel}: {e}")
    return copied


def verify_profile_fingerprint(profile_name: str) -> dict:
    """profile を起動して my ROOM 指紋取得."""
    from playwright.sync_api import sync_playwright
    from shared.profile_health import fetch_my_room_fingerprint
    profile_path = config.DATA_DIR / profile_name
    if not profile_path.exists():
        return {"_error": "profile not exists"}
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path), headless=True, channel="chrome",
            )
            page = ctx.new_page()
            try:
                page.goto("https://room.rakuten.co.jp/", timeout=60000, wait_until="domcontentloaded")
                import time; time.sleep(2)
                fp = fetch_my_room_fingerprint(page, timeout_ms=60000)
            finally:
                ctx.close()
        return fp
    except Exception as e:
        return {"_error": str(e)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="chrome_profile_post",
                    help="cookie 複製元 profile (default: chrome_profile_post)")
    ap.add_argument("--dry-run", action="store_true", help="複製せず source verify のみ")
    ap.add_argument("--min-items", type=int, default=100,
                    help="source profile の最低商品数. これ未満なら 中止 (空アカ複製防止)")
    args = ap.parse_args()

    src_name = args.source
    src_path = config.DATA_DIR / src_name
    if not src_path.exists():
        print(f"ERR: source profile {src_name} not found")
        return 1

    print(f"=== Step 1: source profile {src_name} fingerprint 確認 ===")
    src_fp = verify_profile_fingerprint(src_name)
    print(f"  {src_fp}")
    if "_error" in src_fp:
        print(f"ERR: source fingerprint 取得失敗")
        return 2
    item_count = src_fp.get("item_count") or 0
    if item_count < args.min_items:
        print(f"\n❌ source profile {src_name} の商品数 {item_count} < {args.min_items}")
        print(f"   = まだ正規アカウントに login されていません.")
        print(f"   CEO が手動 login してから再実行してください.")
        return 3
    print(f"\n✅ source profile OK (商品 {item_count} 件)")

    if args.dry_run:
        print("\n[dry-run] 複製スキップ")
        return 0

    # baseline 保存
    from shared.profile_health import save_baseline
    save_baseline(src_fp, note=f"source={src_name} (CEO 正規アカウント login 後)")
    print(f"\n=== Step 2: baseline 保存 (state/profile_baseline.json) ===")

    targets = [p for p in ALL_PROFILES if p != src_name]
    print(f"\n=== Step 3: 他 {len(targets)} profile に複製 ===")
    for t in targets:
        tp = config.DATA_DIR / t
        print(f"\n  [target] {t}")
        if tp.exists():
            bak = _backup(tp)
            print(f"    backup: {bak.name}")
        else:
            tp.mkdir(parents=True, exist_ok=True)
            print(f"    新規 profile dir 作成")
        copied = _copy_cookie_assets(src_path, tp)
        print(f"    copied: {copied}")

    # 各 target で fingerprint 検証
    print(f"\n=== Step 4: 複製後 fingerprint 検証 ===")
    for t in targets:
        fp = verify_profile_fingerprint(t)
        ok = "✅" if fp.get("item_count", 0) >= args.min_items else "❌"
        print(f"  {ok} {t}: items={fp.get('item_count')}, followers={fp.get('follower_count')}")

    print(f"\n=== 完了 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
