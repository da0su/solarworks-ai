"""
最終候補確認: British Sovereign, Swiss 20Fr, Japan Gold Yen 詳細
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from scripts.supabase_client import get_client
from scripts.ebay_api_client import EbayBrowseAPI
import time

USD_JPY = 145.0

def cost_jpy(usd):
    return int(usd * USD_JPY * 1.12 + 2000 + 750)

def profit_calc(usd, buy_limit):
    cost = cost_jpy(usd)
    sell = int(buy_limit / 0.765)
    fee = int(sell * 0.10)
    profit = sell - cost - fee
    roi = profit / cost * 100 if cost else 0
    return cost, sell, profit, roi, profit > 0 and roi >= 15

client = get_client()
api = EbayBrowseAPI()

# ============================
# 1. British Sovereign 1965
# ============================
print("=== 1. British Gold Sovereign 1965 NGC MS65 ===")
res = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, slab_line3, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).ilike('slab_line1', '%1965%').ilike('slab_line1', '%SOV%').execute()
for r in res.data[:5]:
    print(f"  {r.get('grader','')} {r.get('grade',''):12} cert={r.get('cert_number','')[:14]:14} ¥{r.get('ref1_buy_limit_jpy',0):>8,} ref2=¥{r.get('ref2_yahoo_price_jpy',0) or 0:>8,}")
    print(f"    {r.get('slab_line1','')} / {r.get('slab_line2','')}")

# eBay search
for q, bl in [("1965 Great Britain Gold Sovereign NGC MS65", 149902),
               ("1965 Britain Sovereign PCGS MS65 gold", 149902)]:
    print(f"\n  eBay search: '{q}'")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    for item in items[:4]:
        p = item.get('price_usd', 0)
        t = item.get('title', '')[:65]
        url = item.get('url', '')[:80]
        cost, sell, profit, roi, ok = profit_calc(p, bl)
        marker = " ***" if ok else ""
        print(f"    ${p:>8.2f}  cost=¥{cost:>8,}  profit=¥{profit:>+9,}  roi={roi:>7.1f}%{marker}")
        print(f"    {t}")
    time.sleep(0.5)

# ============================
# 2. Swiss 20 Francs 1926B
# ============================
print("\n=== 2. Swiss 20 Francs 1926-B PCGS MS66 ===")
res2 = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).ilike('slab_line1', '%20 Fr%').execute()
swiss = [r for r in res2.data if '1926' in (r.get('slab_line1','') + r.get('slab_line2',''))]
for r in swiss[:5]:
    print(f"  {r.get('grader','')} {r.get('grade',''):12} cert={r.get('cert_number','')[:14]:14} ¥{r.get('ref1_buy_limit_jpy',0):>8,} ref2=¥{r.get('ref2_yahoo_price_jpy',0) or 0:>8,}")

# eBay search
for q, bl in [("1926-B Switzerland 20 Francs gold PCGS MS66", 149594),
               ("Switzerland 20 Franc Helvetia gold NGC MS65", 149594)]:
    print(f"\n  eBay search: '{q}'")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    for item in items[:4]:
        p = item.get('price_usd', 0)
        t = item.get('title', '')[:65]
        cost, sell, profit, roi, ok = profit_calc(p, bl)
        marker = " ***" if ok else ""
        print(f"    ${p:>8.2f}  cost=¥{cost:>8,}  profit=¥{profit:>+9,}  roi={roi:>7.1f}%{marker}")
        print(f"    {t}")
    time.sleep(0.5)

# ============================
# 3. Denmark 20 Krone
# ============================
print("\n=== 3. Denmark 20 Krone 1873 NGC MS64 ===")
res3 = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).ilike('slab_line1', '%DENMARK%').execute()
for r in res3.data[:5]:
    print(f"  {r.get('grader','')} {r.get('grade',''):12} cert={r.get('cert_number','')[:14]:14} ¥{r.get('ref1_buy_limit_jpy',0):>8,} ref2=¥{r.get('ref2_yahoo_price_jpy',0) or 0:>8,}")

for q, bl in [("1873 Denmark 20 Krone gold NGC MS64", 149273),
               ("Denmark 20 Krone gold graded MS", 149273)]:
    print(f"\n  eBay search: '{q}'")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    for item in items[:4]:
        p = item.get('price_usd', 0)
        t = item.get('title', '')[:65]
        cost, sell, profit, roi, ok = profit_calc(p, bl)
        marker = " ***" if ok else ""
        print(f"    ${p:>8.2f}  cost=¥{cost:>8,}  profit=¥{profit:>+9,}  roi={roi:>7.1f}%{marker}")
        print(f"    {t}")
    time.sleep(0.5)

# ============================
# 4. Japan G1Y detailed check
# ============================
print("\n=== 4. Japan Gold 1 Yen DETAILED CHECK ===")
res4 = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, slab_line3, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy, management_no'
).or_('slab_line1.ilike.%G1Y%,slab_line2.ilike.%G1Y%').execute()
for r in res4.data[:10]:
    bl = r.get('ref1_buy_limit_jpy') or 0
    ref2 = r.get('ref2_yahoo_price_jpy') or 0
    print(f"  {r.get('grader',''):5} {r.get('grade',''):15} cert={r.get('cert_number','')[:14]:14} ¥{bl:>8,} ref2=¥{ref2:>8,}  {r.get('slab_line1','')} {r.get('slab_line2','')}")

# Best G1Y eBay search
for q, bl in [("Japan Gold 1 Yen Meiji NGC MS64", 558008),
               ("Japan Gold 1 Yen 1874 M7 NGC", 558008),
               ("Japan Gold 1 Yen 1874 PCGS MS64", 558008)]:
    print(f"\n  eBay search: '{q}'")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    for item in items[:3]:
        p = item.get('price_usd', 0)
        t = item.get('title', '')[:65]
        url = item.get('url', '')
        cost, sell, profit, roi, ok = profit_calc(p, bl)
        marker = " *** VIABLE ***" if ok else ""
        print(f"    ${p:>8.2f}  cost=¥{cost:>8,}  profit=¥{profit:>+9,}  roi={roi:>7.1f}%{marker}")
        print(f"    Title: {t}")
        print(f"    URL:   {url[:90]}")
    time.sleep(0.5)
