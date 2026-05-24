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
_VM_V6 = _THIS.parent.parent          # ops/vm_v6  (or \\VBoxSvr\vm_v6)
try:
    _REPO = _VM_V6.parents[1]         # repo root (UNC paths may not have this)
except IndexError:
    _REPO = _VM_V6
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

import re as _re


def _parse_room_stats(html: str) -> dict:
    """HTML 文字列から ROOM 累計数値を regex で抽出.

    page.evaluate (JS 実行コンテキスト) の代わりに Python 正規表現を使用し、
    ナビゲーション中のコンテキスト破棄エラーを回避する。
    """
    def grab(label: str) -> int | None:
        """ラベル直後の数値を全件収集して最大値を返す.

        楽天 ROOM SPA は大きな数値を「21K」「11K」「24K」のように K サフィックス
        で表示するため、K を捕捉して 1000倍する。
        例: フォロー 21K → 21000 / フォロワー 11K → 11000 / いいね 24K → 24000
        コンマ区切り整数も対応: 商品 3,531 → 3531
        """
        matches = _re.findall(label + r'[^0-9]*?([\d,]+[Kk]?)', html)
        if not matches:
            return None
        values: list[int] = []
        for m in matches:
            m = m.strip()
            try:
                if m.upper().endswith('K'):
                    values.append(int(m[:-1].replace(',', '')) * 1000)
                else:
                    values.append(int(m.replace(',', '')))
            except ValueError:
                pass
        return max(values) if values else None

    return {
        "item_count":        grab('商品'),
        "follower_count":    grab('フォロワー'),
        "follow_count":      grab('フォロー(?!ー)'),
        "coordinate_count":  grab('コーディネート'),
        "collection_count":  grab('コレクション'),
        "like_count":        grab('いいね'),
    }


def fetch() -> dict:
    """Playwright で ROOM プロフィールページから累計数値を取得.

    page.evaluate (JS) は navigation 中に execution context が破棄されるため、
    page.content() でHTMLを取得後 Python regex でパースする方式に変更。
    """
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
            # ROOM プロフィールページへ遷移
            page.goto(ROOM_URL, timeout=30000, wait_until="commit")

            # SPA navigate/redirect が落ち着くまで待機
            # wait_for_url で room_id を含む URL に到達するのを確認
            try:
                page.wait_for_url(f"**{ROOM_ID}**", timeout=20000)
            except Exception:
                pass

            # JS レンダリング完了のため 5 秒待機 (SPA stats 描画)
            page.wait_for_timeout(5000)

            final_url = page.url

            # page.inner_text("body") は navigate 中でも比較的安定して動作する
            # page.evaluate (JS context 依存) / page.content() (HTML のみ) より堅牢
            body_text = ""
            for _attempt in range(3):
                try:
                    body_text = page.inner_text("body")
                    break
                except Exception:
                    page.wait_for_timeout(1500)
            if not body_text:
                return {"_error": f"page.inner_text() failed after 3 attempts: url={final_url}"}

            stats = _parse_room_stats(body_text)

            # None が多い場合はパース失敗疑い
            valid = sum(1 for v in stats.values() if v is not None)
            if valid < 2:
                snippet = body_text[:300].replace('\n', ' ')
                return {"_error": f"DOM parse failed (valid={valid}/6) url={final_url} text_head={snippet[:200]}"}
            stats["_profile_used"] = str(PROFILE_DIR.name)
            stats["url"] = final_url
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
