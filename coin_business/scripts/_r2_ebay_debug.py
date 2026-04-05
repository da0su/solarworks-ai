"""eBay API デバッグ: フィルターなしで基本検索確認"""
import sys, io, os, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))

EBAY_CLIENT_ID = os.environ.get('EBAY_CLIENT_ID', '')
EBAY_CLIENT_SECRET = os.environ.get('EBAY_CLIENT_SECRET', '')

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
print(f'Token: {"OK" if token else "FAIL"}')

# Test 1: フィルターなし
def search(query, filters=None, limit=5):
    h = {'Authorization': f'Bearer {token}', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'}
    p = {'q': query, 'category_ids': '11116', 'limit': limit, 'sort': 'price'}
    if filters:
        p['filter'] = filters
    r = requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search', headers=h, params=p)
    data = r.json()
    if 'errors' in data:
        print(f'  ERROR: {data["errors"]}')
    return data.get('itemSummaries', [])

queries = [
    ('フィルターなし', 'double eagle gold $20 NGC MS63', None),
    ('US+GB filter', 'double eagle gold $20 NGC MS63', 'itemLocationCountry:US,itemLocationCountry:GB'),
    ('US filterのみ', 'double eagle gold $20 NGC MS63', 'itemLocationCountry:US'),
    ('price range', '100 francs gold angel NGC', 'price:[1000..10000],currency:USD'),
    ('簡単なクエリ', 'gold coin NGC MS63 graded', None),
    ('St Gaudens', 'Saint-Gaudens double eagle gold NGC', None),
    ('Liberty Head', 'Liberty Head double eagle gold PCGS graded', None),
]

for label, q, filt in queries:
    items = search(q, filt)
    print(f'\n[{label}] "{q[:45]}"')
    print(f'  フィルター: {filt or "なし"}')
    print(f'  結果: {len(items)} 件')
    for item in items[:3]:
        price_usd = float(item.get('price',{}).get('value', 0))
        loc = item.get('itemLocation',{}).get('country','?')
        opts = ','.join(item.get('buyingOptions',[]))
        print(f'  [{loc}] ${price_usd:,.0f} [{opts}] {item["title"][:60]}')
