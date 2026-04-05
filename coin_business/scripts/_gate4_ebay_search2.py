"""Gate 4 v2: より広いeBay検索 + 収益判定"""
import sys, io, os, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))

EBAY_CLIENT_ID = os.environ.get('EBAY_CLIENT_ID', '')
EBAY_CLIENT_SECRET = os.environ.get('EBAY_CLIENT_SECRET', '')
USD_JPY = 145.0
IMPORT_DUTY = 1.12
FORWARDING_JPY = 2000
DOMESTIC_JPY = 750
YAHOO_FEE = 0.10
PROFIT_MARGIN = 0.15

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

def ebay_search(query, filters=''):
    headers = {'Authorization': f'Bearer {token}', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'}
    params = {'q': query, 'category_ids': '11116', 'limit': 10, 'sort': 'price'}
    if filters:
        params['filter'] = filters
    r = requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search',
                     headers=headers, params=params)
    return r.json().get('itemSummaries', [])

def calc(price_usd, yahoo_jpy):
    total = price_usd * USD_JPY * IMPORT_DUTY + FORWARDING_JPY + DOMESTIC_JPY
    net_yahoo = yahoo_jpy * (1 - YAHOO_FEE)
    profit = net_yahoo - total
    roi = profit / total if total > 0 else 0
    return total, profit, roi

# 各候補の検索
candidates = [
    {
        'name': 'France 100 Francs Angel Gold',
        'queries': [
            'France 100 francs gold angel NGC coin',
            'France angel 100 francs gold MS62',
            '100 francs or angel france graded',
        ],
        'yahoo_jpy': 598000,
        'staging_id': 'g1188643906',
    },
    {
        'name': 'JFK Half Dollar 1966 PCGS MS64',
        'queries': [
            'JFK Kennedy half dollar 1966 PCGS MS64',
            '1966 Kennedy half dollar MS64 PCGS',
        ],
        'yahoo_jpy': 6250,
        'staging_id': 'w1222065662',
    },
]

for cand in candidates:
    yahoo_jpy = cand['yahoo_jpy']
    buy_limit = yahoo_jpy * (1 - YAHOO_FEE) * (1 - PROFIT_MARGIN)
    buy_limit_usd = buy_limit / USD_JPY
    print(f"\n{'='*60}")
    print(f"候補: {cand['name']}")
    print(f"  Yahoo実績: ¥{yahoo_jpy:,} | 買い上限: ¥{buy_limit:,.0f} (${buy_limit_usd:,.0f})")
    print(f"  staging_id: {cand['staging_id']}")

    found_any = False
    for q in cand['queries']:
        items = ebay_search(q)
        if items:
            found_any = True
            print(f"\n  検索: '{q}'  → {len(items)}件")
            for item in items[:5]:
                price_val = item.get('price', {}).get('value', '0')
                price_usd = float(price_val)
                currency = item.get('price', {}).get('currency', '')
                loc = item.get('itemLocation', {}).get('country', '?')
                buying_opts = ','.join(item.get('buyingOptions', []))
                total, profit, roi = calc(price_usd, yahoo_jpy)
                ok = '✅ BUY候補' if profit > 0 and roi >= PROFIT_MARGIN else '❌ NG'
                us_uk = '✅' if loc in ('US', 'GB') else f'❌({loc})'
                print(f"    [{us_uk}] ${price_usd:,} {currency} [{buying_opts}] 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {ok}")
                print(f"      {item['title'][:70]}")

    if not found_any:
        print('  → eBay 検索結果なし')
