import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from supabase_client import get_client
sb = get_client()

# NG案件
r = sb.table('daily_candidates').select(
    'judgment,management_no,lot_title,current_price,expected_profit,judgment_reason'
).eq('judgment', 'NG').execute()
print('--- NG案件 ---')
for row in r.data:
    mgmt = row.get('management_no', '?')
    title = str(row.get('lot_title', ''))[:40]
    profit = row.get('expected_profit', 0) or 0
    reason = str(row.get('judgment_reason', ''))[:70]
    print(f'  [{mgmt}] profit={profit:,} / {title}')
    print(f'    reason: {reason}')

# coin_slab_data 総件数
r2 = sb.table('coin_slab_data').select('id', count='exact').execute()
print(f'\ncoin_slab_data 総件数: {r2.count}')

r3 = sb.table('coin_slab_data').select('id', count='exact').is_('ref2_buy_limit_20k_jpy', 'null').execute()
print(f'ref2_20k NULL件数: {r3.count}件（ヤフオク実績なし）')

r4 = sb.table('coin_slab_data').select('id', count='exact').is_('ref1_buy_limit_20k_jpy', 'null').execute()
print(f'ref1_20k NULL件数: {r4.count}件（基準1計算不可）')
