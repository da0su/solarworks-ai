import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone, date
import uuid
c = get_client()
now = datetime.now(timezone.utc).isoformat()
today = date.today().isoformat()

RUSSIA_REF = '0b8c4816-954b-4a51-b7bb-5cc2aeea8437'
RUSSIA_PRICE = 172800
DUTCH_REF = '1facaff8-5653-4e9e-bf9c-bbbc19b45121'
DUTCH_PRICE = 284000

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# 1. Fix Russia Nicholas I 5 roubles
russia_bl_jpy, russia_bl_usd, russia_cost, russia_profit, russia_roi = calc_bl(RUSSIA_PRICE)
c.table('ceo_review_log').update({
    'yahoo_ref_id': RUSSIA_REF,
    'yahoo_ref_title': 'ロシア ニコライ2世 5ルーブル PCGS MS66 172,800',
    'yahoo_ref_price_jpy': RUSSIA_PRICE,
    'cap_bid_limit_jpy': russia_bl_jpy,
    'cap_bid_limit_usd': russia_bl_usd,
    'estimated_sell_price_jpy': RUSSIA_PRICE,
    'total_cost_jpy': russia_cost,
    'expected_profit_jpy': russia_profit,
    'expected_roi_pct': russia_roi,
    'cap_judgment': 'CEO_CHECK',
    'category': 'CEO_REVIEW',
    'marketing_status': 'MARKETING_REVIEW',
    'comparison_type': 'TYPE_ONLY',
    'cap_comment': f'[CAP 2026-04-04] TYPE_ONLY: Russia Nicholas I 5R 1842 vs Nicholas II 5R PCGS MS66 172,800. eBay price unknown->CEO check. BL=${russia_bl_usd}. Gold spike->CEO_CHECK.',
    'evidence_status': 'PRICE_NEEDED',
    'updated_at': now,
}).eq('id', '92c5bbaf-6689-4c4b-9f27-86b18f96c1a1').execute()
print(f'OK Russia Nicholas I -> CEO_CHECK BL=${russia_bl_usd}')

# 2-4. Heritage lots
sig_bl_jpy, sig_bl_usd, sig_cost, sig_profit, sig_roi = calc_bl(DUTCH_PRICE)

heritage_lots = [
    {
        'item_id': 'heritage_61607_24019',
        'url': 'https://coins.ha.com/itm/poland/danzig/poland-danzig-sigismund-iii-gold-restrike-5-ducat-1614-1996-pr69-ultra-cameo-ngc-/a/61607-24019.s',
        'title_snapshot': 'Poland: Danzig. Sigismund III gold Restrike 5 Ducat 1614 (1996) PR69 Ultra Cameo NGC',
        'country': 'Poland',
        'year': 1996,
        'price_usd': 825,
        'score': 19,
        'comment': f'[CAP 2026-04-04] TYPE_ONLY: Poland Danzig 5 Ducat 1996 restrike PR69 vs NL 10guilden 284k. Current bid $825<<BL${sig_bl_usd}. Heritage ends 4/14. Poland gold JP liquidity uncertain->CEO_CHECK.',
    },
    {
        'item_id': 'heritage_61607_24013',
        'url': 'https://coins.ha.com/itm/danzig/danzig-free-city-gold-25-gulden-1930-ms65-pcgs-/a/61607-24013.s',
        'title_snapshot': 'Danzig: Free City gold 25 Gulden 1930 MS65 PCGS',
        'country': 'Danzig',
        'year': 1930,
        'price_usd': 1650,
        'score': 21,
        'comment': f'[CAP 2026-04-04] TYPE_ONLY: Danzig 25 Gulden 1930 PCGS MS65 vs NL guilden 284k. Current bid $1,650>BL${sig_bl_usd} NOTE:over BL. Rare coin JP value may be higher. Heritage 4/14->CEO judge.',
    },
    {
        'item_id': 'heritage_61607_24012',
        'url': 'https://coins.ha.com/itm/danzig/danzig-free-city-gold-25-gulden-1930-ms66-ngc-/a/61607-24012.s',
        'title_snapshot': 'Danzig: Free City gold 25 Gulden 1930 MS66 NGC',
        'country': 'Danzig',
        'year': 1930,
        'price_usd': 2600,
        'score': 22,
        'comment': f'[CAP 2026-04-04] TYPE_ONLY: Danzig 25 Gulden 1930 MS66 (top grade) vs NL guilden 284k. Current bid $2,600>>BL${sig_bl_usd} WELL OVER. Highly specialized rare coin JP price uncertain. Heritage 4/14->CEO specialist judge.',
    },
]

ok = 0
for lot in heritage_lots:
    row = {
        'id': str(uuid.uuid4()),
        'marketplace': 'Heritage Auctions',
        'item_id': lot['item_id'],
        'url': lot['url'],
        'title_snapshot': lot['title_snapshot'],
        'country': lot['country'],
        'year': lot['year'],
        'material': 'gold',
        'comparison_type': 'TYPE_ONLY',
        'yahoo_ref_id': DUTCH_REF,
        'yahoo_ref_title': 'NL 10guilden 1898 NGC MS62 284k',
        'yahoo_ref_price_jpy': DUTCH_PRICE,
        'cap_bid_limit_jpy': sig_bl_jpy,
        'cap_bid_limit_usd': sig_bl_usd,
        'estimated_sell_price_jpy': DUTCH_PRICE,
        'total_cost_jpy': sig_cost,
        'expected_profit_jpy': sig_profit,
        'expected_roi_pct': sig_roi,
        'price_snapshot_usd': lot['price_usd'],
        'price_snapshot_jpy': lot['price_usd'] * 150,
        'cap_judgment': 'CEO_CHECK',
        'category': 'CEO_REVIEW',
        'marketing_status': 'MARKETING_REVIEW',
        'auction_house': 'HERITAGE',
        'source_group': 'WORLD',
        'review_bucket': 'GOLD_PREMIUM',
        'snapshot_score': lot['score'],
        'scan_date': today,
        'evidence_status': 'READY' if lot['price_usd'] < sig_bl_usd else 'PRICE_OVER_BL',
        'cap_comment': lot['comment'],
        'bid_count_snapshot': 5,
        'submit_count': 1,
        'first_seen_at': now,
        'created_at': now,
        'updated_at': now,
    }
    try:
        c.table('ceo_review_log').insert(row).execute()
        print(f'OK {lot["item_id"]}: BL=${sig_bl_usd} price=${lot["price_usd"]}')
        ok += 1
    except Exception as e:
        print(f'FAIL {lot["item_id"]}: {e}')

rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f'\nMARKETING_REVIEW: CEO_CHECK={cnt["CEO_CHECK"]} CAP_BUY={cnt["CAP_BUY"]}')
