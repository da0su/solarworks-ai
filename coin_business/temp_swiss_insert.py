import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from datetime import datetime, timezone, date
import uuid
c = get_client()
now = datetime.now(timezone.utc).isoformat()
today = date.today().isoformat()

SWISS_REF = 'ff00b7cb-70b7-45b1-907c-2b18ec57f686'
SWISS_PRICE = 198000

def calc_bl(sell_jpy):
    rev = sell_jpy * 0.9
    cost = min(rev - 20000, rev * 0.85)
    bl_jpy = (cost - 2750) / 1.10
    bl_usd = bl_jpy / 150
    total_cost = cost + 2750
    profit = rev - total_cost
    roi = profit / total_cost * 100
    return int(bl_jpy), round(bl_usd, 1), int(total_cost), int(profit), round(roi, 1)

# Swiss 20 franc lots - Noble sale 10505
# 2-coin lots: sell = 2 x 198000 = 396000
# 1-coin lot: sell = 198000
swiss_lots = [
    {
        'item_id': 'noble_10505_2021',
        'lot': 2021,
        'title': 'Switzerland, Confederation, twenty francs, 1890B, 1892B. Good extremely fine (2)',
        'country': 'Switzerland',
        'year': 1890,
        'est_aud': 2750,
        'n_coins': 2,
        'sell_jpy': 396000,
        'score': 16,
    },
    {
        'item_id': 'noble_10505_2022',
        'lot': 2022,
        'title': 'Switzerland, Confederation, twenty francs, 1893B, 1896B. About uncirculated (2)',
        'country': 'Switzerland',
        'year': 1893,
        'est_aud': 2750,
        'n_coins': 2,
        'sell_jpy': 396000,
        'score': 16,
    },
    {
        'item_id': 'noble_10505_2023',
        'lot': 2023,
        'title': 'Switzerland, Confederation, twenty francs, 1898B. Good extremely fine',
        'country': 'Switzerland',
        'year': 1898,
        'est_aud': 1400,
        'n_coins': 1,
        'sell_jpy': 198000,
        'score': 14,
    },
]

ok = 0
for lot in swiss_lots:
    bl_jpy, bl_usd, total_cost, profit, roi = calc_bl(lot['sell_jpy'])
    est_usd = lot['est_aud'] * 0.62
    price_jpy = int(lot['est_aud'] * 95)

    comment = (
        f"[CAP 2026-04-04] TYPE_ONLY: Swiss 20F lot {lot['lot']} ({lot['n_coins']}coins) "
        f"vs Swiss 1916B MS66 198k. Est AUD{lot['est_aud']} (${est_usd:.0f}) "
        f"BL=${bl_usd}. Noble 10505 closes ~Apr2026->CEO_CHECK. Gold spike->CEO judge."
    )

    row = {
        'id': str(uuid.uuid4()),
        'marketplace': 'Noble Numismatics',
        'item_id': lot['item_id'],
        'url': f"https://www.numisbids.com/n.php?p=lot&sid=10505&lot={lot['lot']}",
        'title_snapshot': lot['title'],
        'country': lot['country'],
        'year': lot['year'],
        'material': 'gold',
        'comparison_type': 'TYPE_ONLY',
        'yahoo_ref_id': SWISS_REF,
        'yahoo_ref_title': 'Swiss 20F 1916B NGC MS66 198k',
        'yahoo_ref_price_jpy': SWISS_PRICE,
        'cap_bid_limit_jpy': bl_jpy,
        'cap_bid_limit_usd': bl_usd,
        'estimated_sell_price_jpy': lot['sell_jpy'],
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
        'snapshot_score': lot['score'],
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
        print(f"OK lot {lot['lot']}: {lot['n_coins']}coins BL=${bl_usd} est_aud={lot['est_aud']}")
        ok += 1
    except Exception as e:
        print(f"FAIL lot {lot['lot']}: {e}")

print(f"\nInserted: {ok}/{len(swiss_lots)}")

rows = c.table('ceo_review_log').select('cap_judgment').eq('marketing_status', 'MARKETING_REVIEW').execute().data
from collections import Counter
cnt = Counter(r.get('cap_judgment') for r in rows)
print(f"MARKETING_REVIEW: CEO_CHECK={cnt['CEO_CHECK']} CAP_BUY={cnt['CAP_BUY']}")
