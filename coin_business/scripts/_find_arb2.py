"""
1コイン実戦テスト: 証明書番号ベースの精密アービトラージ検索
- 中程度の buy_limit (100k-500k JPY) 案件をターゲット
- NGC cert番号で精密一致検索
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from scripts.supabase_client import get_client
from scripts.ebay_api_client import EbayBrowseAPI
import time

USD_JPY = 145.0
IMPORT_TAX_RATE = 0.12
TRANSFER_FEE_JPY = 2000
DOMESTIC_FEE_JPY = 750
YAHOO_FEE_RATE = 0.10

def total_cost_jpy(price_usd: float) -> int:
    price_jpy = price_usd * USD_JPY
    import_tax = price_jpy * IMPORT_TAX_RATE
    return int(price_jpy + import_tax + TRANSFER_FEE_JPY + DOMESTIC_FEE_JPY)

def calc_profit(price_usd: float, sell_jpy: int) -> dict:
    cost = total_cost_jpy(price_usd)
    yahoo_fee = int(sell_jpy * YAHOO_FEE_RATE)
    profit = sell_jpy - cost - yahoo_fee
    roi = profit / cost * 100 if cost > 0 else 0
    return {"cost_jpy": cost, "sell_jpy": sell_jpy, "profit_jpy": profit, "roi_pct": roi,
            "viable": profit > 0 and roi >= 15.0}

client = get_client()
api = EbayBrowseAPI()

if not api.is_configured:
    print("ERROR: eBay API not configured"); sys.exit(1)

# Focus on coins with buy_limit in range 100k-600k - more realistic / verifiable
print("Loading coin_slab_data (buy_limit 100k~600k)...")
res = client.table('coin_slab_data').select(
    'id, slab_line1, slab_line2, slab_line3, grader, grade, cert_number, '
    'ref1_buy_limit_jpy, ref2_yahoo_price_jpy, price_jpy, management_no'
).gte('ref1_buy_limit_jpy', 100000).lte('ref1_buy_limit_jpy', 600000).not_.is_('cert_number', 'null').order('ref1_buy_limit_jpy', desc=True).limit(60).execute()

print(f"Found {len(res.data)} candidates")
print()

viable_candidates = []
checked = 0

for row in res.data:
    buy_limit = row.get('ref1_buy_limit_jpy') or 0
    grader = row.get('grader') or ''
    grade = row.get('grade') or ''
    cert = row.get('cert_number') or ''
    line1 = row.get('slab_line1') or ''
    line2 = row.get('slab_line2') or ''
    mgmt = row.get('management_no') or ''

    if not cert or grader not in ('NGC', 'PCGS'):
        continue

    # ref2_yahoo_price is more accurate sell estimate
    ref2 = row.get('ref2_yahoo_price_jpy') or 0
    sell_jpy = ref2 if ref2 > 0 else int(buy_limit / 0.765)

    title_str = f"{line1} {line2}".strip()

    # Strategy 1: search by cert number (most precise)
    query_cert = f"{grader} {cert}"
    # Strategy 2: search by title keywords
    query_title = f"{line1} {grader} {grade}".strip()

    results_cert = api.search(query_cert, limit=5)
    items_cert = results_cert.get('items', [])
    time.sleep(0.4)

    checked += 1
    print(f"[{checked:3}] {buy_limit:>8,} JPY | {grader} {grade:12} | Cert: {cert[:16]:16} | {title_str[:45]}")

    if items_cert:
        for item in items_cert[:3]:
            p = item.get('price_usd', 0)
            item_title = item.get('title', '')[:60]
            cost = total_cost_jpy(p)
            pi = calc_profit(p, sell_jpy)
            marker = "  *** VIABLE ***" if pi['viable'] else ""
            print(f"       eBay: ${p:>8.2f} (~¥{int(p*USD_JPY):>8,}) cost=¥{cost:>8,} profit=¥{pi['profit_jpy']:>+8,} {pi['roi_pct']:>5.1f}%  {item_title}{marker}")
            if pi['viable']:
                viable_candidates.append({
                    'row': row,
                    'item': item,
                    'pi': pi,
                    'query': query_cert
                })
    else:
        # Try title-based search
        results2 = api.search(query_title, limit=3)
        items2 = results2.get('items', [])
        time.sleep(0.4)
        if items2:
            for item in items2[:2]:
                p = item.get('price_usd', 0)
                item_title = item.get('title', '')[:60]
                pi = calc_profit(p, sell_jpy)
                marker = "  *** VIABLE ***" if pi['viable'] else ""
                print(f"       [title] eBay: ${p:>8.2f} (~¥{int(p*USD_JPY):>8,}) profit=¥{pi['profit_jpy']:>+8,} {pi['roi_pct']:>5.1f}%  {item_title}{marker}")
                if pi['viable']:
                    viable_candidates.append({
                        'row': row,
                        'item': item,
                        'pi': pi,
                        'query': query_title
                    })
        else:
            print("       [NO eBay results]")

print()
print("=" * 80)
print(f"=== VIABLE CANDIDATES (ROI >= 15%): {len(viable_candidates)} ===")
print("=" * 80)
for c in viable_candidates[:10]:
    row = c['row']
    pi = c['pi']
    item = c['item']
    print()
    print(f"  COIN: {row.get('slab_line1','')} {row.get('slab_line2','')}")
    print(f"  Cert: {row.get('cert_number','')}  Grader: {row.get('grader','')}  Grade: {row.get('grade','')}")
    print(f"  Buy Limit: ¥{row.get('ref1_buy_limit_jpy',0):,} | Est Sell: ¥{pi['sell_jpy']:,}")
    print(f"  eBay price: ${item.get('price_usd',0):.2f} | Cost all-in: ¥{pi['cost_jpy']:,}")
    print(f"  Profit: ¥{pi['profit_jpy']:+,} | ROI: {pi['roi_pct']:.1f}%")
    print(f"  eBay title: {item.get('title','')[:70]}")
    print(f"  eBay URL: {item.get('url','')}")
