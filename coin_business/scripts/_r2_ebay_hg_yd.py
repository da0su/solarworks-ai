"""Round 2: HIGH_GRADE + YEAR_DELTA eBay検索
最高価格帯の stagingコイン種を eBay で価格比較
"""
import sys, io, os, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))

EBAY_CLIENT_ID = os.environ.get('EBAY_CLIENT_ID', '')
EBAY_CLIENT_SECRET = os.environ.get('EBAY_CLIENT_SECRET', '')
USD_JPY = 145.0
IMPORT_DUTY  = 1.12
FWD_JPY      = 2000
DOM_JPY      = 750
YAHOO_FEE    = 0.10
PROFIT_MIN   = 0.15

def get_token():
    import base64
    creds = base64.b64encode(f'{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}'.encode()).decode()
    r = requests.post(
        'https://api.ebay.com/identity/v1/oauth2/token',
        headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/x-www-form-urlencoded'},
        data='grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope'
    )
    return r.json().get('access_token', '')

token = get_token()

def search(query, limit=8):
    h = {'Authorization': f'Bearer {token}', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'}
    p = {'q': query, 'category_ids': '11116',
         'filter': 'itemLocationCountry:US,itemLocationCountry:GB',
         'limit': limit, 'sort': 'price'}
    r = requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search', headers=h, params=p)
    return r.json().get('itemSummaries', [])

def calc(price_usd, yahoo_jpy):
    total = price_usd * USD_JPY * IMPORT_DUTY + FWD_JPY + DOM_JPY
    net   = yahoo_jpy * (1 - YAHOO_FEE)
    profit = net - total
    roi   = profit / total if total > 0 else 0
    ok    = profit > 0 and roi >= PROFIT_MIN
    return total, profit, roi, ok

def buy_limit_usd(yahoo_jpy):
    return yahoo_jpy * (1 - YAHOO_FEE) * (1 - PROFIT_MIN) / USD_JPY

# ============================================================
# 探索対象（staging上位 + coin_slab_data参照）
# ============================================================
TARGETS = [
    # name, Yahoo実績(JPY), 探索クエリ, route
    ('France Angel 100F MS62(1904A)',  598000, [
        'France 100 francs gold angel MS62 NGC',
        'France angel 100 francs gold graded PCGS NGC',
        '100 francs or france angel gold coin'], 'CERT_EXACT / HIGH_GRADE'),
    ('France Angel 100F MS64(1906)',   867000, [
        'France 100 francs gold angel MS64',
        '100 francs angel gold 1906 PCGS MS64'], 'HIGH_GRADE'),
    ('France Angel 100F MS63(1909)',   880000, [
        'France 100 francs gold angel MS63 NGC',
        '100 francs angel gold NGC MS63'], 'HIGH_GRADE'),
    ('France Napoleon3 100F MS62(1869)', 870000, [
        'France Napoleon 100 francs gold MS62 NGC',
        'Napoleon III 100 francs gold graded'], 'HIGH_GRADE'),
    ('USA $20 Liberty MS62-64',        801000, [
        'United States double eagle $20 gold Liberty MS62 NGC PCGS',
        '$20 liberty head double eagle MS63 MS64 gold coin'], 'HIGH_GRADE / YEAR_DELTA'),
    ('USA $20 Liberty 1927 MS66',      950000, [
        '1927 double eagle $20 gold PCGS MS66',
        '1927 Saint Gaudens $20 MS65 MS66 gold'], 'HIGH_GRADE / YEAR_DELTA'),
    ('USA $20 Liberty 1897 AU58',      810000, [
        '1897 double eagle $20 gold PCGS AU58',
        '1897 liberty double eagle $20 gold graded'], 'YEAR_DELTA'),
    ('UK 5 Pound Gold Proof PF68-70',  792000, [
        'Great Britain 5 pounds gold proof NGC PF70 PCGS PR70',
        'UK 5 pound gold proof coin NGC PCGS graded'], 'HIGH_GRADE'),
    ('USA $50 Gold Eagle 1oz MS69',    900000, [
        '2000 American Gold Eagle $50 1oz NGC MS69',
        'American Eagle gold $50 1oz MS69 NGC PCGS'], 'HIGH_GRADE / YEAR_DELTA'),
    ('USA Ultra HR $20 2009 MS70',     748000, [
        '2009 ultra high relief $20 gold PCGS MS70',
        '2009 ultra high relief double eagle gold NGC'], 'HIGH_GRADE'),
]

longlist = []

for name, yahoo_jpy, queries, route in TARGETS:
    bl_usd = buy_limit_usd(yahoo_jpy)
    print(f'\n{"="*65}')
    print(f'[{route}] {name}')
    print(f'  Yahoo実績: ¥{yahoo_jpy:,}  買い上限: ${bl_usd:,.0f}')

    found = False
    for q in queries:
        items = search(q, limit=6)
        if not items:
            continue
        found = True
        print(f'  検索: "{q[:50]}" → {len(items)}件')
        for item in items[:4]:
            price_usd = float(item.get('price',{}).get('value', 0))
            currency  = item.get('price',{}).get('currency','')
            loc       = item.get('itemLocation',{}).get('country','?')
            opts      = ','.join(item.get('buyingOptions',[]))
            total, profit, roi, ok = calc(price_usd, yahoo_jpy)
            flag = '✅ BUY候補' if ok else '❌'
            us_uk = '✅' if loc in ('US','GB') else f'❌({loc})'
            print(f'  [{us_uk}] ${price_usd:,.0f} [{opts}] → 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
            print(f'    {item["title"][:70]}')
            if ok:
                longlist.append({
                    'name': name,
                    'route': route,
                    'yahoo_jpy': yahoo_jpy,
                    'buy_limit_usd': bl_usd,
                    'price_usd': price_usd,
                    'total_cost_jpy': total,
                    'profit_jpy': profit,
                    'roi': roi,
                    'country': loc,
                    'buying_options': opts,
                    'title': item['title'],
                    'item_id': item.get('itemId',''),
                })
        break  # 最初のヒットクエリのみ使用

    if not found:
        print('  → eBay 検索結果なし')

print(f'\n\n{"="*65}')
print(f'=== LONGLIST: BUY候補合計 {len(longlist)} 件 ===')
for i, item in enumerate(longlist, 1):
    print(f'{i}. [{item["route"]}] {item["name"]}')
    print(f'   ${item["price_usd"]:,.0f} → 総費用¥{item["total_cost_jpy"]:,.0f} | 利益¥{item["profit_jpy"]:,.0f} | ROI{item["roi"]*100:.1f}%')
    print(f'   {item["title"][:70]}')
