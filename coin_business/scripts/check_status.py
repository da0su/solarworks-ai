"""
daily_candidates / coin_slab_data の状態確認スクリプト
"""
import sys
import statistics
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from supabase_client import get_client

sb = get_client()

# ============================================================
# 1. daily_candidates 全件取得
# ============================================================
print("=" * 60)
print("  daily_candidates 状態確認")
print("=" * 60)

r = sb.table('daily_candidates').select(
    'id,judgment,buy_limit_jpy,expected_profit,profit_rate,management_no,lot_title,current_price,created_at,status'
).order('expected_profit', desc=True).limit(500).execute()
rows = r.data
print(f"総件数: {len(rows)}")

j_count = Counter(row.get('judgment') for row in rows)
print(f"Judgment分布: {dict(j_count)}")

s_count = Counter(row.get('status') for row in rows)
print(f"Status分布:   {dict(s_count)}")

zero = [row for row in rows if (row.get('current_price') or 0) == 0]
print(f"price=0件数: {len(zero)}")

ok = [row for row in rows if row.get('judgment') == 'OK']
ok_20k = [row for row in ok if (row.get('expected_profit') or 0) >= 20000]
review = [row for row in rows if row.get('judgment') == 'REVIEW']
ng = [row for row in rows if row.get('judgment') == 'NG']
ceo = [row for row in rows if row.get('judgment') == 'CEO']

print(f"\nOK: {len(ok)}  REVIEW: {len(review)}  NG: {len(ng)}  CEO: {len(ceo)}")
print(f"OK + 利益>=2万: {len(ok_20k)}")

profits = [row.get('expected_profit') or 0 for row in rows if row.get('expected_profit') is not None]
if profits:
    print(f"\n利益統計:")
    print(f"  平均  : {int(statistics.mean(profits)):,}円")
    print(f"  中央値: {int(statistics.median(profits)):,}円")
    print(f"  最大  : {max(profits):,}円")
    print(f"  最小  : {min(profits):,}円")

print("\n--- TOP5 OK案件（利益順） ---")
for row in ok_20k[:5]:
    mgmt = row.get('management_no', '?')
    title = str(row.get('lot_title', ''))[:40]
    price = row.get('current_price', 0) or 0
    limit = row.get('buy_limit_jpy', 0) or 0
    profit = row.get('expected_profit', 0) or 0
    rate = row.get('profit_rate', 0) or 0
    print(f"  [{mgmt}] {title}")
    print(f"    eBay現値={price:,} / 仕入上限={limit:,} / 利益={profit:,} / 利益率={rate:.1f}%")

# ============================================================
# 2. coin_slab_data 4カラム充填率
# ============================================================
print("\n" + "=" * 60)
print("  coin_slab_data 4カラム充填率")
print("=" * 60)

r2 = sb.table('coin_slab_data').select(
    'management_no,ref1_buy_limit_20k_jpy,ref1_buy_limit_15pct_jpy,ref2_buy_limit_20k_jpy,ref2_buy_limit_15pct_jpy'
).limit(1000).execute()
slabs = r2.data
total_slabs = len(slabs)
print(f"coin_slab_data取得: {total_slabs}件")

for col in ['ref1_buy_limit_20k_jpy', 'ref1_buy_limit_15pct_jpy', 'ref2_buy_limit_20k_jpy', 'ref2_buy_limit_15pct_jpy']:
    filled = sum(1 for s in slabs if s.get(col) is not None)
    pct = filled / total_slabs * 100 if total_slabs > 0 else 0
    print(f"  {col}: {filled}/{total_slabs} ({pct:.1f}%)")

# 異常値チェック（極端に高い仕入上限）
r1_vals = [s['ref1_buy_limit_20k_jpy'] for s in slabs if s.get('ref1_buy_limit_20k_jpy') is not None]
if r1_vals:
    print(f"\n基準1(2万)仕入上限: 最大={max(r1_vals):,} / 中央値={int(statistics.median(r1_vals)):,}")
    extreme = [s for s in slabs if (s.get('ref1_buy_limit_20k_jpy') or 0) > 500000]
    print(f"極端値(>50万): {len(extreme)}件")

print("\n完了")
