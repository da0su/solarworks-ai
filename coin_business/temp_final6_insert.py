import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone
c = get_client()
now = datetime.now(timezone.utc).isoformat()

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# Chile 8 escudos 1787 PCGS MS62 = 1,326,000
ESCUDO_REF = 'f8655cec-0000-0000-0000-000000000000'
ESCUDO_PRICE = 1326000

# Get actual ref
esc_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%エスクード%').gte('price_jpy',1300000).order('price_jpy',desc=True).limit(1).execute().data
if esc_rows:
    ESCUDO_REF = str(esc_rows[0]['id'])
    ESCUDO_PRICE = esc_rows[0]['price_jpy']
    print(f'Escudo ref: {ESCUDO_REF[:8]} {ESCUDO_PRICE:,}')

# PNG 100 kina 2020 PR70 = 321,000
PNG_REF = '84324423-0000-0000-0000-000000000000'
PNG_PRICE = 321000
png_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%100キナ%').gte('price_jpy',300000).order('price_jpy',desc=True).limit(1).execute().data
if png_rows:
    PNG_REF = str(png_rows[0]['id'])
    PNG_PRICE = png_rows[0]['price_jpy']
    print(f'PNG ref: {PNG_REF[:8]} {PNG_PRICE:,}')

# Queen Anne guinea 1714 (XF Details) = 485,000
GUINEA_REF = 'dc059121-0000-0000-0000-000000000000'
GUINEA_PRICE = 485000
g_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%ギニー%').gte('price_jpy',450000).lte('price_jpy',550000).order('price_jpy',desc=True).limit(1).execute().data
if g_rows:
    GUINEA_REF = str(g_rows[0]['id'])
    GUINEA_PRICE = g_rows[0]['price_jpy']
    print(f'Guinea ref: {GUINEA_REF[:8]} {GUINEA_PRICE:,}')

items = [
    # item_id, note, ref_id, ref_price, ref_label, sell_jpy
    ('noble_141_2026_apr_586', 'Mexico Charles III 8 escudos 1780 FF Mexico City', ESCUDO_REF, ESCUDO_PRICE, 'Chile 8 escudos 1787 MS62 1,326k', ESCUDO_PRICE),
    ('noble_141_2026_apr_479', 'PNG mint gold 100 kina 1975', PNG_REF, PNG_PRICE, 'PNG 100 kina 2020 PR70 321k', PNG_PRICE),
    ('noble_141_2026_apr_480', 'PNG mint gold 100 kina 1976 and 1979 (2 coins)', PNG_REF, PNG_PRICE, 'PNG 100 kina 2020 PR70 321k', PNG_PRICE * 2),
    ('noble_141_2026_apr_482', 'PNG proof gold 100 kina 1979', PNG_REF, PNG_PRICE, 'PNG 100 kina 2020 PR70 321k', PNG_PRICE),
    ('noble_141_2026_apr_570', 'Great Britain George III gold guinea 1792', GUINEA_REF, GUINEA_PRICE, 'Anne guinea 1714 NGC XF 485k', GUINEA_PRICE),
    ('noble_141_2026_apr_571', 'Great Britain George III gold half guinea 1793', GUINEA_REF, GUINEA_PRICE, 'Anne guinea ref 485k (half)', int(GUINEA_PRICE * 0.5)),
]

ok = 0
for item_id, note, ref_id, ref_price, ref_label, sell_jpy in items:
    bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(sell_jpy)
    comment = (
        f'[CAP 2026-04-04] TYPE_ONLY: {note} '
        f'vs {ref_label}. '
        f'Noble 141 Apr2026. BL=${bl_usd}. '
        f'Gold spike->CEO_CHECK.'
    )
    try:
        c.table('ceo_review_log').update({
            'yahoo_ref_id': ref_id,
            'yahoo_ref_title': ref_label,
            'yahoo_ref_price_jpy': ref_price,
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
        }).eq('item_id', item_id).eq('marketing_status', 'INVESTIGATION').execute()
        print(f'OK {item_id[-8:]} sell={sell_jpy:,} BL=${bl_usd}')
        ok += 1
    except Exception as e:
        print(f'FAIL {item_id}: {e}')

print(f'\nTotal promoted: {ok}')
rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'MARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
