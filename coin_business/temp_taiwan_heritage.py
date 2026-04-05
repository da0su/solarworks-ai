import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone
c = get_client()
now = datetime.now(timezone.utc).isoformat()

# Taiwan 2000 yuan Year 60 (1971) NGC MS66 = 541,000
TW_REF = '36d7cc6a-0000-0000-0000-000000000000'
TW_PRICE = 541000

# Get actual ref ID
tw_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%2000圓%').gte('price_jpy',500000).order('price_jpy',desc=True).limit(1).execute().data
if tw_rows:
    TW_REF = str(tw_rows[0]['id'])
    print(f'Taiwan 2000 yuan ref: {TW_REF[:8]} {tw_rows[0]["price_jpy"]:,}')
else:
    print('No Taiwan 2000 yuan ref found')

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# Heritage HK Spring 2026 Taiwan gold lots
items = [
    ('heritage_hk_spring_world_2026_apr_25172',
     'China: Taiwan. Republic gold Centennial Sun Yat-Sen 2000 yuan 1965 MS65 NGC',
     'Taiwan 2000 yuan 1965 Centennial Sun Yat-Sen NGC MS65'),
    ('heritage_hk_spring_world_2026_apr_25173',
     'China: Taiwan. Republic gold Chiang Kai-shek 80th Birthday 2000 yuan 1966 MS65 NGC',
     'Taiwan 2000 yuan 1966 Chiang Kai-shek 80th Birthday NGC MS65'),
]

bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(TW_PRICE)
ok = 0
for item_id, title, note in items:
    comment = (
        f'[CAP 2026-04-04] TYPE_ONLY: {note} '
        f'vs Taiwan 2000 yuan YR60 MS66 541k. '
        f'Heritage HK Spring 61610 Apr2026. BL=${bl_usd}. '
        f'Taiwan commemorative gold JP demand uncertain->CEO_CHECK.'
    )
    try:
        c.table('ceo_review_log').update({
            'yahoo_ref_id': TW_REF,
            'yahoo_ref_title': 'Taiwan 2000 yuan YR60 NGC MS66 541k',
            'yahoo_ref_price_jpy': TW_PRICE,
            'cap_bid_limit_jpy': bl_jpy,
            'cap_bid_limit_usd': bl_usd,
            'estimated_sell_price_jpy': TW_PRICE,
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
        print(f'OK {item_id[-7:]} BL=${bl_usd}')
        ok += 1
    except Exception as e:
        print(f'FAIL {item_id}: {e}')

print(f'\nPromoted: {ok}')
rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'MARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
