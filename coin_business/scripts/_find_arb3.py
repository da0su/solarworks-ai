"""
精密eBay検索: 特定コインタイプの手動検索
- France G100F (Napoleon/Third Republic era gold 100 Francs)
- Japan G1Y (Meiji gold yen)
- 1847 Gothic Crown GB
- Trade Dollar Japan
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from scripts.ebay_api_client import EbayBrowseAPI
import time

USD_JPY = 145.0
IMPORT_TAX_RATE = 0.12
TRANSFER_FEE_JPY = 2000
DOMESTIC_FEE_JPY = 750
YAHOO_FEE_RATE = 0.10

def total_cost_jpy(price_usd: float) -> int:
    price_jpy = price_usd * USD_JPY
    return int(price_jpy * (1 + IMPORT_TAX_RATE) + TRANSFER_FEE_JPY + DOMESTIC_FEE_JPY)

def check_viable(price_usd: float, buy_limit_jpy: int, sell_jpy: int = None) -> dict:
    cost = total_cost_jpy(price_usd)
    if sell_jpy is None:
        sell_jpy = int(buy_limit_jpy / 0.765)
    yahoo_fee = int(sell_jpy * YAHOO_FEE_RATE)
    profit = sell_jpy - cost - yahoo_fee
    roi = profit / cost * 100 if cost > 0 else 0
    return {"cost_jpy": cost, "sell_jpy": sell_jpy, "profit_jpy": profit, "roi_pct": roi,
            "viable": profit > 0 and roi >= 15.0}

api = EbayBrowseAPI()
if not api.is_configured:
    print("ERROR"); sys.exit(1)

# ========================================
# Searches: coin type + grader + grade
# ========================================
searches = [
    # (description, query, buy_limit_jpy)
    ("France 100 Francs 1869A NGC MS61",    "1869 France 100 Francs gold NGC",      596425),
    ("France 100 Francs 1857A NGC MS62",    "1857 France 100 Francs gold NGC MS62", 589413),
    ("France 100 Francs 1904A NGC MS62",    "1904 France 100 Francs gold NGC",      556761),
    ("France 20 Francs Napoleon NGC MS",    "France 20 Francs Napoleon gold NGC MS",200000),
    ("Japan G1Y (Meiji Gold Yen) NGC MS64", "1874 Japan gold yen NGC MS64",         558008),
    ("Japan Trade Dollar M8 PCGS MS62",     "1875 Japan Trade Dollar gold PCGS",    553776),
    ("1847 Gothic Crown GB NGC PF61",       "1847 Great Britain Gothic Crown NGC PF61 gold", 2012708),
    ("St. Helena Gold £5 Una NGC PF70",     "St Helena 5 pounds gold NGC PF70",     598635),
    ("2021 St Helena G5 Napoleon NGC PF69", "2021 St Helena Napoleon gold NGC PF69", 587583),
    ("Mexico G1 Onza 2011 NGC PF70",        "2011 Mexico gold Onza NGC PF70",       574044),
]

all_viable = []

for desc, query, buy_limit in searches:
    print(f"\n{'='*70}")
    print(f"SEARCH: {desc}")
    print(f"  Query: '{query}'  |  Buy Limit: ¥{buy_limit:,}")
    results = api.search(query, limit=5)
    items = results.get('items', [])
    time.sleep(0.5)

    if not items:
        print("  [NO RESULTS]")
        continue

    for item in items:
        p = item.get('price_usd', 0)
        t = item.get('title', '')[:75]
        url = item.get('url', '')
        pi = check_viable(p, buy_limit)
        marker = " *** BUY CANDIDATE ***" if pi['viable'] else ""
        print(f"  ${p:>8.2f}  cost=¥{pi['cost_jpy']:>8,}  profit=¥{pi['profit_jpy']:>+9,}  roi={pi['roi_pct']:>6.1f}%{marker}")
        print(f"    Title: {t}")
        print(f"    URL:   {url[:80]}")
        if pi['viable']:
            all_viable.append({'desc': desc, 'query': query, 'buy_limit': buy_limit,
                                'item': item, 'pi': pi})

print(f"\n{'='*70}")
print(f"SUMMARY: {len(all_viable)} viable candidates found")
for c in all_viable:
    pi = c['pi']
    item = c['item']
    print(f"\n  >> {c['desc']}")
    print(f"     eBay: ${item.get('price_usd',0):.2f}  cost=¥{pi['cost_jpy']:,}  profit=¥{pi['profit_jpy']:+,}  roi={pi['roi_pct']:.1f}%")
    print(f"     URL: {item.get('url','')}")
