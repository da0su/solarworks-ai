"""
scripts/apply_migrations.py
============================
migration SQL ファイルを Supabase に適用するスクリプト。

Supabase の REST API では DDL が直接実行できないため、
このスクリプトは2つのモードで動作する:

Mode 1: exec_sql RPC （Supabase 側に exec_sql 関数が定義済みの場合）
Mode 2: 標準出力モード（SQL をコピペ用に出力）

使い方:
  # 適用対象ファイルを確認して SQL を表示する（デフォルト）
  python scripts/apply_migrations.py --dry-run

  # exec_sql RPC が使える場合は自動適用
  python scripts/apply_migrations.py --apply

  # 特定のファイルのみ
  python scripts/apply_migrations.py --file 012_yahoo_staging.sql --dry-run
"""

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

sys.path.insert(0, str(PROJECT_ROOT))

# migration 適用順（constants.py の MIGRATION_ORDER と同期すること）
MIGRATION_FILES = [
    "012_yahoo_staging.sql",
    "013_yahoo_seeds.sql",
    "014_ebay_listing_tables.sql",
    "015_global_auction_tables.sql",
    "016_match_audit_watch.sql",
    "017_notifications_negotiate.sql",
]


def load_sql(filename: str) -> str:
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Migration file not found: {path}")
    return path.read_text(encoding="utf-8")


def apply_via_rpc(client, sql: str, filename: str) -> bool:
    """exec_sql RPC 経由で SQL を実行"""
    try:
        result = client.rpc("exec_sql", {"query": sql}).execute()
        return True
    except Exception as e:
        print(f"  RPC ERROR: {e}")
        return False


def print_sql_for_copy(filename: str, sql: str):
    """Supabase SQL Editor にコピペするための出力"""
    print(f"\n{'='*60}")
    print(f"-- FILE: {filename}")
    print(f"{'='*60}")
    print(sql)
    print()


def main():
    parser = argparse.ArgumentParser(description="Apply coin_business migrations to Supabase")
    parser.add_argument("--apply", action="store_true",
                        help="exec_sql RPC 経由で自動適用（RPC 定義済みの場合）")
    parser.add_argument("--dry-run", action="store_true",
                        help="SQL をコピペ用に標準出力するのみ（デフォルト）")
    parser.add_argument("--file", type=str, default=None,
                        help="特定ファイルのみ適用（例: 012_yahoo_staging.sql）")
    args = parser.parse_args()

    # デフォルトは dry-run
    if not args.apply and not args.dry_run:
        args.dry_run = True

    # 対象ファイル
    targets = [args.file] if args.file else MIGRATION_FILES

    if args.dry_run:
        print("=" * 60)
        print("Migration Dry Run — Supabase SQL Editor にコピペしてください")
        print("適用順:")
        for f in targets:
            print(f"  → {f}")
        print("=" * 60)
        for filename in targets:
            try:
                sql = load_sql(filename)
                print_sql_for_copy(filename, sql)
            except FileNotFoundError as e:
                print(f"  SKIP: {e}")
        return 0

    if args.apply:
        from scripts.supabase_client import get_client
        client = get_client()
        success = 0
        fail = 0
        for filename in targets:
            try:
                sql = load_sql(filename)
                print(f"Applying {filename}... ", end="", flush=True)
                ok = apply_via_rpc(client, sql, filename)
                if ok:
                    print("OK")
                    success += 1
                else:
                    print("FAILED")
                    fail += 1
            except FileNotFoundError as e:
                print(f"SKIP: {e}")
                fail += 1

        print(f"\nResult: {success} OK / {fail} FAILED")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
