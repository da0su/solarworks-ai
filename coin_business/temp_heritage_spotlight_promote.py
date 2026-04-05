import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone, date
import uuid
c = get_client()
now = datetime.now(timezone.utc).isoformat()
today = date.today().isoformat()

# Yahoo refs
DUTCH_REF = '1facaff8-5653-4e9e-bf9c-bbbc19b45121'   # NL 10guilden 1898 NGC MS62 284k
DUTCH_PRICE = 284000
DUCAT_REF = 'e83518b9-0000-0000-0000-000000000000'   # NL 1 ducat 1649 NGC MS61 220k - placeholder
DUCAT_PRICE = 220000

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# Get actual ducat ref ID
from scripts.supabase_client import get_client
ducat_rows = c.table('market_transactions').select('id,title,price_jpy').eq('material','金').ilike('title','%1ダカット%').gte('price_jpy',200000).order('price_jpy',desc=True).limit(1).execute().data
if ducat_rows:
    DUCAT_REF = str(ducat_rows[0]['id'])
    DUCAT_PRICE = ducat_rows[0]['price_jpy']
    print(f'Ducat ref: {DUCAT_REF[:8]} price={DUCAT_PRICE:,} {ducat_rows[0]["title"][:40]}')
else:
    # Use Netherlands 10 guilden as fallback
    DUCAT_REF = DUTCH_REF
    DUCAT_PRICE = DUTCH_PRICE
    print(f'Fallback: using Dutch 10G ref')

# Group 1: Danzig 25 Gulden 1930 MS65/66 → Dutch 10G ref
danzig_25g_items = [
    ('heritage_spotlight_poland_2026_apr_24012', 'Danzig: Free City gold 25 Gulden 1930 MS66 NGC', 26, 'MS66'),
    ('heritage_spotlight_poland_2026_apr_24013', 'Danzig: Free City gold 25 Gulden 1930 MS65 PCGS', 26, 'MS65'),
    ('heritage_spotlight_poland_2026_apr_24014', 'Danzig: Free City gold 25 Gulden 1930 MS65 PCGS', 26, 'MS65'),
    ('heritage_spotlight_poland_2026_apr_24015', 'Danzig: Free City gold 25 Gulden 1930 MS65 NGC', 26, 'MS65'),
    ('heritage_spotlight_poland_2026_apr_24016', 'Danzig: Free City gold 25 Gulden 1930 MS65 PCGS', 26, 'MS65'),
    ('heritage_spotlight_poland_2026_apr_24011', 'Danzig: Free City gold 25 Gulden 1923 MS62 Prooflike NGC', 20, 'MS62PL'),
]

# Group 2: Sigismund III 5 Ducat restrikes → Dutch 10G ref
sigismund_items = [
    ('heritage_spotlight_poland_2026_apr_24018', 'Poland: Danzig. Sigismund III gold Proof Restrike 5 Ducat 1614 (1977)', 26, '1977PR'),
    ('heritage_spotlight_poland_2026_apr_24019', 'Poland: Danzig. Sigismund III gold Restrike 5 Ducat 1614 (1996) PR69 Ultra Cameo NGC', 26, '1996PR69'),
    ('heritage_spotlight_poland_2026_apr_24020', 'Poland: Danzig. Sigismund III gold Restrike 5 Ducat 1614 (1996) PR69 Ultra Cameo NGC', 26, '1996PR69'),
]

# Group 3: Johann Casimir Ducat 1657-1660 → Dutch ducat ref
ducat_items = [
    ('heritage_spotlight_poland_2026_apr_24021', 'Poland: Danzig. Johann Casimir gold Ducat 1657-DL AU50 PCGS', 18, 'AU50'),
    ('heritage_spotlight_poland_2026_apr_24022', 'Poland: Danzig. Johann Casimir gold Ducat 1658-DL AU55 PCGS', 18, 'AU55'),
    ('heritage_spotlight_poland_2026_apr_24023', 'Poland: Danzig. Johann II Casimir gold Ducat 1660-DL AU55 PCGS', 18, 'AU55'),
]

ok = 0

def update_item(item_id, title, score, grade_note, ref_id, ref_price, ref_label, sell_jpy):
    bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(sell_jpy)
    comment = (
        f'[CAP 2026-04-04] TYPE_ONLY: {title[:50]} ({grade_note}) '
        f'vs {ref_label} {ref_price:,}. '
        f'Heritage Spotlight Poland Apr2026. BL=${bl_usd}. '
        f'Poland gold JP liquidity uncertain->CEO_CHECK. Gold spike->CEO judge.'
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
        }).eq('item_id', item_id).execute()
        return True
    except Exception as e:
        print(f'  ERROR {item_id}: {e}')
        return False

# Process Group 1: Danzig 25G (sell = Dutch ref ¥284,000)
print('=== Danzig 25G lots ===')
for item_id, title, score, grade in danzig_25g_items:
    if update_item(item_id, title, score, grade, DUTCH_REF, DUTCH_PRICE, 'NL 10G 1898 MS62 284k', DUTCH_PRICE):
        bl_jpy, bl_usd, _, _, _ = calc_bl(DUTCH_PRICE)
        print(f'OK {item_id[-7:]} ({grade}) BL=${bl_usd}')
        ok += 1

# Process Group 2: Sigismund 5D (sell = Dutch ref ¥284,000)
print('=== Sigismund 5D lots ===')
for item_id, title, score, grade in sigismund_items:
    if update_item(item_id, title, score, grade, DUTCH_REF, DUTCH_PRICE, 'NL 10G 1898 MS62 284k', DUTCH_PRICE):
        bl_jpy, bl_usd, _, _, _ = calc_bl(DUTCH_PRICE)
        print(f'OK {item_id[-7:]} ({grade}) BL=${bl_usd}')
        ok += 1

# Process Group 3: Ducat 1657-1660 (sell = ducat ref price)
print('=== Danzig Ducat 1657-1660 lots ===')
for item_id, title, score, grade in ducat_items:
    if update_item(item_id, title, score, grade, DUCAT_REF, DUCAT_PRICE, f'NL ducat {DUCAT_PRICE:,}', DUCAT_PRICE):
        bl_jpy, bl_usd, _, _, _ = calc_bl(DUCAT_PRICE)
        print(f'OK {item_id[-7:]} ({grade}) BL=${bl_usd}')
        ok += 1

print(f'\nTotal promoted: {ok}/{len(danzig_25g_items)+len(sigismund_items)+len(ducat_items)}')

rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'MARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
