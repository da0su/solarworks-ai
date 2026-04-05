"""Round 3: France 20 Francs Napoleon - eBay Completed Sold 調査
目的: 現在出品が高いだけか、それとも成立相場も高く構造的に不採算かを確認
"""
import sys, io, os, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))

EBAY_APP_ID = os.environ.get('EBAY_CLIENT_ID', '')
USD_JPY     = 145.0
IMPORT_DUTY = 1.12
FWD_JPY     = 2000
DOM_JPY     = 750
YAHOO_FEE   = 0.10
PROFIT_MIN  = 0.15

# Yahoo参照価格
YAHOO_REFS = {
    'MS65': 298000,
    'MS64': 280000,
    'MS63': 265000,
    'MS62': 249800,
}

def bl_usd(yahoo_jpy):
    """買い上限(USD)"""
    return yahoo_jpy * (1 - YAHOO_FEE) * (1 - PROFIT_MIN) / USD_JPY

def calc(price_usd, yahoo_jpy):
    total  = price_usd * USD_JPY * IMPORT_DUTY + FWD_JPY + DOM_JPY
    net    = yahoo_jpy * (1 - YAHOO_FEE)
    profit = net - total
    roi    = profit / total if total > 0 else 0
    ok     = profit > 0 and roi >= PROFIT_MIN
    return total, profit, roi, ok

def search_completed(keywords, page=1, entries=20):
    """eBay Finding API - findCompletedItems"""
    url = 'https://svcs.ebay.com/services/search/FindingService/v1'
    params = {
        'OPERATION-NAME':       'findCompletedItems',
        'SERVICE-VERSION':      '1.0.0',
        'SECURITY-APPNAME':     EBAY_APP_ID,
        'RESPONSE-DATA-FORMAT': 'JSON',
        'REST-PAYLOAD':         '',
        'keywords':             keywords,
        'categoryId':           '11116',
        'sortOrder':            'PricePlusShippingLowest',
        'paginationInput.entriesPerPage': str(entries),
        'paginationInput.pageNumber':     str(page),
        # US/UK only
        'itemFilter(0).name':   'LocatedIn',
        'itemFilter(0).value(0)': 'US',
        'itemFilter(0).value(1)': 'GB',
        # 落札済み（sold=true）
        'itemFilter(1).name':   'SoldItemsOnly',
        'itemFilter(1).value':  'true',
    }
    r = requests.get(url, params=params)
    try:
        data = r.json()
        result = data.get('findCompletedItemsResponse', [{}])[0]
        items = result.get('searchResult', [{}])[0].get('item', [])
        total_entries = int(result.get('paginationOutput', [{}])[0].get('totalEntries', ['0'])[0])
        return items, total_entries
    except Exception as e:
        print(f'  API ERROR: {e}')
        print(f'  Response: {r.text[:300]}')
        return [], 0

print('=' * 65)
print('Round 3: France 20 Francs Napoleon - Completed Sold 調査')
print('=' * 65)

for grade_label, yahoo_jpy in YAHOO_REFS.items():
    print(f'  Yahoo参照 {grade_label}: ¥{yahoo_jpy:,} → 買い上限 ${bl_usd(yahoo_jpy):,.0f}')

buy_candidates = []
all_sold = []

QUERIES = [
    'France 20 francs Napoleon gold NGC PCGS graded',
    '20 francs or napoleon france gold graded NGC',
    'France Napoleon 20 francs gold coin NGC MS',
    '20 francs france napoleon III gold PCGS graded',
    'Rooster 20 francs france gold NGC graded',
]

for q in QUERIES:
    items, total = search_completed(q)
    if not items:
        print(f'\n  [{q[:50]}]: 0件')
        continue
    print(f'\n  [{q[:50]}]: {total}件中 {len(items)}件表示')
    for item in items:
        title = item.get('title', [''])[0]
        price_info = item.get('sellingStatus', [{}])[0].get('currentPrice', [{}])[0]
        price_val  = float(price_info.get('__value__', 0))
        currency   = price_info.get('@currencyId', '?')
        end_time   = item.get('listingInfo', [{}])[0].get('endTime', ['?'])[0]
        loc        = item.get('location', ['?'])[0]
        condition  = item.get('condition', [{}])[0].get('conditionDisplayName', ['?'])[0]

        # USD確認
        if currency != 'USD':
            print(f'  [SKIP non-USD {currency}] {title[:50]}')
            continue

        usd = price_val
        # グレード推定（タイトルから）
        title_up = title.upper()
        if 'MS65' in title_up or 'MS 65' in title_up:
            ref_grade, ref_jpy = 'MS65', YAHOO_REFS['MS65']
        elif 'MS64' in title_up or 'MS 64' in title_up:
            ref_grade, ref_jpy = 'MS64', YAHOO_REFS['MS64']
        elif 'MS63' in title_up or 'MS 63' in title_up:
            ref_grade, ref_jpy = 'MS63', YAHOO_REFS['MS63']
        elif 'MS62' in title_up or 'MS 62' in title_up:
            ref_grade, ref_jpy = 'MS62', YAHOO_REFS['MS62']
        else:
            ref_grade, ref_jpy = 'MS62', YAHOO_REFS['MS62']  # 保守推定

        # NGC/PCGS確認
        certified = 'NGC' in title_up or 'PCGS' in title_up

        total_cost, profit, roi, ok = calc(usd, ref_jpy)
        flag = '🔥 BUY候補' if ok else ('⚠️ 要検討' if profit > -30000 else '❌')

        us_uk = '✅' if any(c in loc.upper() for c in ('UNITED STATES', 'US,', ',US', 'UNITED KINGDOM', 'GB,', ',GB')) else f'❓({loc[:15]})'

        all_sold.append({
            'title': title, 'usd': usd, 'ref_grade': ref_grade, 'ref_jpy': ref_jpy,
            'total_jpy': total_cost, 'profit_jpy': profit, 'roi': roi, 'ok': ok,
            'certified': certified, 'end_time': end_time[:10] if end_time != '?' else '?',
        })

        print(f'  [{us_uk}][Cert={certified}] ${usd:,.0f} ({ref_grade}基準) 費用¥{total_cost:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
        print(f'    {title[:70]}')
        print(f'    終了: {end_time[:10] if end_time != "?" else "?"}  条件: {condition[:30]}')

        if ok:
            buy_candidates.append({'title': title, 'usd': usd, 'ref_grade': ref_grade,
                'ref_jpy': ref_jpy, 'total_jpy': total_cost, 'profit_jpy': profit, 'roi': roi})
    break  # 最初のヒットクエリのみ

# ============================================================
# サマリー
# ============================================================
print(f'\n{"=" * 65}')
print(f'=== Round 3 Completed Sold サマリー ===')
print(f'  調査件数: {len(all_sold)}件')
print(f'  BUY候補: {len(buy_candidates)}件')

if all_sold:
    usd_prices = [x['usd'] for x in all_sold]
    print(f'  成立価格帯: ${min(usd_prices):,.0f} 〜 ${max(usd_prices):,.0f}')
    print(f'  中央値: ${sorted(usd_prices)[len(usd_prices)//2]:,.0f}')
    under_limit = [x for x in all_sold if x['usd'] <= bl_usd(YAHOO_REFS['MS62'])]
    print(f'  買い上限以下($1,318): {len(under_limit)}件')

print()
if buy_candidates:
    print('=== BUY候補一覧 ===')
    for i, c in enumerate(buy_candidates, 1):
        print(f'{i}. ${c["usd"]:,.0f} ({c["ref_grade"]}) → 利益¥{c["profit_jpy"]:,.0f} ROI{c["roi"]*100:.1f}%')
        print(f'   {c["title"][:70]}')
else:
    print('→ BUY候補なし')
    if all_sold:
        print()
        print('=== 構造分析 ===')
        usd_prices = sorted([x['usd'] for x in all_sold])
        print(f'  eBay成立最安値: ${usd_prices[0]:,.0f}')
        print(f'  買い上限(MS62): ${bl_usd(YAHOO_REFS["MS62"]):,.0f}')
        gap = usd_prices[0] - bl_usd(YAHOO_REFS['MS62'])
        print(f'  最安値と買い上限の差: +${gap:,.0f}')
        if gap > 0:
            print('  → 成立相場が構造的に買い上限を上回っている（現出品だけが高いわけではない）')
        else:
            print('  → 買い上限以下の成立あり（現出品が高いだけの可能性あり）')
