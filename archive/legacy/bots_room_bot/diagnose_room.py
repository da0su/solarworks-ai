"""楽天ROOM 診断スクリプト

CDP接続で実際のROOMサイトのDOM構造を解析し、
正しいURL・いいねボタンセレクタを特定する。

使い方:
  1. Chrome を --remote-debugging-port=9222 で起動
  2. 楽天ROOMにログイン済みの状態にする
  3. python diagnose_room.py を実行

出力:
  - 各候補URLの到達結果
  - ページ上のリンク一覧
  - いいねボタン候補の検出結果
  - DOM構造のスナップショット
"""

import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import config
from executor.browser_manager import BrowserManager

DIAG_DIR = config.DATA_DIR / "diagnose"
DIAG_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


def save_screenshot(bm, label):
    path = DIAG_DIR / f"{timestamp}_{label}.png"
    bm.page.screenshot(path=str(path), full_page=False)
    print(f"  📸 {path.name}")
    return path


def phase1_url_discovery(bm):
    """Phase 1: どのURLが有効か確認"""
    print("\n" + "=" * 60)
    print("Phase 1: URL到達テスト")
    print("=" * 60)

    candidate_urls = [
        # トップ・メイン
        "https://room.rakuten.co.jp/",
        "https://room.rakuten.co.jp/items",
        # フィード系（旧URL含む）
        "https://room.rakuten.co.jp/all/feed",
        "https://room.rakuten.co.jp/all/ranking",
        "https://room.rakuten.co.jp/feed",
        "https://room.rakuten.co.jp/ranking",
        # カテゴリ系
        "https://room.rakuten.co.jp/all",
        "https://room.rakuten.co.jp/my/feed",
        "https://room.rakuten.co.jp/myroom",
        "https://room.rakuten.co.jp/my",
        # 新着・人気
        "https://room.rakuten.co.jp/all/new",
        "https://room.rakuten.co.jp/all/popular",
        "https://room.rakuten.co.jp/new",
        "https://room.rakuten.co.jp/popular",
        "https://room.rakuten.co.jp/hashtag",
    ]

    results = []
    for url in candidate_urls:
        try:
            bm.page.goto(url, wait_until="domcontentloaded", timeout=10000)
            bm.page.wait_for_timeout(2000)
            final_url = bm.page.url
            title = bm.page.title()

            # 404判定
            is_404 = False
            try:
                el_404 = bm.page.locator("text=404").first
                if el_404.is_visible(timeout=500):
                    is_404 = True
            except Exception:
                pass

            status = "404" if is_404 else "OK"
            redirect = f" → {final_url}" if final_url != url else ""
            print(f"  [{status}] {url}{redirect}")

            results.append({
                "url": url,
                "final_url": final_url,
                "title": title,
                "is_404": is_404,
            })
        except Exception as e:
            print(f"  [ERR] {url} → {e}")
            results.append({"url": url, "error": str(e)})

    return results


def phase2_page_analysis(bm, url, label):
    """Phase 2: 特定ページのDOM構造を詳細解析"""
    print(f"\n{'=' * 60}")
    print(f"Phase 2: DOM解析 - {url}")
    print("=" * 60)

    try:
        bm.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        bm.page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  ❌ 遷移失敗: {e}")
        return None

    final_url = bm.page.url
    print(f"  URL: {final_url}")
    save_screenshot(bm, f"page_{label}")

    # ページ上のリンク一覧（ROOM内部リンクのみ）
    print(f"\n  --- ページ内リンク ---")
    links = bm.page.evaluate("""() => {
        const links = document.querySelectorAll('a[href*="room.rakuten.co.jp"]');
        return Array.from(links).slice(0, 50).map(a => ({
            href: a.href,
            text: (a.textContent || '').trim().substring(0, 60),
            className: a.className.substring(0, 80),
        }));
    }""")
    for link in links[:30]:
        print(f"    {link['href'][:80]}  [{link['text'][:30]}]")

    # ナビゲーション要素
    print(f"\n  --- ナビゲーション要素 ---")
    nav_info = bm.page.evaluate("""() => {
        const navs = document.querySelectorAll('nav, [role="navigation"], header');
        return Array.from(navs).map(n => ({
            tag: n.tagName,
            className: n.className.substring(0, 80),
            links: Array.from(n.querySelectorAll('a')).map(a => ({
                href: a.href,
                text: (a.textContent || '').trim().substring(0, 40),
            })),
        }));
    }""")
    for nav in nav_info:
        print(f"    <{nav['tag']} class='{nav['className'][:50]}'>")
        for link in nav.get('links', [])[:10]:
            print(f"      → {link['href'][:60]}  [{link['text'][:30]}]")

    return {"url": final_url, "links": links, "nav": nav_info}


def phase3_like_button_hunt(bm, url, label):
    """Phase 3: いいねボタン候補を探す"""
    print(f"\n{'=' * 60}")
    print(f"Phase 3: いいねボタン探索 - {url}")
    print("=" * 60)

    try:
        bm.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        bm.page.wait_for_timeout(4000)
    except Exception as e:
        print(f"  ❌ 遷移失敗: {e}")
        return None

    # 少しスクロールしてコンテンツをロード
    for _ in range(3):
        bm.page.evaluate("window.scrollBy(0, 500)")
        bm.page.wait_for_timeout(1500)

    save_screenshot(bm, f"like_hunt_{label}")

    # 包括的にボタン・クリック要素を検出
    print(f"\n  --- 全button要素 ---")
    buttons = bm.page.evaluate("""() => {
        const btns = document.querySelectorAll('button');
        return Array.from(btns).slice(0, 40).map(b => ({
            text: (b.textContent || '').trim().substring(0, 50),
            className: b.className.substring(0, 100),
            ariaLabel: b.getAttribute('aria-label') || '',
            ariaPressed: b.getAttribute('aria-pressed') || '',
            dataTestId: b.getAttribute('data-testid') || '',
            outerHTML: b.outerHTML.substring(0, 200),
            visible: b.offsetParent !== null,
        }));
    }""")
    for i, btn in enumerate(buttons):
        if btn.get('visible'):
            print(f"    [{i}] class='{btn['className'][:60]}'")
            print(f"        text='{btn['text'][:40]}' aria-label='{btn['ariaLabel']}'")
            if btn['ariaPressed']:
                print(f"        aria-pressed='{btn['ariaPressed']}'")
            if btn['dataTestId']:
                print(f"        data-testid='{btn['dataTestId']}'")

    # SVGハートアイコンを探す
    print(f"\n  --- SVG要素（ハート系） ---")
    svgs = bm.page.evaluate("""() => {
        const svgs = document.querySelectorAll('svg');
        return Array.from(svgs).slice(0, 30).map(s => {
            const parent = s.parentElement;
            return {
                className: s.className.baseVal || s.getAttribute('class') || '',
                parentTag: parent ? parent.tagName : '',
                parentClass: parent ? parent.className.substring(0, 80) : '',
                parentAriaLabel: parent ? (parent.getAttribute('aria-label') || '') : '',
                viewBox: s.getAttribute('viewBox') || '',
                width: s.getAttribute('width') || '',
                visible: s.offsetParent !== null,
            };
        });
    }""")
    for i, svg in enumerate(svgs):
        if svg.get('visible'):
            print(f"    [{i}] svg class='{svg['className'][:40]}' viewBox='{svg['viewBox']}'")
            print(f"        parent: <{svg['parentTag']} class='{svg['parentClass'][:50]}'>")

    # いいね・ハート・likeを含むクラス名の要素
    print(f"\n  --- 'like/heart/いいね' を含む要素 ---")
    like_elements = bm.page.evaluate("""() => {
        const all = document.querySelectorAll('*');
        const results = [];
        for (const el of all) {
            const cls = el.className || '';
            const clsStr = typeof cls === 'string' ? cls : (cls.baseVal || '');
            const aria = el.getAttribute('aria-label') || '';
            const testId = el.getAttribute('data-testid') || '';
            const text = (el.textContent || '').trim().substring(0, 30);

            if (clsStr.match(/like|heart|いいね|fav/i) ||
                aria.match(/like|heart|いいね|fav/i) ||
                testId.match(/like|heart|いいね|fav/i)) {
                results.push({
                    tag: el.tagName,
                    className: clsStr.substring(0, 100),
                    ariaLabel: aria,
                    dataTestId: testId,
                    text: text,
                    outerHTML: el.outerHTML.substring(0, 250),
                    visible: el.offsetParent !== null,
                });
            }
        }
        return results.slice(0, 30);
    }""")
    for i, el in enumerate(like_elements):
        vis = "👁" if el.get('visible') else "  "
        print(f"    {vis} [{i}] <{el['tag']} class='{el['className'][:60]}'>")
        if el['ariaLabel']:
            print(f"         aria-label='{el['ariaLabel']}'")
        if el['dataTestId']:
            print(f"         data-testid='{el['dataTestId']}'")
        print(f"         html: {el['outerHTML'][:150]}")

    # 投稿カード構造を確認
    print(f"\n  --- 投稿カード構造 ---")
    cards = bm.page.evaluate("""() => {
        // 投稿カードの候補セレクタ
        const selectors = [
            'article', '[class*="card"]', '[class*="item"]',
            '[class*="Card"]', '[class*="Item"]', '[class*="post"]',
            '[class*="Post"]', '[class*="Feed"]', '[class*="feed"]',
        ];
        const results = [];
        for (const sel of selectors) {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) {
                const first = els[0];
                results.push({
                    selector: sel,
                    count: els.length,
                    firstClass: first.className.substring(0, 100),
                    firstHTML: first.outerHTML.substring(0, 300),
                    childButtons: first.querySelectorAll('button').length,
                    childLinks: first.querySelectorAll('a').length,
                    childSVGs: first.querySelectorAll('svg').length,
                });
            }
        }
        return results;
    }""")
    for card in cards:
        print(f"    {card['selector']}: {card['count']}件")
        print(f"      class='{card['firstClass'][:60]}'")
        print(f"      buttons={card['childButtons']} links={card['childLinks']} svgs={card['childSVGs']}")
        print(f"      html: {card['firstHTML'][:200]}")

    return {"buttons": buttons, "svgs": svgs, "like_elements": like_elements, "cards": cards}


def main():
    print("=" * 60)
    print("楽天ROOM 診断スクリプト")
    print(f"実行時刻: {datetime.now().isoformat()}")
    print(f"CDP URL: {config.CDP_URL}")
    print("=" * 60)

    bm = BrowserManager()
    all_results = {}

    try:
        bm.start()

        # Phase 1: URL到達テスト
        url_results = phase1_url_discovery(bm)
        all_results["urls"] = url_results

        # 有効なURLを特定
        valid_urls = [r for r in url_results if not r.get("is_404") and not r.get("error")]
        print(f"\n  ✅ 有効なURL: {len(valid_urls)}件")
        for v in valid_urls:
            print(f"    {v['url']} → {v['final_url']}")

        if not valid_urls:
            print("  ❌ 有効なURLが見つかりません")
            return

        # Phase 2: 最初に有効なURLのDOM解析
        # トップページと items ページを優先
        priority_urls = []
        for target in ["room.rakuten.co.jp/items", "room.rakuten.co.jp/"]:
            for v in valid_urls:
                if target in v["final_url"]:
                    priority_urls.append(v["final_url"])
                    break

        if not priority_urls:
            priority_urls = [valid_urls[0]["final_url"]]

        for i, url in enumerate(priority_urls[:2]):
            label = f"valid_{i}"
            page_result = phase2_page_analysis(bm, url, label)
            all_results[f"page_{label}"] = page_result

            # Phase 3: いいねボタン探索（同じページで）
            like_result = phase3_like_button_hunt(bm, url, label)
            all_results[f"likes_{label}"] = like_result

    except Exception as e:
        print(f"\n❌ 診断エラー: {e}")
        import traceback
        traceback.print_exc()
    finally:
        bm.stop()

    # 結果をJSONで保存
    result_path = DIAG_DIR / f"{timestamp}_result.json"
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📄 診断結果保存: {result_path}")
    except Exception as e:
        print(f"  結果保存エラー: {e}")

    print("\n" + "=" * 60)
    print("診断完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
