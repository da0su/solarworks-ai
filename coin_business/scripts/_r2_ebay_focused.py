"""Round 2 focused: France 20F / Japan 1Yen / GB Sovereign 詳細検索"""
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
FWD_JPY = 2000
DOM_JPY = 750
YAHOO_FEE = 0.10
PROFIT_MIN = 0.15

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

def search(query, loc_filter='itemLocationCountry:US', limit=8):
    h = {'Authorization': f'Bearer {token}', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'}
    p = {'q': query, 'category_ids': '11116', 'limit': limit, 'sort': 'price'}
    if loc_filter:
        p['filter'] = loc_filter
    r = requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search', headers=h, params=p)
    return r.json().get('itemSummaries', [])

def calc(price_usd, yahoo_jpy):
    total = price_usd * USD_JPY * IMPORT_DUTY + FWD_JPY + DOM_JPY
    net   = yahoo_jpy * (1 - YAHOO_FEE)
    profit = net - total
    roi   = profit / total if total > 0 else 0
    ok    = profit > 0 and roi >= PROFIT_MIN
    return total, profit, roi, ok

def bl_usd(jpy):
    return jpy * (1 - YAHOO_FEE) * (1 - PROFIT_MIN) / USD_JPY

longlist = []

# ============================================================
# 1. France 20 Francs Napoleon (34件 staging, 高流動性)
# ============================================================
print('\n' + '='*65)
print('[HIGH_GRADE / YEAR_DELTA] France 20 Francs Napoleon/Rooster')
# staging参照
yahoo_refs = {
    'MS65 (1869 BB Napoleon)': 298000,
    'MS62 (1869 Napoleon)': 249800,
    'MS63 (推定)': 265000,
    'MS64 (推定)': 280000,
}
for grade_label, yahoo_jpy in yahoo_refs.items():
    print(f'  Yahoo参照 {grade_label}: ¥{yahoo_jpy:,} → 買い上限 ${bl_usd(yahoo_jpy):,.0f}')

# eBay検索 - gradeなし幅広く
for q in ['France 20 francs Napoleon gold graded NGC PCGS',
          'France 20 francs gold coin Napoleon III MS65 PCGS NGC',
          '20 francs or napoleon france gold MS64 MS65',
          '20 francs france napoleon MS63 gold coin graded']:
    items = search(q, 'itemLocationCountry:US')
    if items:
        print(f'\n  クエリ: "{q[:55]}"  {len(items)}件')
        for item in items[:6]:
            usd = float(item.get('price',{}).get('value',0))
            loc = item.get('itemLocation',{}).get('country','?')
            opts = ','.join(item.get('buyingOptions',[]))
            us_uk = '✅' if loc in ('US','GB') else f'❌({loc})'
            # MS65基準で判定
            total, profit, roi, ok = calc(usd, 298000)
            flag = '🔥 BUY候補' if ok else ('⚠️ 要検討' if profit > -50000 else '❌')
            print(f'  [{us_uk}] ${usd:,} [{opts}] 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
            print(f'    {item["title"][:70]}')
            if ok:
                longlist.append({'series':'France 20F Napoleon','route':'HIGH_GRADE/YD',
                    'yahoo_jpy':298000,'price_usd':usd,'total_jpy':total,
                    'profit_jpy':profit,'roi':roi,'loc':loc,'opts':opts,
                    'title':item['title'],'item_id':item.get('itemId','')})
        break  # 最初にヒットしたクエリのみ

# ============================================================
# 2. Japan 1 Yen Meiji Old Silver (4件 staging)
# ============================================================
print('\n\n' + '='*65)
print('[HIGH_GRADE / YEAR_DELTA] Japan 1 Yen Meiji Old Silver (旧1円銀貨)')
japan_refs = {
    'MS64 (明治3年)': 224000,
    'MS62 (明治30年)': 75000,
    'AU58 (明治14年)': 71000,
    'MS63 (推定)': 138000,
}
for grade_label, yahoo_jpy in japan_refs.items():
    print(f'  Yahoo参照 {grade_label}: ¥{yahoo_jpy:,} → 買い上限 ${bl_usd(yahoo_jpy):,.0f}')

for q in ['Japan Yen silver coin PCGS NGC graded Meiji old',
          'Japan 1 yen silver NGC PCGS MS64 MS63',
          'Meiji Japan old silver yen graded PCGS NGC',
          'Japan old 1 yen silver coin graded MS62 MS63 MS64']:
    items = search(q, 'itemLocationCountry:US')
    if items:
        print(f'\n  クエリ: "{q[:55]}"  {len(items)}件')
        for item in items[:6]:
            usd = float(item.get('price',{}).get('value',0))
            loc = item.get('itemLocation',{}).get('country','?')
            opts = ','.join(item.get('buyingOptions',[]))
            us_uk = '✅' if loc in ('US','GB') else f'❌({loc})'
            total, profit, roi, ok = calc(usd, 224000)
            flag = '🔥 BUY候補' if ok else ('⚠️ 要検討' if profit > -30000 else '❌')
            print(f'  [{us_uk}] ${usd:,} [{opts}] 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
            print(f'    {item["title"][:70]}')
            if ok:
                longlist.append({'series':'Japan 1 Yen Meiji','route':'HIGH_GRADE/YD',
                    'yahoo_jpy':224000,'price_usd':usd,'total_jpy':total,
                    'profit_jpy':profit,'roi':roi,'loc':loc,'opts':opts,
                    'title':item['title'],'item_id':item.get('itemId','')})
        break

# ============================================================
# 3. GB Sovereign Gold (61件 staging, 流動性高い)
# ============================================================
print('\n\n' + '='*65)
print('[HIGH_GRADE / YEAR_DELTA] British Gold Sovereign')
# staging上位
gb_refs = {
    'PF69 (1985 5Pound)': 792000,
    'PF70UC (1982 2Sovereign)': 598000,
    'PF70UC (2009 2Sovereign)': 454000,
    'MS65 Sovereign (推定)': 120000,
    'MS64 Sovereign (推定)': 90000,
    'MS63 Sovereign (推定)': 75000,
}
for grade_label, yahoo_jpy in gb_refs.items():
    print(f'  Yahoo参照 {grade_label}: ¥{yahoo_jpy:,} → 買い上限 ${bl_usd(yahoo_jpy):,.0f}')

for q in ['Great Britain gold sovereign NGC MS63 MS64 graded',
          'British gold sovereign PCGS NGC graded coin',
          'UK sovereign gold coin NGC PCGS MS65 MS64',
          'British sovereign 1oz gold coin graded NGC']:
    items = search(q, 'itemLocationCountry:US')
    if items:
        print(f'\n  クエリ: "{q[:55]}"  {len(items)}件')
        for item in items[:6]:
            usd = float(item.get('price',{}).get('value',0))
            loc = item.get('itemLocation',{}).get('country','?')
            opts = ','.join(item.get('buyingOptions',[]))
            us_uk = '✅' if loc in ('US','GB') else f'❌({loc})'
            # MS65 sovereign推定価格¥120,000基準
            total, profit, roi, ok = calc(usd, 120000)
            flag = '🔥 BUY候補' if ok else ('⚠️ 要検討' if profit > -20000 else '❌')
            print(f'  [{us_uk}] ${usd:,} [{opts}] 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
            print(f'    {item["title"][:70]}')
            if ok:
                longlist.append({'series':'GB Sovereign','route':'HIGH_GRADE/YD',
                    'yahoo_jpy':120000,'price_usd':usd,'total_jpy':total,
                    'profit_jpy':profit,'roi':roi,'loc':loc,'opts':opts,
                    'title':item['title'],'item_id':item.get('itemId','')})
        break

# ============================================================
# 4. US $20 Double Eagle (念のため再確認)
# ============================================================
print('\n\n' + '='*65)
print('[YEAR_DELTA] US $20 Double Eagle (最安値確認)')
for q in ['double eagle $20 gold NGC MS62',
          'US $20 gold coin NGC PCGS graded Saint-Gaudens MS62']:
    items = search(q, 'itemLocationCountry:US')
    if items:
        print(f'  クエリ: "{q[:55]}"  {len(items)}件')
        for item in items[:4]:
            usd = float(item.get('price',{}).get('value',0))
            loc = item.get('itemLocation',{}).get('country','?')
            opts = ','.join(item.get('buyingOptions',[]))
            # MS62基準 ¥801,000
            total, profit, roi, ok = calc(usd, 801000)
            flag = '🔥 BUY候補' if ok else ('⚠️' if profit > -50000 else '❌')
            print(f'  [{loc}] ${usd:,} [{opts}] 費用¥{total:,.0f} 利益¥{profit:,.0f} ROI{roi*100:.1f}% {flag}')
            print(f'    {item["title"][:70]}')
        break

# ============================================================
print(f'\n\n{"="*65}')
print(f'=== LONGLIST 合計: {len(longlist)} 件 ===')
for i, r in enumerate(longlist, 1):
    print(f'{i}. [{r["route"]}] {r["series"]}')
    print(f'   eBay: ${r["price_usd"]:,.0f} → 費用¥{r["total_jpy"]:,.0f} | 利益¥{r["profit_jpy"]:,.0f} | ROI{r["roi"]*100:.1f}%')
    print(f'   {r["title"][:70]}')
