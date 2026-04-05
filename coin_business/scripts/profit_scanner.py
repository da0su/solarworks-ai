"""利益候補スキャナー（勝ちパターン版）

ヤフオク起点 → eBay検索 → スラブ完全一致 → 利益計算

Usage:
    python scripts/profit_scanner.py [--limit 20] [--min-profit 0]
"""
import sys
import os
import json
import time
import random
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.supabase_client import get_client

# === 定数 ===
YAHOO_FEE = 0.10          # ヤフオク手数料 10%
IMPORT_TAX = 1.10          # 輸入消費税（商品価格のみ×1.1）
US_FORWARDING = 2000       # US転送サービス ¥2,000
DOMESTIC_SHIPPING = 750    # 国内送料 ¥750
FX_BUFFER = 1              # 為替バッファ +1円
MIN_YAHOO_RECORDS = 1      # 最低ヤフオク履歴件数

# === シリーズ名マッピング（日本語→英語検索用） ===
SERIES_MAP = {
    'モルガン': 'morgan', 'Morgan': 'morgan',
    'ブリタニア': 'britannia', 'Britannia': 'britannia',
    'パンダ': 'panda', 'Panda': 'panda',
    'ソブリン': 'sovereign', 'Sovereign': 'sovereign',
    'イーグル': 'eagle', 'Eagle': 'eagle',
    'メイプル': 'maple leaf', 'Maple': 'maple leaf',
    'リバティ': 'liberty', 'Liberty': 'liberty',
    'クルーガー': 'krugerrand', 'Krugerrand': 'krugerrand',
    'ピース': 'peace', 'Peace': 'peace',
    'ウナ': 'una lion', 'Una': 'una lion',
    'カンガルー': 'kangaroo', 'Kangaroo': 'kangaroo',
    'クッカバラ': 'kookaburra', 'Kookaburra': 'kookaburra',
    'フィルハーモニー': 'philharmonic',
    'バッファロー': 'buffalo', 'Buffalo': 'buffalo',
}


def get_fx_rate():
    """Google FinanceからUSD/JPYリアルタイム取得 +1円 切り上げ"""
    # TODO: Supabase daily_ratesから取得に切り替え
    # 暫定: 手動設定値
    import math
    raw_rate = 149.24  # デフォルト（daily_rates未実装時）
    try:
        # Supabaseから今日のレートを取得
        client = get_client()
        today = datetime.now().strftime("%Y-%m-%d")
        resp = (client.table("daily_rates")
            .select("usd_jpy_calc")
            .eq("rate_date", today)
            .limit(1)
            .execute())
        if resp.data:
            return float(resp.data[0]["usd_jpy_calc"])
    except:
        pass
    # フォールバック: 固定値
    return math.ceil(raw_rate + FX_BUFFER)


def calc_cost(price_usd: float, shipping_usd: float, fx_rate: float) -> int:
    """確定版原価計算
    原価 = (eBay商品価格USD × 為替 × 1.1) + (eBay送料USD × 為替) + 2000 + 750
    """
    item_jpy = price_usd * fx_rate * IMPORT_TAX
    ship_jpy = shipping_usd * fx_rate if shipping_usd > 0 else 0
    return int(item_jpy + ship_jpy + US_FORWARDING + DOMESTIC_SHIPPING)


def calc_profit(yahoo_price: int, cost: int) -> tuple:
    """利益計算: 手取り = ヤフオク × 0.9, 利益 = 手取り - 原価"""
    net = int(yahoo_price * (1 - YAHOO_FEE))
    profit = net - cost
    pct = round(profit / cost * 100, 1) if cost > 0 else 0
    return net, profit, pct


def extract_yahoo_candidates(client, min_price=30000, max_price=500000, days=90, limit=200):
    """ヤフオクDBから有望コインを抽出"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    resp = (client.table("market_transactions")
        .select("title, price_jpy, sold_date, url, grader, grade, year, country")
        .eq("source", "yahoo")
        .gte("sold_date", cutoff)
        .gte("price_jpy", min_price)
        .lte("price_jpy", max_price)
        .not_.is_("grader", "null")
        .order("sold_date", desc=True)
        .limit(limit)
        .execute())

    candidates = []
    seen_keys = set()

    for r in resp.data:
        t = r['title']

        # グレード抽出（必須）
        grade_m = re.search(r'(MS|PF|PR|AU)\s*(\d{1,2})', t, re.IGNORECASE)
        if not grade_m:
            continue
        grade_type = grade_m.group(1).upper()
        if grade_type == 'PR':
            grade_type = 'PF'
        grade_num = grade_m.group(2)
        grade = f"{grade_type}{grade_num}"

        # シリーズ名抽出（既知マップから。なければタイトルの英語キーワードを使用）
        series_en = None
        for jp, en in SERIES_MAP.items():
            if jp in t:
                series_en = en
                break

        if not series_en:
            # タイトルから英語の固有名詞を抽出してeBay検索に使う
            eng_words = re.findall(r'[A-Za-z]{3,}', t)
            # NGC/PCGS/グレード等の汎用語を除外
            skip_words = {'NGC', 'PCGS', 'ULTRA', 'CAMEO', 'UCAM', 'DCAM', 'DEEP',
                         'FIRST', 'EARLY', 'RELEASES', 'RELEASE', 'STRIKE', 'DAY',
                         'ISSUE', 'THE', 'AND', 'FOR', 'WITH', 'FROM', 'PROOF',
                         'MINT', 'STATE', 'GRADED', 'CERTIFIED', 'FREE', 'SHIPPING',
                         'COIN', 'RARE', 'NEW', 'OLD', 'BUY', 'SET', 'LOT', 'ONE',
                         'OZT', 'SILVER', 'GOLD', 'PLATINUM', 'COA', 'BOX'}
            keywords = [w for w in eng_words if w.upper() not in skip_words and len(w) >= 3]
            if keywords:
                series_en = ' '.join(keywords[:3]).lower()
            else:
                # 日本語タイトルからシリーズ部分を抽出
                # 「1880年 S アメリカ モルガンダラー」→ タイトル全体をクエリに
                series_en = ''

        # ミントマーク
        mint = ''
        mint_m = re.search(r'(\d{4})\s*[-年]\s*([SDWCOP])\b', t)
        if mint_m:
            mint = mint_m.group(2)
        else:
            mint_m2 = re.search(r'(\d{4})-([SDWCOP])\b', t)
            if mint_m2:
                mint = mint_m2.group(2)

        year = r['year']
        grader = r['grader']

        if not year:
            continue

        # 重複排除
        key = f"{year}-{mint}-{series_en[:20] if series_en else 'unknown'}-{grader}-{grade}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # eBay検索クエリ生成
        query_parts = [str(year)]
        if mint:
            query_parts[0] = f"{year}-{mint}"
        if series_en:
            query_parts.append(series_en)
        query_parts.extend([grader, grade])
        ebay_query = ' '.join(query_parts)

        candidates.append({
            'title': t[:80],
            'price': r['price_jpy'],
            'date': r['sold_date'],
            'url': r['url'],
            'year': year,
            'mint': mint,
            'series': series_en or 'unknown',
            'grader': grader,
            'grade': grade,
            'grade_type': grade_type,
            'key': key,
            'ebay_query': ebay_query,
        })

    return candidates


def search_ebay_for_coin(page, query: str, slab_check, max_items=8, include_auction=False):
    """eBayで特定コインを検索し、スラブ一致する商品を返す

    Args:
        include_auction: Trueならオークション出品も含める（BIN + Auction両方）
    """
    if include_auction:
        # BINとオークション両方
        url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}"
    else:
        # BINのみ
        url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}&LH_BIN=1"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)
    except:
        return []

    # Step 1: Get all item URLs
    item_urls = page.evaluate(r"""() => {
        const links = document.querySelectorAll('a[href*="/itm/"]');
        const seen = new Set();
        const urls = [];
        for (const link of links) {
            const href = link.getAttribute('href') || '';
            const m = href.match(/ebay\.com\/itm\/(\d+)/);
            if (m && !seen.has(m[1]) && m[1] !== '123456') {
                seen.add(m[1]);
                urls.push('https://www.ebay.com/itm/' + m[1]);
            }
        }
        return urls;
    }""")

    # Step 2: Visit each and check English title
    matches = []
    for item_url in item_urls[:max_items]:
        try:
            page.goto(item_url, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
        except:
            continue

        info = page.evaluate(r"""() => {
            var title = document.title.replace(' | eBay', '').trim();
            var body = document.body.innerText;
            var usdMatch = body.match(/US\s*\$([\d,]+\.\d{2})/);
            var shipFree = /free\s*shipping|送料無料/i.test(body);
            var shipMatch = body.match(/US\s*\$([\d.]+).*?(?:shipping|配送)/i);

            // オークション情報取得
            var isAuction = /入札|bid|Place bid/i.test(body);
            var bidCount = 0;
            var bidMatch = body.match(/(\d+)\s*(?:入札|bids?)/i);
            if (bidMatch) bidCount = parseInt(bidMatch[1]);

            var timeLeft = '';
            var timeMatch = body.match(/残り\s*([^\n]+)/i) || body.match(/Time left[:\s]*([^\n]+)/i);
            if (timeMatch) timeLeft = timeMatch[1].trim().substring(0, 30);

            return {
                title: title,
                priceUsd: usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null,
                shipping: shipFree ? 0 : (shipMatch ? parseFloat(shipMatch[1]) : -1),
                isAuction: isAuction,
                bidCount: bidCount,
                timeLeft: timeLeft,
            };
        }""")

        title = info.get('title', '')
        if not info.get('priceUsd'):
            continue

        if slab_check(title):
            # Exclude special variants (PL, Star, Signed)
            if any(v in title.upper() for v in [' PL ', ' PL,', 'PROOF-LIKE', 'PROOFLIKE']):
                continue
            if 'Star' in title or '★' in title:
                continue
            if any(v in title.lower() for v in ['signed', 'autograph']):
                continue

            match_data = {
                'title': title,
                'url': item_url,
                'price_usd': info['priceUsd'],
                'shipping_usd': info['shipping'] if info['shipping'] >= 0 else 0,
                'is_auction': info.get('isAuction', False),
                'bid_count': info.get('bidCount', 0),
                'time_left': info.get('timeLeft', ''),
            }
            matches.append(match_data)

    return matches


def calc_max_bid(yahoo_median: int, fx_rate: float, min_profit_pct: float = 20.0) -> float:
    """オークション用: 利益率を確保できる最大入札額(USD)を計算

    利益率 = (手取り - 原価) / 原価 × 100
    手取り = yahoo_median × 0.9
    原価 = (bid × fx × 1.1) + (ship × fx) + 2750
    ship = 0と仮定（Free Shippingが多い）

    → bid_max = (手取り / (1 + min_profit_pct/100) - 2750) / (fx × 1.1)
    """
    net = yahoo_median * (1 - YAHOO_FEE)
    max_cost = net / (1 + min_profit_pct / 100)
    max_bid_usd = (max_cost - US_FORWARDING - DOMESTIC_SHIPPING) / (fx_rate * IMPORT_TAX)
    return round(max_bid_usd, 2)


def build_slab_checker(year, mint, series, grade, grade_type):
    """スラブ一致チェック関数を生成"""
    def check(title):
        t = title.lower()
        t_upper = title.upper()

        # 年号一致
        if str(year) not in title:
            return False

        # シリーズ一致
        if series.lower() not in t:
            return False

        # グレード一致
        grade_patterns = [grade, grade.replace('PF', 'PR'), f"{grade_type} {grade[2:]}",
                         f"{grade_type}-{grade[2:]}", f"{grade_type}{grade[2:]}"]
        if not any(gp.upper() in t_upper for gp in grade_patterns):
            return False

        # ミントマーク一致（あれば）
        if mint:
            mint_patterns = [f"{year}-{mint}", f"{year} {mint}", f"{year}{mint}"]
            if not any(mp in title for mp in mint_patterns):
                return False

        return True

    return check


def get_yahoo_records(client, year, mint, series_jp_candidates, grader, grade, grade_type, limit=10):
    """同一コインのヤフオク履歴を取得"""
    records = []

    for series_jp in series_jp_candidates:
        qb = (client.table("market_transactions")
            .select("title, price_jpy, sold_date, url")
            .eq("source", "yahoo")
            .eq("year", year)
            .eq("grader", grader)
            .ilike("title", f"%{series_jp}%")
            .gte("price_jpy", 10000)
            .order("sold_date", desc=True)
            .limit(limit))

        try:
            resp = qb.execute()
            for r in resp.data:
                t = r['title']
                # グレード種別チェック（PF vs MS）
                if grade_type == 'PF' and 'MS' in t.upper() and 'PF' not in t.upper() and 'PR' not in t.upper():
                    continue
                if grade_type == 'MS' and ('PF' in t.upper() or 'PR' in t.upper()) and 'MS' not in t.upper():
                    continue
                records.append(r)
        except:
            pass

    return records


def deep_dive(client, page, cand, ebay_match, fx_rate):
    """深掘り調査5ステップ

    Returns:
        dict with deep dive results, or None if disqualified
    """
    year = cand['year']
    series = cand['series']
    grader = cand['grader']
    grade = cand['grade']
    grade_type = cand['grade_type']

    # シリーズ名の日本語候補
    series_jp_map = {
        'morgan': ['モルガン', 'Morgan'],
        'eagle': ['イーグル', 'Eagle'],
        'britannia': ['ブリタニア', 'Britannia'],
        'panda': ['パンダ', 'Panda'],
        'sovereign': ['ソブリン', 'Sovereign'],
        'maple leaf': ['メイプル', 'Maple'],
        'liberty': ['リバティ', 'Liberty'],
        'krugerrand': ['クルーガー', 'Krugerrand'],
        'peace': ['ピース', 'Peace'],
        'una lion': ['ウナ', 'Una'],
        'kangaroo': ['カンガルー', 'Kangaroo'],
        'kookaburra': ['クッカバラ', 'Kookaburra'],
        'buffalo': ['バッファロー', 'Buffalo'],
        'philharmonic': ['フィルハーモニー', 'Philharmonic'],
    }
    series_jp = series_jp_map.get(series, [series])

    # === Step 2: ヤフオク同一コイン履歴 ===
    yahoo_records = get_yahoo_records(client, year, '', series_jp, grader, grade, grade_type, limit=30)

    # セット品除外
    yahoo_records = [r for r in yahoo_records if not any(
        kw in r['title'] for kw in ['セット', '2枚', '3枚', '枚セット', '個セット']
    )]

    # URL重複排除
    seen_aids = set()
    unique_records = []
    for r in yahoo_records:
        m = re.search(r'/auction/([a-z0-9]+)', r['url'])
        aid = m.group(1) if m else r['url']
        if aid not in seen_aids:
            seen_aids.add(aid)
            unique_records.append(r)
    yahoo_records = unique_records

    if not yahoo_records:
        return None

    # === Step 3: グレード別相場 + 地金分離 ===
    # 地金価値計算（Silver Eagle = 1oz .999 silver として仮設定）
    silver_usd_oz = 33.5
    melt_jpy = int(1.0 * 0.999 * silver_usd_oz * fx_rate)

    # グレード別に分類
    grade_groups = {}
    for r in yahoo_records:
        gm = re.search(r'(MS|PF|PR)\s*[-]?\s*(\d{1,2})', r['title'], re.IGNORECASE)
        g = f"{gm.group(1).upper()}{gm.group(2)}" if gm else 'unknown'
        if g.startswith('PR'):
            g = g.replace('PR', 'PF')
        if g not in grade_groups:
            grade_groups[g] = []
        grade_groups[g].append(r)

    # 同一グレードの価格統計
    same_grade = grade_groups.get(grade, [])
    if same_grade:
        prices = [r['price_jpy'] for r in same_grade]
        # 異常値除外（中央値の2倍以上は除外）
        med = statistics.median(prices)
        prices_clean = [p for p in prices if p <= med * 2]
        if prices_clean:
            yahoo_median = int(statistics.median(prices_clean))
            yahoo_min = min(prices_clean)
        else:
            yahoo_median = int(med)
            yahoo_min = min(prices)
    else:
        yahoo_median = cand['price']
        yahoo_min = cand['price']

    # === Step 4: 現在の出品状況確認 ===
    current_listings = []
    try:
        search_terms = series_jp[0]
        search_url = f"https://auctions.yahoo.co.jp/search/search?p={year}+{search_terms}+{grade}&auccat=0"
        page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(2000)

        body = page.evaluate("() => document.body.innerText")
        lines = body.split('\n')
        for i, line in enumerate(lines):
            if str(year) in line and (series_jp[0] in line or series in line.lower()):
                for j in range(max(0, i-3), min(len(lines), i+5)):
                    pm = re.search(r'現在\s*([\d,]+)円', lines[j])
                    if pm:
                        price = int(pm.group(1).replace(',', ''))
                        current_listings.append({'title': line[:70], 'price': price})
                        break
    except:
        pass

    # === Step 5: 総合評価 ===
    cost = calc_cost(ebay_match['price_usd'], ebay_match['shipping_usd'], fx_rate)

    # 中央値ベースの利益
    net_median, profit_median, pct_median = calc_profit(yahoo_median, cost)
    # 下限ベースの利益
    net_min, profit_min, pct_min = calc_profit(yahoo_min, cost)

    # 現在出品の最安値
    current_min = min([l['price'] for l in current_listings]) if current_listings else None
    if current_min:
        net_current, profit_current, pct_current = calc_profit(current_min, cost)
    else:
        profit_current, pct_current = None, None

    # 判定
    qualified = True
    disqualify_reason = ""

    # 中央値で赤字なら除外
    if pct_median < 0:
        qualified = False
        disqualify_reason = f"中央値で赤字 ({pct_median}%)"

    # 現在出品で赤字なら警告
    if current_min and pct_current is not None and pct_current < 0:
        qualified = False
        disqualify_reason = f"現在出品最安Y{current_min:,}で赤字 ({pct_current}%)"

    # 履歴1件だけで高利益は警告
    if len(same_grade) <= 1 and pct_median > 50:
        disqualify_reason += " / 履歴1件のみ（要注意）"

    return {
        'qualified': qualified,
        'disqualify_reason': disqualify_reason,
        'yahoo_records_count': len(yahoo_records),
        'same_grade_count': len(same_grade),
        'yahoo_median': yahoo_median,
        'yahoo_min': yahoo_min,
        'melt_jpy': melt_jpy,
        'premium_median': yahoo_median - melt_jpy,
        'premium_pct': round((yahoo_median - melt_jpy) / melt_jpy * 100, 1) if melt_jpy > 0 else 0,
        'cost_jpy': cost,
        'profit_median': profit_median,
        'profit_pct_median': pct_median,
        'profit_min': profit_min,
        'profit_pct_min': pct_min,
        'current_listings': len(current_listings),
        'current_min_price': current_min,
        'profit_current': profit_current,
        'profit_pct_current': pct_current,
        'grade_breakdown': {g: len(recs) for g, recs in grade_groups.items()},
        'yahoo_urls': [r['url'] for r in same_grade[:3]],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="Number of Yahoo candidates to scan")
    parser.add_argument("--min-profit", type=float, default=-999, help="Minimum profit % to report")
    parser.add_argument("--output", default="data/scan_results.json")
    parser.add_argument("--days", type=int, default=90, help="Yahoo data lookback days")
    parser.add_argument("--min-price", type=int, default=30000, help="Min Yahoo price")
    parser.add_argument("--max-price", type=int, default=500000, help="Max Yahoo price")
    parser.add_argument("--check-urls", action="store_true", help="Check Yahoo URL accessibility")
    args = parser.parse_args()

    print("=" * 60)
    print("  利益候補スキャナー（勝ちパターン版）")
    print("=" * 60)

    # FX rate
    fx_rate = get_fx_rate()
    print(f"  為替レート: {fx_rate}円/USD")

    # Supabase
    client = get_client()

    # Step 1: ヤフオク候補抽出
    print("\n[Step 1] ヤフオク候補抽出...")
    candidates = extract_yahoo_candidates(client, min_price=args.min_price, max_price=args.max_price, days=args.days, limit=2000)
    print(f"  {len(candidates)}件の候補")

    # Playwright
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="America/New_York",
        viewport={"width": 1280, "height": 800},
    )
    page = ctx.new_page()

    # Step 2: 各コインをeBayで検索
    print(f"\n[Step 2] eBay検索開始（上位{args.limit}件）...")
    results = []

    for i, cand in enumerate(candidates[:args.limit], 1):
        print(f"\n  [{i}/{min(args.limit, len(candidates))}] {cand['key']}")
        print(f"    Query: {cand['ebay_query']}")

        slab_check = build_slab_checker(
            cand['year'], cand['mint'], cand['series'],
            cand['grade'], cand['grade_type']
        )

        ebay_matches = search_ebay_for_coin(page, cand['ebay_query'], slab_check)

        if not ebay_matches:
            print(f"    -> eBayマッチなし")
            continue

        # 最安値を選択
        best = min(ebay_matches, key=lambda x: x['price_usd'])

        # 原価計算
        cost = calc_cost(best['price_usd'], best['shipping_usd'], fx_rate)
        net, profit, pct = calc_profit(cand['price'], cost)

        print(f"    MATCH: {best['title'][:60]}")
        print(f"    ${best['price_usd']} + ship ${best['shipping_usd']} -> 原価 Y{cost:,}")
        print(f"    Yahoo Y{cand['price']:,} -> 手取り Y{net:,} | 利益 Y{profit:,} ({pct}%)")

        # ヤフオクURL開通チェック
        yahoo_url_ok = False
        try:
            page.goto(cand['url'], wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1500)
            body_text = page.evaluate("() => document.body.innerText.substring(0, 300)")
            yahoo_url_ok = ('落札' in body_text or 'SOLD' in body_text or
                           'このオークションは終了' in body_text or '現在' in body_text)
            if not yahoo_url_ok:
                print(f"    Yahoo URL NG: {cand['url']}")
        except:
            print(f"    Yahoo URL ERROR: {cand['url']}")

        result = {
            'yahoo_key': cand['key'],
            'yahoo_title': cand['title'],
            'yahoo_price': cand['price'],
            'yahoo_date': cand['date'],
            'yahoo_url': cand['url'],
            'yahoo_url_ok': yahoo_url_ok,
            'ebay_title': best['title'],
            'ebay_url': best['url'],
            'ebay_price_usd': best['price_usd'],
            'ebay_shipping_usd': best['shipping_usd'],
            'ebay_matched': True,
            'fx_rate': fx_rate,
            'cost_jpy': cost,
            'net_jpy': net,
            'profit_jpy': profit,
            'profit_pct': pct,
            'scanned_at': datetime.now().isoformat(),
        }
        results.append(result)

        # 1件ごとに保存（クラッシュ耐性）
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        time.sleep(random.uniform(3, 6))

    browser.close()
    pw.stop()

    # サマリー
    print(f"\n{'='*60}")
    print(f"完了: {len(results)}件マッチ / {min(args.limit, len(candidates))}件スキャン")

    profitable = [r for r in results if r['profit_pct'] > 0]
    print(f"利益あり: {len(profitable)}件")

    url_ok_results = [r for r in results if r.get('yahoo_url_ok')]
    print(f"URL開通: {len(url_ok_results)}件")

    for r in sorted(results, key=lambda x: x['profit_pct'], reverse=True):
        url_mark = "OK" if r.get('yahoo_url_ok') else "NG"
        mark = "+++" if r['profit_pct'] > 20 else ("+" if r['profit_pct'] > 0 else "---")
        print(f"  {mark} [{url_mark}] {r['yahoo_key']} | ${r['ebay_price_usd']} -> Y{r['yahoo_price']:,} | {r['profit_pct']}%")
        print(f"      eBay: {r['ebay_url']}")
        print(f"      Yahoo: {r['yahoo_url']}")

    # VOICEVOX通知
    try:
        import subprocess
        notifier = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               '..', 'ops', 'notifications', 'notifier.py')
        if os.path.exists(notifier):
            subprocess.run(['python', notifier, 'dev_done'], timeout=15)
    except:
        pass
