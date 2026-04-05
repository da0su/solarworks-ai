"""Gate 4: eBay価格検索 + 収益計算
対象: France 100F Angel NGC 403024500 (MS62)
"""
import sys, io, os, requests, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))

EBAY_CLIENT_ID = os.environ.get('EBAY_CLIENT_ID', '')
EBAY_CLIENT_SECRET = os.environ.get('EBAY_CLIENT_SECRET', '')

USD_JPY = 145.0  # 計算用レート
IMPORT_DUTY = 1.12  # 関税1.1 × 消費税
FORWARDING_JPY = 2000  # US転送送料
DOMESTIC_JPY = 750  # 国内送料
YAHOO_FEE = 0.10  # ヤフオク手数料
PROFIT_MARGIN = 0.15  # 目標利益率

# Yahoo実績
YAHOO_SOLD_JPY = 598000  # staging参照
YAHOO_SOLD_DATE = '2025-06-14'

buy_limit = YAHOO_SOLD_JPY * (1 - YAHOO_FEE) * (1 - PROFIT_MARGIN)
buy_limit_usd = buy_limit / USD_JPY
print('=== Gate 4: 収益計算基準 ===')
print(f'  Yahoo実績価格:    ¥{YAHOO_SOLD_JPY:,}  ({YAHOO_SOLD_DATE})')
print(f'  買い上限 (JPY):   ¥{buy_limit:,.0f}')
print(f'  買い上限 (USD):   ${buy_limit_usd:,.0f}  (@ {USD_JPY} JPY/USD)')
print()

# eBay OAuth token
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
if not token:
    print('ERROR: eBay token取得失敗')
    sys.exit(1)

def ebay_search(query, max_price_usd=None):
    headers = {'Authorization': f'Bearer {token}', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'}
    params = {
        'q': query,
        'category_ids': '11116',  # Coins: World
        'filter': 'itemLocationCountry:US,itemLocationCountry:GB',
        'limit': 10,
        'sort': 'price'
    }
    if max_price_usd:
        params['filter'] += f',price:[0..{max_price_usd}],currency:USD'
    r = requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search',
                     headers=headers, params=params)
    return r.json()

print('=== eBay検索1: NGC cert 403024500 ===')
res1 = ebay_search('NGC 403024500 France 100 francs')
items1 = res1.get('itemSummaries', [])
print(f'  結果: {len(items1)} 件')
for item in items1[:5]:
    price = item.get('price', {})
    price_usd = float(price.get('value', 0))
    currency = price.get('currency', '')
    total_cost = price_usd * USD_JPY * IMPORT_DUTY + FORWARDING_JPY + DOMESTIC_JPY
    profit = YAHOO_SOLD_JPY * (1 - YAHOO_FEE) - total_cost
    loc = item.get('itemLocation', {}).get('country', '?')
    print(f'  [{loc}] ${price_usd:,.0f} {currency} → 総費用¥{total_cost:,.0f} → 利益¥{profit:,.0f} | {item["title"][:60]}')

print()
print('=== eBay検索2: France 100 Francs Angel Gold MS62 ===')
res2 = ebay_search('France 100 francs gold angel MS62 NGC')
items2 = res2.get('itemSummaries', [])
print(f'  結果: {len(items2)} 件')
for item in items2[:8]:
    price = item.get('price', {})
    price_usd = float(price.get('value', 0))
    currency = price.get('currency', '')
    total_cost = price_usd * USD_JPY * IMPORT_DUTY + FORWARDING_JPY + DOMESTIC_JPY
    profit = YAHOO_SOLD_JPY * (1 - YAHOO_FEE) - total_cost
    loc = item.get('itemLocation', {}).get('country', '?')
    buying_options = item.get('buyingOptions', [])
    end_date = item.get('itemEndDate', '')[:16]
    print(f'  [{loc}] ${price_usd:,.0f} {currency} {buying_options} → 費用¥{total_cost:,.0f} 利益¥{profit:,.0f} | {item["title"][:55]}')

print()
print(f'  ※判定基準: 利益>0 かつ ROI>={PROFIT_MARGIN*100:.0f}% のみBUY候補')
