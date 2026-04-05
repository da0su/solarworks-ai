"""高速一括探索スクリプト（最終方針版）

STEP1: eBay 300-500件一括取得（タイトル+価格+画像URL）
STEP2: タイトル正規化（year/mint/series/denom/material/grade/label）
STEP3: ヤフオクDB一括照合（全件まとめて突合）
STEP4: 精密判定（候補のみOCR）
STEP5: CEO提出レポート生成

速度目標: 30分で300-1000件スキャン
"""
import json
import math
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from scripts.coin_matcher import extract_slab_key, is_same_coin
from scripts.fetch_daily_rates import get_today_rate

# ============================================================
# 設定
# ============================================================

YAHOO_FEE_RATE = 0.10
IMPORT_TAX_RATE = 0.10
SHIPPING_US_FORWARD_JPY = 2000
SHIPPING_DOMESTIC_JPY = 750

BLACKLIST_TITLE = [
    "sacagawea", "presidential", "innovation", "plated",
    "filled", "layered", "clad", "nickel", "copper-nickel",
    "quarter", "kennedy", "jefferson", "lincoln", "stamp",
    "token", "medal", "bar", "round", "ingot",
]

VALID_METALS = {"gold", "silver", "platinum"}

# eBay検索クエリ群（幅広くカバー）
SEARCH_QUERIES_ROUND1 = [
    # Gold - US
    "NGC gold coin proof", "PCGS gold coin proof",
    "NGC gold eagle $20", "NGC gold eagle $10", "NGC gold eagle $5",
    "PCGS MS65 gold liberty", "PCGS MS63 gold saint gaudens",
    "NGC gold buffalo proof",
    # Gold - UK
    "NGC gold sovereign proof", "PCGS gold sovereign",
    "NGC gold britannia 1oz", "NGC gold 5 pounds proof",
    # Gold - Other
    "NGC gold panda", "NGC gold maple leaf",
    "NGC gold kangaroo", "NGC gold krugerrand",
    "NGC gold 20 franc", "NGC gold ducat",
    # Silver - US
    "NGC PF70 silver eagle", "PCGS PF70 silver eagle",
    "NGC MS70 silver eagle", "NGC silver morgan dollar",
    "PCGS MS65 morgan dollar", "PCGS MS64 morgan dollar",
    # Silver - UK
    "NGC silver proof pound", "NGC PF70 britannia silver",
    "NGC silver queens beast", "NGC silver kings beast",
    # Silver - Other
    "NGC silver panda 30g", "NGC silver maple leaf proof",
    "NGC silver kookaburra", "NGC silver lunar",
    # Platinum
    "NGC platinum eagle proof", "PCGS platinum coin",
]

SEARCH_QUERIES_ROUND2 = [
    # Silver - UK heavy
    "NGC PF70 silver 5 pounds", "PCGS PR70 silver 5 pounds",
    "NGC PF69 silver britannia", "NGC silver proof crown",
    "NGC silver proof UK coin", "PCGS silver proof UK",
    # Silver - Australia
    "NGC PF70 silver kookaburra", "NGC PF70 silver koala",
    "NGC MS70 silver kangaroo", "NGC silver wedge tailed eagle",
    # Silver - Canada
    "NGC PF70 silver maple leaf", "PCGS PF70 silver maple",
    # Silver - China
    "NGC MS70 silver panda 30g", "PCGS MS70 silver panda",
    # Gold - more specific
    "NGC PF70 gold 1oz", "PCGS PR70 gold proof",
    "NGC MS70 gold 1/4oz", "NGC gold 1/2oz proof",
    "PCGS gold Indian $10", "PCGS gold Indian $5",
    # Niche
    "NGC PF70 silver proof coin rare", "NGC silver proof limited",
    "PCGS gold rare mintage", "NGC MS66 gold coin",
    # Mixed
    "NGC PF70 coin proof", "PCGS PR70 DCAM coin",
    "NGC MS69 gold coin", "PCGS MS70 silver coin",
]

SEARCH_QUERIES_ROUND3 = [
    # Silver - US proofs
    "NGC PF69 silver eagle proof", "PCGS PF69 silver eagle",
    "NGC PF70 silver buffalo", "NGC PF70 silver liberty",
    # Gold - various sizes
    "NGC gold 1/4oz proof", "NGC gold 1/2oz proof",
    "PCGS gold 1/4oz", "NGC gold 1oz proof coin",
    # Silver - world
    "NGC PF70 silver proof Australia", "NGC PF70 silver proof Canada",
    "NGC PF70 silver proof China", "NGC PF70 silver New Zealand",
    "NGC PF69 silver proof UK", "PCGS PF69 silver proof",
    # Gold - world
    "NGC PF70 gold Australia", "NGC PF70 gold Canada",
    "NGC gold libertad proof", "NGC gold philharmonic",
    # Morgan/Peace
    "PCGS MS63 morgan dollar", "PCGS MS64 peace dollar",
    "NGC MS64 morgan dollar", "NGC MS65 peace dollar",
    # Misc
    "NGC PF70 silver proof 1oz", "PCGS PR69 silver proof",
    "NGC MS70 gold 1/10oz", "NGC PF70 ultra cameo silver",
]

SEARCH_QUERIES = SEARCH_QUERIES_ROUND3

EXTRACT_URLS_JS = """() => {
    var links = document.querySelectorAll('a[href*="/itm/"]');
    var u = {};
    for (var i = 0; i < links.length; i++) {
        var href = links[i].getAttribute('href') || '';
        var m = href.match(/ebay\\.com\\/itm\\/(\\d+)/);
        if (m) u[m[1]] = 'https://www.ebay.com/itm/' + m[1];
    }
    return Object.values(u);
}"""

EXTRACT_ITEM_JS = r"""() => {
    var pageTitle = document.title.replace(' | eBay', '').trim();
    var body = document.body.innerText;
    var usdMatch = body.match(/US\s*\$([\d,]+\.\d{2})/);
    var priceUsd = usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null;

    // Shipping
    var shipMatch = body.match(/Shipping:\s*US\s*\$([\d,]+\.\d{2})/);
    var shipUsd = shipMatch ? parseFloat(shipMatch[1].replace(/,/g, '')) : 0;
    if (body.indexOf('Free') >= 0 && body.indexOf('shipping') >= 0) shipUsd = 0;

    // Image URL (first product image)
    var imgs = document.querySelectorAll('img[src*="i.ebayimg.com"]');
    var imgUrl = '';
    for (var i = 0; i < imgs.length; i++) {
        var src = imgs[i].getAttribute('src') || '';
        if (src.indexOf('s-l') >= 0 || src.indexOf('/images/') >= 0) {
            imgUrl = src;
            break;
        }
    }

    return {title: pageTitle, priceUsd: priceUsd, shipUsd: shipUsd, imgUrl: imgUrl};
}"""


def collect_ebay_urls(page, max_items=500):
    """STEP1: eBay検索結果からURLを一括収集"""
    all_urls = set()
    for sq in SEARCH_QUERIES:
        url = f"https://www.ebay.com/sch/i.html?_nkw={sq.replace(' ', '+')}&LH_PrefLoc=1&_udlo=4500"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(1500)
            urls = page.evaluate(EXTRACT_URLS_JS)
            before = len(all_urls)
            all_urls.update(urls)
            added = len(all_urls) - before
            if added > 0:
                print(f"  '{sq}': +{added} ({len(all_urls)})")
        except Exception:
            pass
        time.sleep(1)
        if len(all_urls) >= max_items:
            print(f"  {max_items}件到達、収集打ち切り")
            break
    return list(all_urls)


def extract_item_data(page, urls, max_check=300):
    """STEP1b: 各商品ページからタイトル・価格・画像URLを高速取得"""
    items = []
    for i, url in enumerate(urls[:max_check]):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1500)
            info = page.evaluate(EXTRACT_ITEM_JS)
        except Exception:
            continue

        title = info.get("title", "")
        price_usd = info.get("priceUsd")
        if not price_usd or not title:
            continue

        title_lower = title.lower()
        if any(bl in title_lower for bl in BLACKLIST_TITLE):
            continue
        if not any(m in title_lower for m in VALID_METALS):
            continue

        items.append({
            "url": url,
            "title": title,
            "price_usd": price_usd,
            "ship_usd": info.get("shipUsd", 0),
            "img_url": info.get("imgUrl", ""),
        })

        if (i + 1) % 50 == 0:
            print(f"  取得済: {len(items)}件 / {i+1}巡回")

        time.sleep(0.5)

    return items


def normalize_and_match(items, fx_rate):
    """STEP2-4: 正規化 → ヤフオクDB一括照合 → 利益計算"""
    client = get_client()
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    results = []

    # STEP2: 全eBayアイテムを正規化
    normalized = []
    for item in items:
        slab = extract_slab_key(item["title"])
        if not slab["year"]:
            continue
        item["slab"] = slab
        normalized.append(item)

    print(f"\n[STEP2] 正規化完了: {len(normalized)}件")

    # STEP3-4: ヤフオクDB照合
    # yearごとにグループ化して一括クエリ
    by_year = {}
    for item in normalized:
        y = item["slab"]["year"]
        if y not in by_year:
            by_year[y] = []
        by_year[y].append(item)

    print(f"[STEP3] 年号グループ: {len(by_year)}年分")

    for year_str, year_items in by_year.items():
        try:
            year_int = int(year_str)
        except ValueError:
            continue

        # この年の全ヤフオクレコードを一括取得
        try:
            resp = (client.table("market_transactions")
                .select("title, price_jpy, sold_date, url, grader, grade")
                .eq("source", "yahoo")
                .eq("year", year_int)
                .gte("sold_date", cutoff)
                .gte("price_jpy", 10000)
                .order("sold_date", desc=True)
                .limit(500)
                .execute())
            yahoo_records = resp.data
        except Exception:
            continue

        if not yahoo_records:
            continue

        # 各ヤフオクレコードも正規化
        yahoo_normalized = []
        for yr in yahoo_records:
            ys = extract_slab_key(yr.get("title", "") or "")
            yr["slab"] = ys
            yahoo_normalized.append(yr)

        # eBayアイテムごとにヤフオクとマッチング
        for item in year_items:
            matches = []
            for yr in yahoo_normalized:
                is_match, reason = is_same_coin(item["slab"], yr["slab"])
                if is_match:
                    matches.append(yr)

            if not matches:
                continue

            # 利益計算
            yahoo_prices = [r["price_jpy"] for r in matches if r.get("price_jpy")]
            if not yahoo_prices:
                continue
            yahoo_median = int(statistics.median(yahoo_prices))

            item_jpy = item["price_usd"] * fx_rate
            ship_jpy = item.get("ship_usd", 0) * fx_rate
            cost = int(item_jpy * (1 + IMPORT_TAX_RATE) + ship_jpy + SHIPPING_US_FORWARD_JPY + SHIPPING_DOMESTIC_JPY)
            yahoo_net = int(yahoo_median * (1 - YAHOO_FEE_RATE))
            profit = yahoo_net - cost
            profit_pct = round(profit / cost * 100, 1) if cost > 0 else 0

            results.append({
                "ebay_url": item["url"],
                "ebay_title": item["title"],
                "ebay_price_usd": item["price_usd"],
                "ebay_ship_usd": item.get("ship_usd", 0),
                "ebay_img_url": item.get("img_url", ""),
                "ebay_slab": item["slab"]["slab_key"],
                "yahoo_count": len(matches),
                "yahoo_median_jpy": yahoo_median,
                "yahoo_records": [
                    {"title": r["title"][:80], "price": r["price_jpy"],
                     "date": r["sold_date"], "url": r.get("url", ""),
                     "slab_key": r["slab"]["slab_key"]}
                    for r in matches[:5]
                ],
                "cost_jpy": cost,
                "profit_jpy": profit,
                "profit_pct": profit_pct,
                "fx_rate": fx_rate,
            })

    return results


def generate_report(results):
    """CEO提出用レポート生成"""
    results.sort(key=lambda x: x["profit_pct"], reverse=True)

    profitable = [r for r in results if r["profit_pct"] > 0]
    near_miss = [r for r in results if -15 <= r["profit_pct"] <= 0]

    print(f"\n{'='*70}")
    print(f"  探索結果レポート")
    print(f"{'='*70}")
    print(f"  マッチ総数:   {len(results)}件")
    print(f"  利益あり:     {len(profitable)}件")
    print(f"  惜しい:       {len(near_miss)}件（-15%以内）")
    print(f"  為替レート:   {results[0]['fx_rate'] if results else 'N/A'}円/USD")

    for i, r in enumerate(profitable[:10], 1):
        print(f"\n--- 利益候補{i}: 利益率 {r['profit_pct']}% ---")
        print(f"  eBay: ${r['ebay_price_usd']:,.2f} (+送料${r['ebay_ship_usd']:.2f})")
        print(f"  URL:  {r['ebay_url']}")
        print(f"  スラブ: {r['ebay_slab']}")
        print(f"  原価: Y{r['cost_jpy']:,} | 手取: Y{int(r['yahoo_median_jpy']*0.9):,} | 利益: Y{r['profit_jpy']:,}")
        print(f"  Yahoo: {r['yahoo_count']}件 (中央値 Y{r['yahoo_median_jpy']:,})")
        for yr in r["yahoo_records"][:3]:
            print(f"    Y{yr['price']:,} | {yr['date']} | {yr['url']}")
            print(f"      slab: {yr['slab_key']}")

    if near_miss:
        print(f"\n--- 惜しい候補（-15%以内）---")
        for r in near_miss[:5]:
            print(f"  [{r['profit_pct']}%] ${r['ebay_price_usd']:,.2f} vs Y{r['yahoo_median_jpy']:,} | {r['ebay_slab']}")
            print(f"    {r['ebay_url']}")


def main():
    start_time = time.time()

    # 為替レート取得
    fx_rate = get_today_rate("usd_jpy") or 161
    print(f"為替レート: {fx_rate}円/USD")

    # ブラウザ起動
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="America/New_York",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = ctx.new_page()

    # STEP1: eBay URL一括収集
    print("\n[STEP1] eBay URL収集...")
    urls = collect_ebay_urls(page, max_items=500)
    print(f"  合計: {len(urls)}件")

    # STEP1b: 商品データ取得
    print(f"\n[STEP1b] 商品データ取得（{min(300, len(urls))}件）...")
    items = extract_item_data(page, urls, max_check=300)
    print(f"  有効アイテム: {len(items)}件")

    try:
        browser.close()
        pw.stop()
    except Exception:
        pass

    # STEP2-4: 正規化 → 照合 → 利益計算
    print(f"\n[STEP2-4] 正規化・照合・利益計算...")
    results = normalize_and_match(items, fx_rate)
    print(f"  マッチ結果: {len(results)}件")

    # 保存
    out_path = PROJECT_ROOT / "data" / "bulk_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print(f"\n所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")

    # レポート
    generate_report(results)


if __name__ == "__main__":
    main()
