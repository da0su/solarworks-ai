"""
精密検索v4:
1. Japan Trade Dollar cert#59077049で精密検索
2. 1869A G20F France gold cert#5959708-001で精密検索
3. 低価格帯 (30k-150k buy_limit) の一般的コイン検索
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from scripts.supabase_client import get_client
from scripts.ebay_api_client import EbayBrowseAPI
import time

USD_JPY = 145.0

def total_cost_jpy(price_usd: float) -> int:
    return int(price_usd * USD_JPY * 1.12 + 2000 + 750)

def calc_profit(price_usd: float, buy_limit_jpy: int) -> dict:
    cost = total_cost_jpy(price_usd)
    sell_jpy = int(buy_limit_jpy / 0.765)
    yahoo_fee = int(sell_jpy * 0.10)
    profit = sell_jpy - cost - yahoo_fee
    roi = profit / cost * 100 if cost > 0 else 0
    return {"cost": cost, "sell": sell_jpy, "profit": profit, "roi": roi,
            "viable": profit > 0 and roi >= 15.0}

client = get_client()
api = EbayBrowseAPI()

# =====================================================================
# 1. Japan Trade Dollar cert search
# =====================================================================
print("=== 1. Japan Trade Dollar Cert Search ===")
res = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).ilike('slab_line1', '%TRADE%').execute()
trade_coins = res.data
print(f"Japan Trade Dollar entries: {len(trade_coins)}")
for r in trade_coins[:5]:
    bl = r.get('ref1_buy_limit_jpy') or 0
    ref2 = r.get('ref2_yahoo_price_jpy') or 0
    print(f"  {r.get('grader','')} {r.get('grade','')} cert={r.get('cert_number','')} buy_limit=¥{bl:,} ref2=¥{ref2:,}")
    print(f"    {r.get('slab_line1','')} / {r.get('slab_line2','')}")

# Search by cert
for r in trade_coins[:3]:
    cert = r.get('cert_number', '')
    grader = r.get('grader', '')
    bl = r.get('ref1_buy_limit_jpy') or 0
    if not cert:
        continue
    q = f"{grader} {cert}"
    print(f"\n  Searching eBay: '{q}'")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    for item in items[:3]:
        p = item.get('price_usd', 0)
        pi = calc_profit(p, bl)
        t = item.get('title', '')[:60]
        print(f"    ${p:.2f} cost=¥{pi['cost']:,} profit=¥{pi['profit']:+,} roi={pi['roi']:.1f}%  {t}")
    time.sleep(0.5)

# =====================================================================
# 2. France G20F / Napoleon gold
# =====================================================================
print("\n=== 2. France Gold Coins in coin_slab_data ===")
res2 = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, slab_line3, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).or_('slab_line1.ilike.%FRANCE%,slab_line2.ilike.%FRANCE%').execute()
france_coins = [r for r in res2.data if r.get('ref1_buy_limit_jpy', 0) > 0]
france_coins.sort(key=lambda x: -(x.get('ref1_buy_limit_jpy') or 0))
print(f"France coins with buy_limit: {len(france_coins)}")
for r in france_coins[:10]:
    bl = r.get('ref1_buy_limit_jpy') or 0
    ref2 = r.get('ref2_yahoo_price_jpy') or 0
    print(f"  {r.get('grader','')} {r.get('grade',''):12} cert={r.get('cert_number','')[:14]:14} ¥{bl:>8,} ref2=¥{ref2:>8,}  {r.get('slab_line1','')} {r.get('slab_line2','')}")

# =====================================================================
# 3. Lower buy_limit coins (30k-150k)
# =====================================================================
print("\n=== 3. Lower buy_limit coins (30k-150k) ===")
res3 = client.table('coin_slab_data').select(
    'slab_line1, slab_line2, grader, grade, cert_number, ref1_buy_limit_jpy, ref2_yahoo_price_jpy'
).gte('ref1_buy_limit_jpy', 30000).lte('ref1_buy_limit_jpy', 150000).not_.is_('cert_number', 'null').order('ref1_buy_limit_jpy', desc=True).limit(20).execute()
print(f"Found {len(res3.data)} coins")
for r in res3.data:
    bl = r.get('ref1_buy_limit_jpy') or 0
    print(f"  {r.get('grader',''):5} {r.get('grade',''):10} cert={r.get('cert_number','')[:14]:14} ¥{bl:>7,}  {r.get('slab_line1','')} {r.get('slab_line2','')}")

# =====================================================================
# 4. Specific eBay check: Japan Gold Yen cert 6652609-003
# =====================================================================
print("\n=== 4. Direct cert searches for Japan gold coins ===")
japan_gold_certs = [
    ("NGC", "6652609-003", 558008, "M7(1874) JAPAN G1Y NGC MS64"),
]
for grader, cert, bl, desc in japan_gold_certs:
    q = f"{grader} {cert}"
    print(f"\n  Cert search: '{q}' ({desc}) buy_limit=¥{bl:,}")
    results = api.search(q, limit=5)
    items = results.get('items', [])
    if not items:
        # Try title search
        q2 = "1874 Japan 1 Yen Gold NGC MS64"
        print(f"  No cert results. Trying '{q2}'")
        results = api.search(q2, limit=5)
        items = results.get('items', [])
    for item in items[:5]:
        p = item.get('price_usd', 0)
        pi = calc_profit(p, bl)
        t = item.get('title', '')[:70]
        url = item.get('url', '')[:80]
        print(f"    ${p:.2f} cost=¥{pi['cost']:,} profit=¥{pi['profit']:+,} roi={pi['roi']:.1f}%")
        print(f"    {t}")
        print(f"    {url}")
    time.sleep(0.5)

# =====================================================================
# 5. Check what coins in coin_slab_data appear to have CLEAN grades
#    and are from US series (Morgan Dollar, Peace Dollar, etc.)
# =====================================================================
print("\n=== 5. US Coin series in coin_slab_data ===")
us_queries = ['MORGAN', 'PEACE', 'EAGLE', 'SAINT', 'BUFFALO', 'WALKER', 'FRANKLIN', 'KENNEDY']
for q in us_queries:
    res_us = client.table('coin_slab_data').select('slab_line1, ref1_buy_limit_jpy').ilike('slab_line1', f'%{q}%').execute()
    if res_us.data:
        prices = sorted([r.get('ref1_buy_limit_jpy') or 0 for r in res_us.data], reverse=True)
        print(f"  {q}: {len(res_us.data)} coins, max=¥{prices[0]:,}, median=¥{prices[len(prices)//2]:,}")
    else:
        print(f"  {q}: 0 coins")
