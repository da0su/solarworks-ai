"""ハイブリッドスキャナー: ヤフオク起点 + CEO方式検索 + BIN/Auction両対応"""
import sys, os, json, time, random, re, statistics
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.supabase_client import get_client
from scripts.coin_matcher import extract_slab_key, is_same_coin

FX_RATE = 161

COUNTRY_EN = {
    'アメリカ': 'america', 'イギリス': 'britain', 'カナダ': 'canada',
    'オーストラリア': 'australia', 'フランス': 'france', 'ドイツ': 'germany',
    'イタリア': 'italy', 'スイス': 'switzerland', '中国': 'china',
    '日本': 'japan', 'メキシコ': 'mexico', '南アフリカ': 'south africa',
    'オーストリア': 'austria', 'ペルー': 'peru', 'ロシア': 'russia',
}


def get_metal_en(title):
    t = title.lower()
    if '金貨' in title or 'gold' in t or 'ゴールド' in title:
        return 'gold'
    if '銀貨' in title or 'silver' in t or 'シルバー' in title:
        return 'silver'
    if 'プラチナ' in title or 'platinum' in t:
        return 'platinum'
    return ''


def build_query(year, country_en, metal, grader, grade_str):
    """CEO方式: 年号 国名 素材 鑑定 グレード"""
    parts = [str(year)]
    if country_en:
        parts.append(country_en)
    if metal:
        parts.append(metal)
    if grader:
        parts.append(grader)
    if grade_str:
        parts.append(grade_str)
    return ' '.join(parts)


def main():
    client = get_client()
    data_dir = Path(__file__).parent.parent / 'data'

    # スキャン済みキー読み込み
    keys_file = data_dir / 'cap_scanned_keys.json'
    seen_keys = set()
    if keys_file.exists():
        with open(keys_file, 'r') as f:
            seen_keys = set(json.load(f))

    # ヤフオクから候補取得（シリーズフィルタなし）
    resp = (client.table("market_transactions")
        .select("title, price_jpy, sold_date, url, grader, grade, year, country, denomination")
        .eq("source", "yahoo")
        .gte("sold_date", "2025-09-01")
        .gte("price_jpy", 100000)
        .lte("price_jpy", 1000000)
        .not_.is_("grader", "null")
        .order("sold_date", desc=True)
        .limit(500)
        .execute())

    candidates = []
    for r in resp.data:
        t = r['title']
        info = extract_slab_key(t)
        year = info.get('year', '')
        if not year:
            continue

        gm = re.search(r'(MS|PF|PR)\s*(\d{1,2})', t, re.IGNORECASE)
        if not gm:
            continue
        gt = gm.group(1).upper()
        if gt == 'PR':
            gt = 'PF'
        grade_str = f'{gt}{gm.group(2)}'

        country = r.get('country', '') or ''
        country_e = COUNTRY_EN.get(country, '')
        metal = get_metal_en(t)
        grader = r.get('grader', '')

        key = f'{year}-{country_e}-{metal}-{grader}-{grade_str}'
        if key in seen_keys:
            continue

        query = build_query(year, country_e, metal, grader, grade_str)

        candidates.append({
            'title': t, 'price': r['price_jpy'], 'date': r['sold_date'],
            'url': r['url'], 'year': year, 'country_en': country_e,
            'metal': metal, 'grader': grader, 'grade': grade_str,
            'key': key, 'query': query, 'info': info,
        })
        seen_keys.add(key)

    print(f'ヤフオク候補: {len(candidates)}件')

    # Playwright起動（persistent context: eBay配送先USをCookie保持）
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    profile_dir = str(Path(__file__).parent.parent / 'ebay_profile')
    os.makedirs(profile_dir, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        locale="en-US",
        timezone_id="America/New_York",
        viewport={"width": 1280, "height": 800},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    browser = None  # persistent contextではbrowser不要

    results = []
    scanned = 0

    for cand in candidates:
        scanned += 1
        print(f'\n[{scanned}/{len(candidates)}] {cand["query"]} | Y{cand["price"]:,}')

        url = f'https://www.ebay.com/sch/i.html?_nkw={cand["query"].replace(" ", "+")}'
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
        except Exception:
            continue

        item_ids = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/itm/"]');
            const seen = new Set();
            for (const link of links) {
                const m = (link.getAttribute('href') || '').match(/ebay\\.com\\/itm\\/(\\d+)/);
                if (m && m[1] !== '123456') seen.add(m[1]);
            }
            return [...seen].slice(0, 10);
        }""")

        if not item_ids:
            print(f'  -> 0件')
            continue

        print(f'  -> {len(item_ids)}件ヒット、上位3件巡回')

        for item_id in item_ids[:3]:
            item_url = f'https://www.ebay.com/itm/{item_id}'
            try:
                page.goto(item_url, wait_until="domcontentloaded", timeout=8000)
                page.wait_for_timeout(1500)
            except Exception:
                continue

            try:
                info = page.evaluate("""() => {
                    var title = document.title.replace(' | eBay', '').trim();
                    var body = document.body.innerText;
                    var usdMatch = body.match(/US\\s*\\$([\\d,]+\\.\\d{2})/);
                    var shipFree = /free\\s*shipping|送料無料/i.test(body);
                    var shipMatch = body.match(/US\\s*\\$([\\d.]+).*?(?:shipping|配送)/i);
                    var isAuction = /Place bid|入札する/i.test(body);
                    return {
                        title: title,
                        priceUsd: usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null,
                        shipping: shipFree ? 0 : (shipMatch ? parseFloat(shipMatch[1]) : -1),
                        isAuction: isAuction,
                    };
                }""")
            except Exception:
                continue

            if not info.get('priceUsd') or not info.get('title'):
                continue

            ebay_info = extract_slab_key(info['title'])
            yahoo_info = cand['info']

            matched, reason = is_same_coin(ebay_info, yahoo_info)
            if not matched:
                continue

            ship_jpy = int(info['shipping'] * FX_RATE) if info['shipping'] > 0 else 0
            cost = int(info['priceUsd'] * FX_RATE * 1.1) + ship_jpy + 2750
            net = int(cand['price'] * 0.9)
            profit = net - cost
            pct = round(profit / cost * 100, 1) if cost > 0 else 0

            auc_label = "[AUC]" if info['isAuction'] else "[BIN]"
            print(f'  {auc_label} MATCH: {info["title"][:55]} | ${info["priceUsd"]}')
            print(f'    vs Yahoo: {cand["title"][:55]}')
            print(f'    原価Y{cost:,} vs 手取りY{net:,} -> {pct}%')
            print(f'    Match: {reason}')

            if pct > -30:
                results.append({
                    'key': cand['key'],
                    'ebay_url': item_url,
                    'ebay_title': info['title'],
                    'ebay_price_usd': info['priceUsd'],
                    'ebay_shipping': info['shipping'],
                    'is_auction': info['isAuction'],
                    'yahoo_url': cand['url'],
                    'yahoo_title': cand['title'],
                    'yahoo_price': cand['price'],
                    'cost': cost,
                    'profit': profit,
                    'pct': pct,
                    'match_reason': reason,
                })
            break

        # 随時保存
        with open(keys_file, 'w', encoding='utf-8') as f:
            json.dump(sorted(list(seen_keys)), f, ensure_ascii=False, indent=2)

        time.sleep(random.uniform(2, 4))

    ctx.close()
    pw.stop()

    # 結果保存
    profitable = [r for r in results if r['pct'] > 0]
    with open(data_dir / 'candidates_cap_100k.json', 'w', encoding='utf-8') as f:
        json.dump(profitable, f, ensure_ascii=False, indent=2)

    print(f'\n{"="*60}')
    print(f'完了: {scanned}件 / マッチ{len(results)}件 / 利益あり{len(profitable)}件')
    for r in sorted(profitable, key=lambda x: x['pct'], reverse=True):
        auc = "[AUC]" if r['is_auction'] else "[BIN]"
        print(f'\n  {auc} +{r["pct"]}% | 利益Y{r["profit"]:,}')
        print(f'  eBay: ${r["ebay_price_usd"]:,.2f} | {r["ebay_title"][:55]}')
        print(f'  Yahoo: Y{r["yahoo_price"]:,} | {r["yahoo_title"][:55]}')
        print(f'  {r["ebay_url"]}')
        print(f'  {r["yahoo_url"]}')

    # 通知
    try:
        import subprocess
        notifier = str(Path(__file__).parent.parent.parent / 'ops' / 'notifications' / 'notifier.py')
        subprocess.run(['python', notifier, 'dev_done'], timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    main()
