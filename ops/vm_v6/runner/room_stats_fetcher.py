#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM 内で KAPIBARAN ROOM 累計統計を取得して JSON で stdout に出力.

VM の chrome_profile_follow (KAPIBARAN session 有) を使用して
room.rakuten.co.jp/room_e05d4d1c1e にアクセスし累計数値を返す.

Usage:
    python -m runner.room_stats_fetcher
    → stdout: {"item_count":N,"follower_count":N,...} or {"_error":"..."}

Exit codes:
    0  success
    1  error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ---- path setup -------------------------------------------------------
_THIS = Path(__file__).resolve()
_VM_V6 = _THIS.parent.parent          # ops/vm_v6
_REPO  = _VM_V6.parents[1]           # repo root
sys.path.insert(0, str(_VM_V6))

ROOM_ID  = "room_e05d4d1c1e"
ROOM_URL = f"https://room.rakuten.co.jp/{ROOM_ID}"

# Chrome profiles dir (VM 内)
try:
    from runner.shared_logic import BASE_DIR
    PROFILE_DIR = BASE_DIR / "data" / "chrome_profile_follow"
except Exception:
    # fallback: VM default path
    PROFILE_DIR = Path(r"C:\Users\cyber\Desktop\rakuten_room_bot\data\chrome_profile_follow")

CHROME_EXEC = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

PLAYWRIGHT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-restore-last-session",
    "--disable-session-crashed-bubble",
]

_GRAB_JS = r"""() => {
    const txt = document.body ? document.body.innerText : '';
    const grab = (label) => {
        const m = txt.match(new RegExp(label + '[^0-9]*(\\d[\\d,]*)'));
        return m ? parseInt(m[1].replace(/,/g, '')) : null;
    };
    return {
        item_count:        grab('商品'),
        follower_count:    grab('フォロワー'),
        follow_count:      grab('フォロー(?!ー)'),
        coordinate_count:  grab('コーディネート'),
        collection_count:  grab('コレクション'),
        like_count:        grab('いいね'),
    };
}"""


def fetch() -> dict:
    """Playwright で ROOM プロフィールページから累計数値を取得."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            executable_path=CHROME_EXEC,
            headless=True,
            args=PLAYWRIGHT_ARGS,
            no_viewport=False,
            viewport={"width": 1280, "height": 800},
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(ROOM_URL, timeout=20000)
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            page.wait_for_timeout(2000)   # JS レンダリング待機
            stats = page.evaluate(_GRAB_JS)
            # None が多い場合はパース失敗疑い
            valid = sum(1 for v in stats.values() if v is not None)
            if valid < 2:
                return {"_error": f"DOM parse failed (valid={valid}/6): page={page.url}"}
            stats["_profile_used"] = str(PROFILE_DIR.name)
            stats["url"] = page.url
            return stats
        finally:
            ctx.close()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    result = fetch()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if "_error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
