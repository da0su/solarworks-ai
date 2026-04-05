"""eBay正方式 探索ラウンド2 - タイトルキーワード照合付き"""
import sys, json, time, random, re, statistics
from pathlib import Path
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.supabase_client import get_client

YAHOO_FEE_RATE = 0.10
IMPORT_TAX_RATE = 0.10
SHIPPING_US_FORWARD_JPY = 2000
SHIPPING_DOMESTIC_JPY = 750


def _get_fx_rate():
    """Supabaseから今日のUSD/JPYレートを取得"""
    try:
        from scripts.fetch_daily_rates import get_today_rate
        rate = get_today_rate("usd_jpy")
        if rate:
            return rate
    except Exception:
        pass
    return 161  # fallback

EBAY_COUNTRY_MAP = {
    "United States": "アメリカ", "United Kingdom": "イギリス",
    "Canada": "カナダ", "Australia": "オーストラリア",
    "France": "フランス", "Germany": "ドイツ",
    "Italy": "イタリア", "Switzerland": "スイス",
    "China": "中国", "Japan": "日本",
    "South Africa": "南アフリカ", "Austria": "オーストリア",
    "Peru": "ペルー", "Mexico": "メキシコ",
}

BLACKLIST = [
    "sacagawea", "presidential", "innovation", "plated",
    "filled", "layered", "clad", "nickel", "copper-nickel",
    "quarter", "kennedy", "jefferson", "lincoln", "stamp",
]

NOISE_KEYWORDS = {
    "ngc", "pcgs", "coin", "the", "and", "for", "proof", "ultra", "cameo",
    "dcam", "deep", "mint", "state", "graded", "certified", "free",
    "shipping", "rare", "new", "old", "buy", "get", "set", "lot", "with",
    "from", "gold", "silver", "platinum", "scarce", "gem", "nice", "great",
}

EXTRACT_JS = r"""() => {
    var pageTitle = document.title.replace(' | eBay', '').trim();
    var body = document.body.innerText;
    var usdMatch = body.match(/US\s*\$([\d,]+\.\d{2})/);
    var priceUsd = usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null;
    var specs = {};
    var fields = ['Composition', 'Fineness', 'Grade', 'Certification', 'Year',
                  'Denomination', 'Country of Origin', 'Country/Region of Manufacture',
                  'Coin', 'Mint Location'];
    for (var i = 0; i < fields.length; i++) {
        var field = fields[i];
        var rx1 = new RegExp(field + '\\n([^\\n]{1,60})');
        var rx2 = new RegExp(field + '\\s*\\|\\s*([^|\\n]{1,60})');
        var m = body.match(rx1) || body.match(rx2);
        if (m) specs[field] = m[1].trim();
    }
    return {title: pageTitle, priceUsd: priceUsd, specs: specs};
}"""

SEARCHES = [
    "NGC gold sovereign proof",
    "PCGS gold Morgan dollar",
    "NGC silver 1oz proof",
    "PCGS gold 10 dollars indian",
    "NGC gold britannia 1oz",
    "PCGS MS65 gold liberty",
    "NGC PF70 silver eagle proof",
    "NGC gold 20 franc",
]


def main():
    start_time = time.time()

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

    # Collect eBay items
    print("eBay item collection...")
    all_urls = set()
    for sq in SEARCHES:
        url = f"https://www.ebay.com/sch/i.html?_nkw={sq.replace(' ', '+')}&LH_PrefLoc=1&_udlo=4500"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            urls = page.evaluate("""() => {
                var links = document.querySelectorAll('a[href*="/itm/"]');
                var u = [];
                var seen = {};
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].getAttribute('href') || '';
                    var m = href.match(/ebay\\.com\\/itm\\/(\\d+)/);
                    if (m && !seen[m[1]]) {
                        seen[m[1]] = true;
                        u.push('https://www.ebay.com/itm/' + m[1]);
                    }
                }
                return u;
            }""")
            before = len(all_urls)
            all_urls.update(urls)
            added = len(all_urls) - before
            if added > 0:
                print(f"  '{sq}': +{added} ({len(all_urls)})")
        except Exception as e:
            print(f"  '{sq}': ERROR {e}")
        time.sleep(random.uniform(1.5, 3))
        if len(all_urls) >= 400:
            break

    items = list(all_urls)
    random.shuffle(items)
    max_check = min(80, len(items))
    print(f"\nTotal: {len(items)} -> checking {max_check}\n")

    client = get_client()
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    results = []
    checked = 0

    for item_url in items[:max_check]:
        checked += 1
        try:
            page.goto(item_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
        except Exception:
            continue

        try:
            info = page.evaluate(EXTRACT_JS)
        except Exception:
            continue
        title = info.get("title", "")
        price_usd = info.get("priceUsd")
        specs = info.get("specs", {})
        if not price_usd or not title:
            continue

        title_lower = title.lower()
        if any(bl in title_lower for bl in BLACKLIST):
            continue

        composition = specs.get("Composition", "").lower()
        valid_metals = {"gold", "silver", "platinum"}
        if not any(m in composition for m in valid_metals):
            if not any(m in title_lower for m in valid_metals):
                continue

        grader_m = re.search(r"\b(NGC|PCGS)\b", title)
        grade_m = re.search(r"\b(MS|PF|PR|AU)\s*\d{1,2}\w*", title, re.IGNORECASE)
        year_m = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", title)
        grader = grader_m.group() if grader_m else ""
        grade = grade_m.group().replace(" ", "") if grade_m else ""
        year = year_m.group() if year_m else ""
        if not grader or not year:
            continue
        try:
            year_int = int(year)
        except ValueError:
            continue

        ebay_country = specs.get("Country of Origin", "") or specs.get("Country/Region of Manufacture", "")
        jp_country = EBAY_COUNTRY_MAP.get(ebay_country, "")
        fx_rate = _get_fx_rate()
        price_jpy = int(price_usd * fx_rate)

        qb = (client.table("market_transactions")
            .select("title, price_jpy, sold_date, url")
            .eq("source", "yahoo")
            .eq("grader", grader)
            .eq("year", year_int)
            .gte("sold_date", cutoff)
            .gte("price_jpy", 10000)
            .order("sold_date", desc=True)
            .limit(30))
        if jp_country:
            qb = qb.eq("country", jp_country)
        try:
            resp = qb.execute()
            yahoo_all = resp.data
        except Exception:
            continue
        if not yahoo_all:
            continue

        # スラブ1行目一致マッチング（coin_matcher使用）
        from scripts.coin_matcher import extract_slab_key, is_same_coin
        ebay_slab = extract_slab_key(title)

        yahoo_filtered = []
        for r in yahoo_all:
            yahoo_slab = extract_slab_key(r.get("title", "") or "")
            match, reason = is_same_coin(ebay_slab, yahoo_slab)
            if match:
                yahoo_filtered.append(r)
        if not yahoo_filtered:
            continue

        yahoo_prices = [r["price_jpy"] for r in yahoo_filtered if r.get("price_jpy")]
        if not yahoo_prices:
            continue
        yahoo_median = int(statistics.median(yahoo_prices))

        # 確定版原価: (商品×為替×1.1) + 転送2000 + 国内750
        cost_jpy = int(price_usd * fx_rate * (1 + IMPORT_TAX_RATE)) + SHIPPING_US_FORWARD_JPY + SHIPPING_DOMESTIC_JPY
        yahoo_net = int(yahoo_median * (1 - YAHOO_FEE_RATE))
        profit = yahoo_net - cost_jpy
        profit_pct = round(profit / cost_jpy * 100, 1) if cost_jpy > 0 else 0

        result = {
            "ebay_url": item_url, "ebay_title": title,
            "ebay_price_usd": price_usd, "ebay_price_jpy": price_jpy,
            "grader": grader, "grade": grade, "year": year,
            "ebay_country": ebay_country, "composition": composition,
            "yahoo_count": len(yahoo_filtered), "yahoo_median_jpy": yahoo_median,
            "yahoo_records": [
                {"title": r["title"][:80], "price": r["price_jpy"],
                 "date": r["sold_date"], "url": r.get("url", "")}
                for r in yahoo_filtered[:5]
            ],
            "cost_jpy": cost_jpy, "profit_jpy": profit, "profit_pct": profit_pct,
        }
        results.append(result)
        print(f"[{checked}] {title[:65]}")
        print(f"    eBay: ${price_usd:,.2f} | {item_url}")
        print(f"    Yahoo: Y{yahoo_median:,} ({len(yahoo_filtered)}件) | profit: {profit_pct}%")
        for yr in result["yahoo_records"][:2]:
            print(f"      Y{yr['price']:,} | {yr['url']}")

        # 1件ごとに即保存（クラッシュ耐性）
        out = PROJECT_ROOT / "data" / "ebay_first_v4.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        time.sleep(random.uniform(2, 4))

    try:
        browser.close()
        pw.stop()
    except Exception:
        pass

    elapsed = time.time() - start_time
    out = PROJECT_ROOT / "data" / "ebay_first_v4.json"

    print(f"\n{'=' * 60}")
    print(f"Done: {len(results)} matched / {checked} checked | {elapsed:.0f}s")
    results.sort(key=lambda x: x["yahoo_count"], reverse=True)
    for i, r in enumerate(results[:6], 1):
        print(f"\n--- Candidate {i}: {r['ebay_title'][:60]} ---")
        print(f"  eBay: ${r['ebay_price_usd']:,.2f} | {r['ebay_url']}")
        print(f"  Yahoo: Y{r['yahoo_median_jpy']:,} ({r['yahoo_count']}件) | Profit: {r['profit_pct']}%")
        for yr in r["yahoo_records"][:3]:
            print(f"    Y{yr['price']:,} | {yr['date']} | {yr['url']}")
            print(f"      {yr['title'][:60]}")


if __name__ == "__main__":
    main()
