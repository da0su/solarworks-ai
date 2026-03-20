"""コイン事業 - CLIエントリーポイント

使用方法:
    python run.py setup              # Supabaseテーブル確認＋SQL表示
    python run.py setup --dump       # 全SQL出力（コピペ用）
    python run.py setup --status     # テーブル状態確認
    python run.py import-yahoo <excel_path> [--dry-run]  # ヤフオクExcel投入
    python run.py search [--country XX] [--grade XX] [--grader XX] [--year XXXX] [--limit N]
    python run.py stats [--clean] [--country XX] [--grader NGC|PCGS]  # 簡易集計レポート
    python run.py count              # 全テーブル件数表示
    python run.py collect            # 価格データ取得
    python run.py analyze            # 分析実行
    python run.py report             # 日次レポート生成
    python run.py all                # 取得→分析→レポートを一括実行
"""

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
import config

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            config.LOG_DIR / f"coin_research_{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8",
        ),
    ],
)

logger = logging.getLogger("coin_business")


# ============================================================
# Supabase コマンド
# ============================================================

def cmd_setup():
    """Supabaseテーブル確認・SQL表示"""
    from scripts.setup_supabase import main as setup_main
    # 引数をそのまま渡す
    setup_main()


def cmd_import_yahoo():
    """ヤフオクExcelデータをSupabaseへ投入"""
    from scripts.import_yahoo_history import main as import_main
    import_main()


def cmd_search():
    """market_transactions検索"""
    from scripts.supabase_client import get_client

    # 引数パース
    args = sys.argv[2:]
    filters = {}
    limit = 20
    i = 0
    while i < len(args):
        if args[i] == "--country" and i + 1 < len(args):
            filters["country"] = args[i + 1]; i += 2
        elif args[i] == "--grade" and i + 1 < len(args):
            filters["grade"] = args[i + 1]; i += 2
        elif args[i] == "--grader" and i + 1 < len(args):
            filters["grader"] = args[i + 1]; i += 2
        elif args[i] == "--year" and i + 1 < len(args):
            filters["year"] = int(args[i + 1]); i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            filters["source"] = args[i + 1]; i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        else:
            i += 1

    client = get_client()
    q = client.table("market_transactions").select("title, price_jpy, sold_date, source, country, year, grader, grade, url")
    for col, val in filters.items():
        q = q.eq(col, val)
    q = q.order("sold_date", desc=True).limit(limit)

    results = q.execute().data
    if not results:
        print("該当データなし")
        return

    print(f"検索結果: {len(results)}件 (上限{limit})")
    print("-" * 80)
    for r in results:
        price = f"¥{r.get('price_jpy', 0):,}" if r.get('price_jpy') else "N/A"
        print(f"  {r.get('sold_date', 'N/A')} | {price:>12} | {r.get('grader', ''):<5} {r.get('grade', ''):<15} | {r.get('title', '')[:50]}")
    print("-" * 80)


def cmd_stats():
    """market_transactions簡易集計レポート"""
    from scripts.market_stats import main as stats_main
    stats_main()


def cmd_update_yahoo():
    """ヤフオク closedsearch差分更新"""
    from scripts.fetch_yahoo_closedsearch import main as fetch_main
    fetch_main()


def cmd_update_ebay():
    """eBay Completed Listings差分更新"""
    from scripts.fetch_ebay_sold import main as fetch_main
    fetch_main()


def cmd_ebay_watch():
    """eBay仕入れ監視（リアルタイム/オークション/交渉）"""
    from scripts.ebay_monitor import main as monitor_main
    monitor_main()


def cmd_count():
    """全テーブル件数表示"""
    from scripts.supabase_client import get_client
    client = get_client()
    tables = [
        "coin_master", "market_transactions", "sellers", "cost_rules",
        "sourcing_records", "listing_records", "profit_analysis",
        "daily_candidates", "inventory", "exchange_rates",
    ]
    print("テーブル件数:")
    total = 0
    for table in tables:
        try:
            resp = client.table(table).select("id", count="exact").limit(0).execute()
            c = resp.count or 0
            total += c
            print(f"  {table:<25} {c:>8,}件")
        except Exception:
            print(f"  {table:<25} (未作成)")
    print(f"  {'合計':<25} {total:>8,}件")


# ============================================================
# 既存コマンド（仮想通貨リサーチ）
# ============================================================

def cmd_collect():
    from scripts.collectors.price_collector import collect
    collect()


def cmd_analyze():
    from scripts.analyzers.trend_analyzer import analyze
    result = analyze()
    if result:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_report():
    from scripts.analyzers.report_generator import generate_daily_report
    filepath = generate_daily_report()
    if filepath:
        print(f"レポート生成完了: {filepath}")


def cmd_all():
    logger.info("=== 一括実行開始 ===")
    cmd_collect()
    cmd_report()
    logger.info("=== 一括実行完了 ===")


# ============================================================
# コマンドルーティング
# ============================================================

COMMANDS = {
    "setup": cmd_setup,
    "import-yahoo": cmd_import_yahoo,
    "update-yahoo": cmd_update_yahoo,
    "update-ebay": cmd_update_ebay,
    "ebay-watch": cmd_ebay_watch,
    "search": cmd_search,
    "stats": cmd_stats,
    "count": cmd_count,
    "collect": cmd_collect,
    "analyze": cmd_analyze,
    "report": cmd_report,
    "all": cmd_all,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print(f"利用可能なコマンド: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    command = sys.argv[1]
    logger.info(f"コマンド実行: {command}")
    COMMANDS[command]()


if __name__ == "__main__":
    main()
