import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone
c = get_client()
now = datetime.now(timezone.utc).isoformat()

# Correct sovereign ref
AU_SOV_REF = '046037e4-7d45-4e7e-8f1d-2c4e9a5b0c1d'  # placeholder - get from DB
AU_SOV_PRICE = 279800

# Verify ref ID
# Use direct UUID lookup
sov_rows = c.table('market_transactions').select('id,title,price_jpy').eq('id','046037e4-7d45-4e7e-8f1d-2c4e9a5b0c1d').execute().data
if not sov_rows:
    # Try the other ID
    sov_rows = c.table('market_transactions').select('id,title,price_jpy').eq('id','da13a9cd-0000-0000-0000-000000000000').execute().data
if not sov_rows:
    # Search by price and title
    sov_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').eq('price_jpy',279800).ilike('title','%1916%').limit(1).execute().data
if sov_rows:
    AU_SOV_REF = str(sov_rows[0]['id'])
    print(f'Found: {AU_SOV_REF[:8]} {sov_rows[0]["price_jpy"]:,} {sov_rows[0]["title"][:40]}')
else:
    print(f'Using hardcoded ref: {AU_SOV_REF[:8]}')

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

items = [
    ('noble_141_2026_apr_823', 2, 'George V 1915 Sydney 2x sovereigns', 1915),
    ('noble_141_2026_apr_1008', 3, 'George V 1931/1936 sovereigns x3', 1931),
    ('noble_141_2026_apr_1051', 1, 'Edward VII 1910 sovereign', 1910),
    ('noble_141_2026_apr_991', 1, 'George V 1911 sovereign', 1911),
    ('noble_141_2026_apr_636', 1, 'Queen Victoria 1863 sovereign', 1863),
]

ok = 0
for item_id, n_coins, note, year in items:
    sell_jpy = n_coins * AU_SOV_PRICE
    bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(sell_jpy)
    comment = (
        f'[CAP 2026-04-04] TYPE_ONLY: {note} '
        f'vs AU George V 1916M MS64 279,800. '
        f'Noble 141 Apr2026. BL=${bl_usd}. '
        f'Gold spike->CEO_CHECK. {n_coins}x sell={sell_jpy:,}.'
    )
    try:
        c.table('ceo_review_log').update({
            'yahoo_ref_id': AU_SOV_REF,
            'yahoo_ref_title': 'AU sovereign George V 1916M PCGS MS64 279k',
            'yahoo_ref_price_jpy': AU_SOV_PRICE,
            'cap_bid_limit_jpy': bl_jpy,
            'cap_bid_limit_usd': bl_usd,
            'estimated_sell_price_jpy': sell_jpy,
            'total_cost_jpy': total_cost,
            'expected_profit_jpy': profit,
            'expected_roi_pct': roi,
            'cap_judgment': 'CEO_CHECK',
            'category': 'CEO_REVIEW',
            'marketing_status': 'MARKETING_REVIEW',
            'comparison_type': 'TYPE_ONLY',
            'evidence_status': 'PRICE_NEEDED',
            'cap_comment': comment,
            'updated_at': now,
        }).eq('item_id', item_id).execute()
        print(f'OK {item_id[-8:]} n={n_coins} sell={sell_jpy:,} BL=${bl_usd}')
        ok += 1
    except Exception as e:
        print(f'FAIL {item_id}: {e}')

print(f'\nFixed: {ok}')
rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'MARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
