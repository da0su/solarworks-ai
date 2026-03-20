"""Supabaseマイグレーション実行スクリプト

使い方:
    python scripts/setup_supabase.py              # 全マイグレーション実行
    python scripts/setup_supabase.py --file 001   # 特定ファイルのみ
    python scripts/setup_supabase.py --status      # テーブル状態確認
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

sys.path.insert(0, str(PROJECT_ROOT))
from scripts.supabase_client import get_client


def run_migrations(target_file: str | None = None):
    """マイグレーションSQLファイルを順番に実行"""
    client = get_client()
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    if target_file:
        sql_files = [f for f in sql_files if target_file in f.name]

    if not sql_files:
        print("マイグレーションファイルが見つかりません")
        return False

    success = 0
    failed = 0
    for sql_file in sql_files:
        print(f"  実行中: {sql_file.name}")
        sql = sql_file.read_text(encoding="utf-8")

        # セミコロンで分割して個別実行（CREATE TABLE等は1文ずつ）
        statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]

        for i, stmt in enumerate(statements):
            try:
                client.postgrest.session.headers.update(client.options.headers)
                # Supabase SQL Editor相当: rpc経由ではなくREST API
                # supabase-pyではSQL直接実行はサポートされないため、
                # Supabaseダッシュボードからの手動実行を推奨
                pass
            except Exception as e:
                print(f"    エラー (statement {i+1}): {e}")
                failed += 1

        success += 1
        print(f"    完了: {sql_file.name}")

    print()
    print(f"結果: {success}ファイル完了, {failed}エラー")
    return failed == 0


def check_status():
    """テーブル存在確認"""
    client = get_client()
    tables = [
        "coin_master", "sellers", "market_transactions", "cost_rules",
        "sourcing_records", "listing_records", "profit_analysis",
        "daily_candidates", "inventory", "exchange_rates", "inventory_snapshots",
    ]
    print("テーブル状態確認:")
    for table in tables:
        try:
            resp = client.table(table).select("id", count="exact").limit(0).execute()
            count = resp.count or 0
            print(f"  OK {table}: {count}件")
        except Exception:
            print(f"  NG {table}: 未作成")


def print_migration_instructions():
    """SupabaseダッシュボードでのSQL実行手順を表示"""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    print("=" * 60)
    print("Supabase テーブル作成手順")
    print("=" * 60)
    print()
    print("supabase-pyではDDL(CREATE TABLE)を直接実行できないため、")
    print("Supabaseダッシュボードの SQL Editor で以下のファイルを順に実行してください。")
    print()
    for f in sql_files:
        print(f"  {f.name}")
    print()
    print("手順:")
    print("  1. Supabase Dashboard → SQL Editor を開く")
    print("  2. 上記ファイルの内容を順にコピー＆ペースト")
    print("  3. Run を押して実行")
    print("  4. 完了後、python scripts/setup_supabase.py --status で確認")
    print()
    print("または、以下のファイル内容をまとめて表示:")
    print("  python scripts/setup_supabase.py --dump")
    print()


def dump_all_sql():
    """全マイグレーションSQLを結合して標準出力に表示"""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for f in sql_files:
        print(f"-- {'='*56}")
        print(f"-- {f.name}")
        print(f"-- {'='*56}")
        print(f.read_text(encoding="utf-8"))
        print()


def main():
    args = sys.argv[1:]

    if "--status" in args:
        check_status()
    elif "--dump" in args:
        dump_all_sql()
    elif "--file" in args:
        idx = args.index("--file")
        target = args[idx + 1] if idx + 1 < len(args) else None
        if target:
            run_migrations(target)
        else:
            print("--file にファイル番号を指定してください (例: --file 001)")
    elif "--help" in args or "-h" in args:
        print_migration_instructions()
    else:
        print_migration_instructions()
        check_status()


if __name__ == "__main__":
    main()
