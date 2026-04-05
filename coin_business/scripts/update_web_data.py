"""
coin_business/web/data.json に4カラムを追加するスクリプト

用途:
  - Supabase から ref1_buy_limit_20k_jpy 等4カラムを取得し data.json へパッチ

実行:
  cd coin_business
  python scripts/update_web_data.py
"""

import json
import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from scripts.supabase_client import get_client

DATA_JSON = ROOT / 'web' / 'data.json'

COLS = [
    'ref1_buy_limit_20k_jpy',
    'ref1_buy_limit_15pct_jpy',
    'ref2_buy_limit_20k_jpy',
    'ref2_buy_limit_15pct_jpy',
]


def fetch_4cols(mgmt_nos: list[str]) -> dict[str, dict]:
    """Supabase から4カラムを一括取得して {management_no: {...}} を返す"""
    db = get_client()
    result = {}
    batch = 500
    for i in range(0, len(mgmt_nos), batch):
        chunk = mgmt_nos[i:i + batch]
        r = (db.table('coin_slab_data')
               .select('management_no,' + ','.join(COLS))
               .in_('management_no', chunk)
               .execute())
        for row in r.data:
            result[row['management_no']] = {c: row.get(c) for c in COLS}
    return result


def main():
    print(f'読み込み: {DATA_JSON}')
    with open(DATA_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)

    coins = data['coins']
    mgmt_nos = [c['management_no'] for c in coins if c.get('management_no')]
    print(f'コイン数: {len(coins)}件 / management_no あり: {len(mgmt_nos)}件')

    print('Supabase から4カラム取得中...')
    lookup = fetch_4cols(mgmt_nos)
    print(f'取得: {len(lookup)}件')

    updated = 0
    for coin in coins:
        mn = coin.get('management_no')
        if mn and mn in lookup:
            vals = lookup[mn]
            for col in COLS:
                coin[col] = vals.get(col)
            if any(vals.get(c) is not None for c in COLS):
                updated += 1
        else:
            for col in COLS:
                coin.setdefault(col, None)

    print(f'4カラム有効データ: {updated}件')

    # atomic write
    tmp = DATA_JSON.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, DATA_JSON)
    print(f'完了: {DATA_JSON}')


if __name__ == '__main__':
    main()
