"""my ROOM page で実際の商品数と最新投稿日時を取得 (CEO 5/17 真因究明).

【CEO ルール】
投稿成功 = ROOM ページの商品数が増えること.
DB status='posted' は信用できない (5/10 時点で 338件 false success 累積).
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

import config
from executor.browser_manager import BrowserManager


def main():
    print(f"[{datetime.now()}] my ROOM page 商品数確認")
    bm = BrowserManager(action="post")
    bm.start()
    status = bm.check_login_status()
    if not status.get("logged_in"):
        print("ERR: not logged in")
        bm.stop()
        return 1

    page = bm.page
    # my ROOM items page
    page.goto("https://room.rakuten.co.jp/my/items", timeout=30000)
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    import time
    time.sleep(5)  # angular app render

    out_path = ROOT.parent / "state" / "my_room_check.png"
    page.screenshot(path=str(out_path), full_page=False)

    # 商品数を取得
    try:
        # 「商品 N」みたいなテキスト探す
        count_text = page.evaluate("""() => {
            const els = document.querySelectorAll('*');
            for (const el of els) {
                const t = (el.innerText || '').trim();
                // 「商品\\n3500」みたいなパターン
                const m = t.match(/^商品\\s*\\n?(\\d+)$/);
                if (m) return {pattern: 'tab', count: parseInt(m[1])};
            }
            // h2/span 等の数字単独
            const counts = [];
            document.querySelectorAll('[class*="count"], [class*="num"], h2, h3').forEach(el => {
                const t = (el.innerText || '').trim();
                if (/^\\d{3,5}$/.test(t)) counts.push({tag: el.tagName, cls: el.className.slice(0,40), n: parseInt(t)});
            });
            return {pattern: 'fallback', counts: counts.slice(0, 10)};
        }""")
        print(f"商品数: {count_text}")
    except Exception as e:
        print(f"商品数取得 ERR: {e}")

    # 最新投稿数件の item の post 日時を取得
    try:
        items = page.evaluate("""() => {
            const items = [];
            // 商品 card 候補
            document.querySelectorAll('a[href*="/items/"]').forEach(el => {
                const href = el.getAttribute('href') || '';
                const m = href.match(/\\/items\\/(\\d+)/);
                if (m) {
                    items.push({
                        item_id: m[1],
                        href: href,
                        text: (el.innerText || '').slice(0, 50).trim(),
                    });
                }
            });
            return items.slice(0, 10);
        }""")
        print(f"最新 item 候補 (top 10):")
        for it in items:
            print(f"  item_id={it['item_id']} href={it['href'][:60]} text={it['text'][:30]!r}")
    except Exception as e:
        print(f"item 取得 ERR: {e}")

    # ページ全体のテキストから「商品 N」相当を抽出
    try:
        body_text = page.evaluate("() => document.body.innerText.slice(0, 1000)")
        print(f"\n=== page text (first 1000 chars) ===")
        print(body_text)
    except Exception:
        pass

    bm.stop()
    print(f"\nscreenshot: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
