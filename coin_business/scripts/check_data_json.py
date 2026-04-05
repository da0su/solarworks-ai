"""data.json の内容確認スクリプト"""
import sys, json, os
sys.stdout.reconfigure(encoding='utf-8')

data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data.json')

with open(data_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

coins = data.get('coins', [])
print(f"総件数: {len(coins)}")
print(f"更新日時: {data.get('updated_at', '?')}")

# 001001 確認
target = next((c for c in coins if c.get('management_no') == '001001'), None)
if target:
    print("\n--- 001001 ---")
    for col in ['price_jpy', 'ref1_buy_limit_20k_jpy', 'ref1_buy_limit_15pct_jpy',
                'ref2_buy_limit_20k_jpy', 'ref2_buy_limit_15pct_jpy']:
        print(f"  {col}: {target.get(col)}")
else:
    print("001001 なし")

# price=0 件数
zero = [c for c in coins if not c.get('price_jpy')]
print(f"\nprice=0 or null: {len(zero)}件")

# 4カラム NULL件数
for col in ['ref1_buy_limit_20k_jpy', 'ref1_buy_limit_15pct_jpy',
            'ref2_buy_limit_20k_jpy', 'ref2_buy_limit_15pct_jpy']:
    null_cnt = sum(1 for c in coins if c.get(col) is None)
    print(f"{col} NULL: {null_cnt}件 ({null_cnt/len(coins)*100:.1f}%)")
