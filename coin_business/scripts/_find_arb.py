"""
1コイン実戦テスト用 アービトラージ候補探索スクリプト
- coin_slab_data の高buy_limit案件を列挙
- eBay APIで現在価格を検索
- 採算チェック (eBay * 145 + コスト < buy_limit)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from scripts.supabase_client import get_client
from scripts.ebay_api_client import EbayBrowseAPI
import time

USD_JPY = 145.0
IMPORT_TAX_RATE = 0.12   # 8% + 4% margin
TRANSFER_FEE_JPY = 2000  # US→JP transfer
DOMESTIC_FEE_JPY = 750   # domestic shipping
YAHOO_FEE_RATE = 0.10    # Yahoo auction 10%

def total_cost_jpy(price_usd: float) -> int:
    """eBay落札価格USD → 実際の仕入れコスト円換算"""
    price_jpy = price_usd * USD_JPY
    import_tax = price_jpy * IMPORT_TAX_RATE
    total = price_jpy + import_tax + TRANSFER_FEE_JPY + DOMESTIC_FEE_JPY
    return int(total)

def projected_sell_jpy(buy_limit_jpy: int) -> int:
    """Yahoo仕入れ限界価格 → 想定売却価格"""
    # buy_limit = sell * (1 - 0.10) * (1 - 0.15 profit margin)
    # なので sell ≈ buy_limit / 0.765
    return int(buy_limit_jpy / 0.765)

def calc_profit(price_usd: float, buy_limit_jpy: int, sell_jpy: int) -> dict:
    cost = total_cost_jpy(price_usd)
    yahoo_fee = int(sell_jpy * YAHOO_FEE_RATE)
    profit = sell_jpy - cost - yahoo_fee
    roi = profit / cost * 100 if cost > 0 else 0
    return {
        "cost_jpy": cost,
        "sell_jpy": sell_jpy,
        "yahoo_fee": yahoo_fee,
        "profit_jpy": profit,
        "roi_pct": roi,
        "viable": profit > 0 and roi >= 10
    }

# 1. coin_slab_data から候補を取得
client = get_client()
print("Loading coin_slab_data (top 50 by ref1_buy_limit_jpy)...")
res = client.table('coin_slab_data').select(
    'id, slab_line1, slab_line2, slab_line3, grader, grade, cert_number, '
    'ref1_buy_limit_jpy, ref2_yahoo_price_jpy, price_jpy, material, weight_g, purity'
).gt('ref1_buy_limit_jpy', 50000).order('ref1_buy_limit_jpy', desc=True).limit(50).execute()

print(f"Found {len(res.data)} candidates with buy_limit > 50k JPY")
print()

# 2. eBay APIで検索
api = EbayBrowseAPI()
if not api.is_configured:
    print("ERROR: eBay API not configured")
    sys.exit(1)

print(f"{'Buy Limit':>10}  {'eBay USD':>9}  {'eBay JPY':>9}  {'Cost JPY':>9}  {'Profit':>8}  {'ROI':>6}  Grader Grade  Cert           Title")
print("-" * 130)

viable_candidates = []

for row in res.data[:50]:
    buy_limit = row.get('ref1_buy_limit_jpy') or 0
    grader = row.get('grader') or ''
    grade = row.get('grade') or ''
    cert = row.get('cert_number') or ''
    line1 = row.get('slab_line1') or ''
    line2 = row.get('slab_line2') or ''
    line3 = row.get('slab_line3') or ''
    title_str = f"{line1} {line2}".strip()

    # eBay検索クエリ作成
    if cert and grader in ('NGC', 'PCGS'):
        query = f"{grader} {cert}"
    else:
        query = f"{title_str} {grader} {grade}".strip()

    if len(query) < 5:
        continue

    try:
        results = api.search(query, limit=3)
        items = results.get('items', [])

        if not items:
            # Try broader search
            query2 = f"{line1} {grader} {grade}".strip()
            results = api.search(query2, limit=3)
            items = results.get('items', [])

        if items:
            # Use median price
            prices = sorted([i.get('price_usd', 0) for i in items if i.get('price_usd', 0) > 0])
            if not prices:
                continue
            median_usd = prices[len(prices)//2]

            sell_jpy = projected_sell_jpy(buy_limit)
            profit_info = calc_profit(median_usd, buy_limit, sell_jpy)

            marker = ">>VIABLE<<" if profit_info['viable'] else ""
            print(f"{buy_limit:>10,}  ${median_usd:>8.2f}  {int(median_usd*USD_JPY):>9,}  {profit_info['cost_jpy']:>9,}  {profit_info['profit_jpy']:>+8,}  {profit_info['roi_pct']:>5.1f}%  {grader:5} {grade:8}  {cert[:12]:12}  {title_str[:45]}  {marker}")

            if profit_info['viable']:
                viable_candidates.append({
                    'row': row,
                    'median_usd': median_usd,
                    'items': items,
                    'profit_info': profit_info,
                    'query': query
                })
        else:
            print(f"{buy_limit:>10,}  {'NO_EBAY':>9}  {'':>9}  {'':>9}  {'':>8}  {'':>6}  {grader:5} {grade:8}  {cert[:12]:12}  {title_str[:45]}")

        time.sleep(0.3)

    except Exception as e:
        print(f"  ERROR for {title_str[:40]}: {e}")

print()
print(f"=== VIABLE CANDIDATES: {len(viable_candidates)} ===")
for c in viable_candidates:
    row = c['row']
    pi = c['profit_info']
    print(f"  BUY LIMIT: {row.get('ref1_buy_limit_jpy'):,} JPY  eBay median: ${c['median_usd']:.2f}")
    print(f"  Cost: {pi['cost_jpy']:,} JPY | Sell: {pi['sell_jpy']:,} JPY | Profit: {pi['profit_jpy']:+,} JPY ({pi['roi_pct']:.1f}%)")
    print(f"  {row.get('grader','')} {row.get('grade','')} | Cert: {row.get('cert_number','')} | {row.get('slab_line1','')} {row.get('slab_line2','')}")
    for item in c['items'][:2]:
        print(f"    eBay: ${item.get('price_usd',0):.2f} | {item.get('title','')[:70]}")
        print(f"    URL: {item.get('url','')}")
    print()
