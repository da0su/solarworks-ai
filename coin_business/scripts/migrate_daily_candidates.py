"""
daily_candidates テーブルへ 4カラム追加マイグレーション
- ref1_buy_limit_20k_jpy
- ref1_buy_limit_15pct_jpy
- ref2_buy_limit_20k_jpy
- ref2_buy_limit_15pct_jpy

実行方法:
    python scripts/migrate_daily_candidates.py          # 本番実行
    python scripts/migrate_daily_candidates.py --check  # カラム存在確認のみ
"""

import sys
import os
import logging

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def check_columns() -> dict:
    """daily_candidates のカラム一覧を取得して4カラムの存在を確認"""
    from supabase_client import get_client
    sb = get_client()
    r = sb.table('daily_candidates').select('*').limit(1).execute()
    if not r.data:
        return {}
    cols = set(r.data[0].keys())
    target = ['ref1_buy_limit_20k_jpy', 'ref1_buy_limit_15pct_jpy',
              'ref2_buy_limit_20k_jpy', 'ref2_buy_limit_15pct_jpy']
    return {c: (c in cols) for c in target}


def run_ddl_via_rpc(sql: str) -> bool:
    """Supabase の exec_sql RPC 経由でDDL実行を試みる"""
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
    }
    resp = httpx.post(url, headers=headers, json={'sql': sql}, timeout=30)
    logger.info(f"RPC exec_sql: status={resp.status_code}")
    return resp.status_code == 200


def run_ddl_via_management_api(sql: str) -> bool:
    """Supabase Management API 経由でDDL実行"""
    # project ref = URL のサブドメイン
    project_ref = SUPABASE_URL.replace('https://', '').split('.')[0]
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
    }
    resp = httpx.post(url, headers=headers, json={'query': sql}, timeout=30)
    logger.info(f"Management API: status={resp.status_code} body={resp.text[:200]}")
    return resp.status_code == 200


ALTER_SQL = """
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS ref1_buy_limit_20k_jpy   integer,
    ADD COLUMN IF NOT EXISTS ref1_buy_limit_15pct_jpy  integer,
    ADD COLUMN IF NOT EXISTS ref2_buy_limit_20k_jpy   integer,
    ADD COLUMN IF NOT EXISTS ref2_buy_limit_15pct_jpy  integer;
"""

COMMENT_SQL = """
COMMENT ON COLUMN daily_candidates.ref1_buy_limit_20k_jpy   IS '基準1 利益2万円ベースeBay仕入上限(円)';
COMMENT ON COLUMN daily_candidates.ref1_buy_limit_15pct_jpy IS '基準1 粗利15%ベースeBay仕入上限(円)';
COMMENT ON COLUMN daily_candidates.ref2_buy_limit_20k_jpy   IS '基準2 利益2万円ベースeBay仕入上限(円)';
COMMENT ON COLUMN daily_candidates.ref2_buy_limit_15pct_jpy IS '基準2 粗利15%ベースeBay仕入上限(円)';
"""


def main():
    check_only = '--check' in sys.argv

    logger.info("=== daily_candidates 4カラム追加マイグレーション ===")

    # 現状確認
    status = check_columns()
    logger.info("現在のカラム存在状況:")
    all_exist = True
    for col, exists in status.items():
        mark = '✓' if exists else '✗'
        logger.info(f"  [{mark}] {col}")
        if not exists:
            all_exist = False

    if all_exist:
        logger.info("→ 4カラム全て既存。マイグレーション不要。")
        return

    if check_only:
        logger.info("→ --check モード: DDL実行しません")
        return

    logger.info("\nDDL実行中...")

    # RPC経由を試みる
    ok = run_ddl_via_rpc(ALTER_SQL)
    if not ok:
        logger.info("RPC exec_sql 不可。Management API を試行...")
        ok = run_ddl_via_management_api(ALTER_SQL)

    if ok:
        logger.info("✓ ALTER TABLE 成功")
        run_ddl_via_rpc(COMMENT_SQL) or run_ddl_via_management_api(COMMENT_SQL)
        # 再確認
        status2 = check_columns()
        logger.info("マイグレーション後のカラム:")
        for col, exists in status2.items():
            mark = '✓' if exists else '✗'
            logger.info(f"  [{mark}] {col}")
    else:
        logger.warning("自動DDL実行不可。以下のSQLをSupabase SQL Editorで手動実行してください:")
        print("\n" + "="*60)
        print(ALTER_SQL)
        print("="*60 + "\n")


if __name__ == '__main__':
    main()
