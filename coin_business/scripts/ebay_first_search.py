"""eBay正方式の探索スクリプト

eBay出品中アイテム → Item Specifics抽出 → Supabase照合（5条件マッチ）
"""
import re
import sys
import json
import time
import random
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.supabase_client import get_client

USD_TO_JPY = 149

# eBay Country -> Supabase country
EBAY_COUNTRY_MAP = {
    "United States": "アメリカ", "US": "アメリカ", "USA": "アメリカ",
    "United Kingdom": "イギリス", "Great Britain": "イギリス", "UK": "イギリス",
    "Canada": "カナダ", "Australia": "オーストラリア",
    "France": "フランス", "Germany": "ドイツ",
    "Italy": "イタリア", "Switzerland": "スイス",
    "China": "中国", "Japan": "日本", "Mexico": "メキシコ",
    "South Africa": "南アフリカ", "Austria": "オーストリア",
    "Peru": "ペルー", "Russia": "ロシア",
}

# eBay Denomination -> Supabase denomination
EBAY_DENOM_MAP = {
    "$1": "1ドル", "$5": "5ドル", "$10": "10ドル", "$20": "20ドル", "$50": "50ドル",
    "1 Dollar": "1ドル", "5 Dollars": "5ドル", "10 Dollars": "10ドル",
    "20 Dollars": "20ドル", "50 Dollars": "50ドル",
    "1 Pound": "1ポンド", "2 Pounds": "2ポンド", "5 Pounds": "5ポンド",
    "10 Pounds": "10ポンド", "25 Pounds": "25ポンド", "100 Pounds": "100ポンド",
    "1 Sovereign": "1ソブリン", "Half Sovereign": "ハーフソブリン",
    "10 Yuan": "10元", "50 Yuan": "50元",
    "100 Soles": "100ソル",
    "20 Francs": "20フラン", "100 Lire": "100リラ",
    "20 Mark": "20マルク",
}

EXTRACT_SPECS_JS = r"""() => {
    const pageTitle = document.title.replace(' | eBay', '').trim();
    const body = document.body.innerText;
    const usdMatch = body.match(/US\s*\$([\d,]+\.\d{2})/);
    const priceUsd = usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null;

    const specs = {};
    const fields = ['Composition', 'Fineness', 'Grade', 'Certification', 'Year',
                    'Denomination', 'Country of Origin', 'Country/Region of Manufacture',
                    'Coin', 'Mint Location', 'Strike Type'];

    for (const field of fields) {
        const regexes = [
            new RegExp(field + '\\n([^\\n]{1,60})'),
            new RegExp(field + '\\s*\\|\\s*([^|\\n]{1,60})'),
        ];
        for (const rx of regexes) {
            const m = body.match(rx);
            if (m) {
                specs[field] = m[1].trim();
                break;
            }
        }
    }

    return {title: pageTitle, priceUsd, specs};
}"""

# 素材は Gold / Silver / Platinum のみ。非貴金属コインは除外。
VALID_COMPOSITIONS = {"gold", "silver", "platinum"}

SEARCH_QUERIES = [
    # Gold
    "NGC gold sovereign", "PCGS gold sovereign",
    "NGC gold 5 pounds proof", "PCGS gold 5 pounds proof",
    "NGC gold eagle $20", "PCGS gold eagle $20",
    "NGC gold panda", "PCGS gold 20 dollars",
    "NGC gold proof coin", "PCGS gold proof coin",
    # Silver
    "NGC silver proof 5 pounds", "PCGS silver proof crown",
    "NGC silver panda 30g", "PCGS silver eagle proof",
    "NGC silver proof coin", "PCGS silver proof dollar",
    # Platinum
    "NGC platinum coin proof", "PCGS platinum eagle",
]


def main():
    start_time = time.time()

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="America/New_York",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = context.new_page()

    # Step 0: 配送先をアメリカに変更（USD表示にする）
    print("[Step 0] eBay配送先をUSに設定...")
    try:
        page.goto("https://www.ebay.com", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        # Ship-to ボタンをクリック
        ship_btn = page.query_selector("button.gh-ship-to__menu")
        if ship_btn:
            ship_btn.click()
            page.wait_for_timeout(1000)
            # 国選択ドロップダウンでUnited Statesを選択
            select_el = page.query_selector("select#gh-shipto-click-countryId, select[name='countryId']")
            if select_el:
                select_el.select_option(label="アメリカ合衆国 - USA")
                page.wait_for_timeout(500)
                # Apply/Done ボタン
                done_btn = page.query_selector("button.gh-ship-to__dialog-btn, button[type='submit']")
                if done_btn:
                    done_btn.click()
                    page.wait_for_timeout(2000)
                    print("  配送先をUSに変更完了")
                else:
                    print("  Doneボタン見つからず")
            else:
                print("  国選択見つからず")
        else:
            print("  Ship-toボタン見つからず")
    except Exception as e:
        print(f"  配送先変更失敗: {e}")

    # Step 1: Collect eBay item URLs
    print("\n[Step 1] eBay item collection...")
    all_urls = set()
    for sq in SEARCH_QUERIES:
        url = f"https://www.ebay.com/sch/i.html?_nkw={sq.replace(' ', '+')}&LH_PrefLoc=1&_udlo=30"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            urls = page.evaluate(r"""() => {
                const links = document.querySelectorAll('a[href*="/itm/"]');
                const u = new Set();
                for (const l of links) {
                    const m = (l.getAttribute('href') || '').match(/ebay\.com\/itm\/(\d+)/);
                    if (m) u.add('https://www.ebay.com/itm/' + m[1]);
                }
                return [...u];
            }""")
            before = len(all_urls)
            all_urls.update(urls)
            added = len(all_urls) - before
            if added > 0:
                print(f"  '{sq}': +{added} (total: {len(all_urls)})")
        except Exception as e:
            print(f"  '{sq}': ERROR {e}")
        time.sleep(random.uniform(1.5, 3))
        if len(all_urls) >= 500:
            print(f"  500件到達、収集打ち切り")
            break

    items = list(all_urls)
    random.shuffle(items)
    max_check = min(30, len(items))
    print(f"\n  Total: {len(items)} items -> checking top {max_check}\n")

    # Step 2: Visit items, extract specs, match Supabase
    client = get_client()
    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    results = []
    checked = 0
    matched = 0

    for item_url in items[:max_check]:
        checked += 1
        try:
            page.goto(item_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
        except Exception:
            continue

        info = page.evaluate(EXTRACT_SPECS_JS)
        title = info.get("title", "")
        price_usd = info.get("priceUsd")
        specs = info.get("specs", {})

        if not price_usd or not title:
            continue

        # Parse grader/grade/year from title
        grader_m = re.search(r"\b(NGC|PCGS)\b", title)
        grade_m = re.search(r"\b(MS|PF|PR|AU)\s*\d{1,2}\w*", title, re.IGNORECASE)
        year_m = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", title)

        grader = grader_m.group() if grader_m else specs.get("Certification", "")
        grade = grade_m.group().replace(" ", "") if grade_m else specs.get("Grade", "").replace(" ", "")
        year = year_m.group() if year_m else specs.get("Year", "")

        ebay_country = specs.get("Country of Origin", "") or specs.get("Country/Region of Manufacture", "")
        jp_country = EBAY_COUNTRY_MAP.get(ebay_country, "")
        ebay_denom = specs.get("Denomination", "")
        jp_denom = EBAY_DENOM_MAP.get(ebay_denom, "")

        if not grader or not year:
            continue
        # year が数値でない場合はスキップ
        try:
            year_int = int(year)
        except ValueError:
            continue

        # 素材フィルタ: Gold/Silver/Platinum のみ通す
        composition = specs.get("Composition", "").lower()
        title_lower = title.lower()

        # ブラックリスト: 非貴金属なのに"gold"が名前に入るコイン
        blacklist = ["sacagawea", "presidential", "innovation", "plated",
                     "filled", "layered", "clad", "nickel", "copper-nickel",
                     "quarter", "kennedy", "jefferson", "lincoln"]
        if any(bl in title_lower for bl in blacklist):
            continue

        if not any(metal in composition for metal in VALID_COMPOSITIONS):
            if not any(metal in title_lower for metal in VALID_COMPOSITIONS):
                continue

        price_jpy = int(price_usd * USD_TO_JPY)

        # Build Supabase query
        qb = (client.table("market_transactions")
            .select("title, price_jpy, sold_date")
            .eq("source", "yahoo")
            .eq("grader", grader)
            .eq("year", year_int)
            .gte("sold_date", cutoff)
            .gte("price_jpy", 10000)
            .order("sold_date", desc=True)
            .limit(50))

        if jp_country:
            qb = qb.eq("country", jp_country)
        if jp_denom:
            qb = qb.eq("denomination", jp_denom)

        try:
            resp = qb.execute()
            yahoo_all = resp.data
        except Exception:
            continue

        if not yahoo_all:
            continue

        # Grade filter
        yahoo_matches = yahoo_all
        if grade:
            grade_base = re.match(r"(MS|PF|PR|AU)\d+", grade, re.IGNORECASE)
            grade_key = grade_base.group().upper() if grade_base else grade.upper()
            filtered = [r for r in yahoo_all
                        if grade_key in (r.get("title", "") or "").upper().replace(" ", "")]
            if filtered:
                yahoo_matches = filtered

        yahoo_prices = [r["price_jpy"] for r in yahoo_matches if r.get("price_jpy")]
        if not yahoo_prices:
            continue

        matched += 1
        yahoo_median = int(statistics.median(yahoo_prices))

        cost_jpy = int(price_jpy * 1.08)
        yahoo_net = int(yahoo_median * (1 - 0.088))
        profit = yahoo_net - cost_jpy
        profit_pct = round(profit / cost_jpy * 100, 1) if cost_jpy > 0 else 0

        mq = "5-field" if (jp_country and jp_denom) else ("4-field" if jp_country else "3-field")

        print(f"[{checked}] [{mq}] {title[:65]}")
        print(f"    eBay: ${price_usd:,.2f} (Y{price_jpy:,}) | {ebay_country}, {ebay_denom}")
        print(f"    Yahoo: Y{yahoo_median:,} ({len(yahoo_matches)}件) | profit: Y{profit:,} ({profit_pct}%)")
        for ym in yahoo_matches[:2]:
            print(f"    Y{ym['price_jpy']:,} | {ym['title'][:55]}")
        print()

        results.append({
            "ebay_url": item_url, "ebay_title": title,
            "ebay_price_usd": price_usd, "ebay_price_jpy": price_jpy,
            "grader": grader, "grade": grade, "year": year,
            "ebay_country": ebay_country, "ebay_denom": ebay_denom,
            "match_quality": mq,
            "yahoo_count": len(yahoo_matches), "yahoo_median_jpy": yahoo_median,
            "yahoo_titles": [r["title"][:80] for r in yahoo_matches[:3]],
            "cost_jpy": cost_jpy, "profit_jpy": profit, "profit_pct": profit_pct,
        })

        time.sleep(random.uniform(2, 4))

    browser.close()
    pw.stop()

    elapsed = time.time() - start_time
    out_path = PROJECT_ROOT / "data" / "ebay_first_v3.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"Result: {matched} matched / {checked} checked | {elapsed:.0f}s")
    profitable = [r for r in results if r["profit_pct"] > 0]
    print(f"Profitable: {len(profitable)}")
    for r in sorted(profitable, key=lambda x: x["profit_pct"], reverse=True):
        print(f"  [{r['match_quality']}] ${r['ebay_price_usd']:,.2f} -> Y{r['yahoo_median_jpy']:,} | {r['profit_pct']}%")
        print(f"    {r['ebay_title'][:55]}")
        print(f"    {r['ebay_url']}")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
