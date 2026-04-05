import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone, date
import uuid
c = get_client()
now = datetime.now(timezone.utc).isoformat()
today = date.today().isoformat()

# Yahoo ref: US $20 Double Eagle MS63 PCGS 540,000
US20_REF = 'de5e36fe-8a97-4db1-81f1-59afbf1e1e47'
US20_PRICE = 540000

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(US20_PRICE)
est_aud = 5500
est_usd = est_aud * 0.62
price_jpy = int(est_aud * 95)

comment = (
    f"[CAP 2026-04-04] TYPE_ONLY: USA $20 Liberty 1904 (mint lustre) vs $20 Double Eagle MS63 PCGS 540k. "
    f"Est AUD{est_aud} (${est_usd:.0f}) BL=${bl_usd}. Noble 141 closes ~Apr2026. "
    f"Same type as lot 2040 (1907)->CEO_CHECK. Gold spike->CEO judge."
)

row = {
    'id': str(uuid.uuid4()),
    'marketplace': 'Noble Numismatics',
    'item_id': 'noble_141_2026_apr_2039',
    'url': 'https://www.numisbids.com/n.php?p=lot&sid=10505&lot=2039',
    'title_snapshot': 'USA, twenty dollars or double eagle, 1904, Liberty Head. Mint lustre.',
    'country': 'USA',
    'year': 1904,
    'material': 'gold',
    'comparison_type': 'TYPE_ONLY',
    'yahoo_ref_id': US20_REF,
    'yahoo_ref_title': 'US $20 Double Eagle MS63 PCGS 540k',
    'yahoo_ref_price_jpy': US20_PRICE,
    'cap_bid_limit_jpy': bl_jpy,
    'cap_bid_limit_usd': bl_usd,
    'estimated_sell_price_jpy': US20_PRICE,
    'total_cost_jpy': total_cost,
    'expected_profit_jpy': profit,
    'expected_roi_pct': roi,
    'price_snapshot_usd': round(est_usd, 1),
    'price_snapshot_jpy': price_jpy,
    'cap_judgment': 'CEO_CHECK',
    'category': 'CEO_REVIEW',
    'marketing_status': 'MARKETING_REVIEW',
    'auction_house': 'NOBLE',
    'source_group': 'WORLD',
    'review_bucket': 'GOLD_PREMIUM',
    'snapshot_score': 18,
    'scan_date': today,
    'evidence_status': 'READY',
    'cap_comment': comment,
    'bid_count_snapshot': 0,
    'submit_count': 1,
    'first_seen_at': now,
    'created_at': now,
    'updated_at': now,
}

try:
    c.table('ceo_review_log').insert(row).execute()
    print(f"OK lot 2039: USA $20 1904 Liberty BL=${bl_usd} est_aud={est_aud}")
except Exception as e:
    print(f"FAIL: {e}")

rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f"MARKETING_REVIEW: CEO_CHECK={cnt['CEO_CHECK']} CAP_BUY={cnt['CAP_BUY']}")
