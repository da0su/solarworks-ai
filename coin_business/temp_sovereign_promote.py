import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone, date
import uuid
c = get_client()
now = datetime.now(timezone.utc).isoformat()
today = date.today().isoformat()

# Australian/British sovereign Yahoo ref
AU_SOV_REF = '046037e4-7d45-4e7e-8f1d-2c4e9a5b0c1d'  # George V 1916M PCGS MS64 279,800
AU_SOV_PRICE = 279800

# Get actual ref ID
sov_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%ソブリン%').gte('price_jpy',250000).order('price_jpy',desc=True).limit(3).execute().data
if sov_rows:
    print('Sovereign refs found:')
    for r in sov_rows:
        print(f'  {str(r["id"])[:8]} {r["price_jpy"]:,} {r["title"][:50]}')
    AU_SOV_REF = str(sov_rows[0]['id'])
    AU_SOV_PRICE = sov_rows[0]['price_jpy']
else:
    print('No sovereign ref - using fallback')

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# Items to promote - these are in INVESTIGATION with no Yahoo ref
# n_coins = how many sovereigns in the lot
promote_items = [
    {
        'item_id': 'noble_141_2026_apr_823',
        'title': 'George V, 1915 Sydney. Extremely fine or better. (2)',
        'n_coins': 2,
        'year': 1915,
        'country': 'Australia',
        'note': 'George V 1915 Sydney 2x sovereigns',
    },
    {
        'item_id': 'noble_141_2026_apr_1008',
        'title': 'George V, 1931, 1936 (2). Extremely fine or better. (3)',
        'n_coins': 3,
        'year': 1931,
        'country': 'Australia',
        'note': 'George V 1931/1936 sovereigns x3',
    },
    {
        'item_id': 'noble_141_2026_apr_1051',
        'title': 'Edward VII, 1910. Frosty mint bloom, light gold and brown reverse tone',
        'n_coins': 1,
        'year': 1910,
        'country': 'Australia',
        'note': 'Edward VII 1910 sovereign',
    },
    {
        'item_id': 'noble_141_2026_apr_991',
        'title': 'George V, 1911. Subdued original mint bloom with gold olive toning',
        'n_coins': 1,
        'year': 1911,
        'country': 'Australia',
        'note': 'George V 1911 sovereign',
    },
    {
        'item_id': 'noble_141_2026_apr_636',
        'title': 'Queen Victoria, second type, 1863. Very fine or better',
        'n_coins': 1,
        'year': 1863,
        'country': 'Australia',
        'note': 'Queen Victoria 1863 sovereign (Sydney)',
    },
]

ok = 0
for item in promote_items:
    sell_jpy = item['n_coins'] * AU_SOV_PRICE
    bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(sell_jpy)
    comment = (
        f'[CAP 2026-04-04] TYPE_ONLY: {item["note"]} '
        f'vs AU George V 1916M MS64 279,800. '
        f'Noble 141 Apr2026. BL=${bl_usd}. '
        f'Gold spike->CEO_CHECK. {item["n_coins"]}x sell={sell_jpy:,}.'
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
        }).eq('item_id', item['item_id']).execute()
        print(f"OK {item['item_id'][-8:]} ({item['note'][:40]}) BL=${bl_usd}")
        ok += 1
    except Exception as e:
        print(f"FAIL {item['item_id']}: {e}")

print(f'\nTotal promoted: {ok}')
rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'MARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
