"""コイン事業 - CLIエントリーポイント

使用方法:
    python run.py setup              # Supabaseテーブル確認＋SQL表示
    python run.py setup --dump       # 全SQL出力（コピペ用）
    python run.py setup --status     # テーブル状態確認
    python run.py import-yahoo <excel_path> [--dry-run]  # ヤフオクExcel投入
    python run.py search [--country XX] [--grade XX] [--grader XX] [--year XXXX] [--limit N]
    python run.py stats [--clean] [--country XX] [--grader NGC|PCGS]  # 簡易集計レポート
    python run.py explore            # eBay vs Yahoo 価格差自動探索
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


def cmd_explore():
    """eBay vs Yahoo 価格差自動探索"""
    from scripts.auto_explorer import main as explore_main
    explore_main()


def cmd_ebay_search():
    """eBay仕入候補をAPIで探索し judge_opportunity で判定。
    使い方:
        python run.py ebay-search
    """
    from scripts.ebay_auction_search import main as ebay_search_main
    ebay_search_main()


def cmd_overseas_fetch():
    """海外オークション落札データ取得（Heritage / Stack's Bowers 等）。
    使い方:
        python run.py overseas-fetch                    # 全ソース
        python run.py overseas-fetch --source heritage  # Heritage のみ
        python run.py overseas-fetch --coin 001001      # 特定管理番号
        python run.py overseas-fetch --dry-run          # テスト実行
    """
    from scripts.fetch_overseas_sold import main as overseas_main
    overseas_main()


def cmd_calc_ref():
    """仕入上限(ref1/ref2)を全件再計算。NULLレコードも補完。
    使い方:
        python run.py calc-ref           # 全件
        python run.py calc-ref --null    # ref1_buy_limit_jpy=NULLのみ
    """
    from scripts.calc_ref_values import main as calc_main
    calc_main()


def cmd_ebay_integrate():
    """eBay候補 (ebay_review_candidates.json) を daily_candidates へ統合。
    使い方:
        python run.py ebay-integrate           # 全候補を統合
        python run.py ebay-integrate --dry-run  # 確認のみ
        python run.py ebay-integrate --show     # 候補一覧表示
        python run.py ebay-integrate --approved-only  # 承認済みのみ
    """
    from scripts.ebay_lot_integrator import integrate_ebay_candidates, load_ebay_candidates
    import argparse

    parser = argparse.ArgumentParser(prog="run.py ebay-integrate", add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fx",      type=float, default=150.0)
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--show",    action="store_true")
    args, _ = parser.parse_known_args(sys.argv[2:])

    if args.show:
        candidates = load_ebay_candidates()
        print(f"\n=== eBay候補一覧 ({len(candidates)}件) ===\n")
        for c in candidates:
            mgmt  = c.get("mgmt_no", "?")
            title = (c.get("ebay_title") or "")[:55]
            price = c.get("api_price_usd")
            limit = c.get("ebay_limit_jpy", 0) or 0
            appr  = c.get("approved")
            appr_str = "APPROVED" if appr else ("PENDING" if appr is None else "REJECTED")
            price_str = f"USD {price:,.0f}" if price else "BIN/N.A."
            print(f"  [{mgmt}] {appr_str:8} | {price_str:>10} | limit=JPY{limit:,.0f} | {title}")
        return

    result = integrate_ebay_candidates(
        fx_rate=args.fx,
        dry_run=args.dry_run,
        only_approved=args.approved_only,
    )
    print(f"\n=== eBay統合結果 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")


def cmd_overseas_watch():
    """
    全世界オークション常時監視 (Layer 1〜4 統合実行)

    auction_schedule.json を参照し、開催中・直前オークションのロットを取得、
    judgment → daily_candidates 書き込み → Slack通知 を一括実行。

    使い方:
        python run.py overseas-watch                     # 全ソース監視
        python run.py overseas-watch --source heritage   # Heritage のみ
        python run.py overseas-watch --dry-run           # テスト実行（DB/Slack なし）
        python run.py overseas-watch --schedule          # 監視スケジュール確認
        python run.py overseas-watch --candidates        # daily_candidates 現状確認
        python run.py overseas-watch --ceo-list          # CEO判断リスト表示
    """
    import sys
    import argparse
    import logging

    parser = argparse.ArgumentParser(prog="run.py overseas-watch", add_help=False)
    parser.add_argument("--source",     default=None,  help="監視ソース (heritage/numisbids/noble/noonans/spink/sincona/all)")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--schedule",   action="store_true", help="監視スケジュール表示のみ")
    parser.add_argument("--candidates", action="store_true", help="daily_candidates 現状確認")
    parser.add_argument("--ceo-list",   action="store_true", help="CEO判断リスト表示")
    parser.add_argument("--detail",     default=None,  help="管理番号で詳細表示")
    parser.add_argument("--pages",      type=int, default=5, help="1オークション最大ページ数")

    # run.py コマンドの後続引数をパース
    args, _ = parser.parse_known_args(sys.argv[2:])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("overseas_watch")

    # ── --schedule: スケジュール確認のみ
    if args.schedule:
        from scripts.auction_status_checker import print_schedule_summary, get_active_auctions
        print_schedule_summary()
        actives = get_active_auctions()
        print(f"今すぐ監視すべき: {len(actives)}件")
        return

    # ── --candidates: daily_candidates 現状確認
    if args.candidates:
        from scripts.candidates_writer import print_candidate_summary
        print_candidate_summary()
        return

    # ── --ceo-list: CEO判断リスト
    if args.ceo_list:
        from scripts.candidates_writer import get_ceo_list
        ceo = get_ceo_list()
        if not ceo:
            print("CEO判断待ち案件: なし")
            return
        print(f"\n=== CEO判断リスト ({len(ceo)}件) ===\n")
        for i, c in enumerate(ceo, 1):
            print(f"{i:2}. {c.get('lot_title', '')[:55]}")
            print(f"     仕入推定: ¥{c.get('estimated_cost_jpy') or 0:,.0f}  "
                  f"上限: ¥{c.get('buy_limit_jpy') or 0:,.0f}")
            print(f"     理由: {c.get('judgment_reason', '')[:60]}")
            print(f"     {c.get('auction_house', '')} | {c.get('lot_url', '')[:60]}")
            print()
        return

    # ── メイン監視実行
    from scripts.auction_status_checker import get_active_auctions
    actives = get_active_auctions()

    if not actives:
        logger.info("現在監視対象のオークションなし (active/imminent なし)")
        return

    logger.info(f"監視対象: {len(actives)}件")
    for a in actives:
        logger.info(f"  {a.get('name', '')[:50]} [{a.get('_status')}] "
                    f"→ {a.get('_interval_min')}分ごと")

    all_lots = []

    # ── Heritage
    source = (args.source or "all").lower()
    if source in ("all", "heritage"):
        try:
            from scripts.heritage_fetcher import fetch_heritage_lots
            heritage_lots = fetch_heritage_lots(dry_run=args.dry_run, max_pages=args.pages)
            all_lots.extend(heritage_lots)
            logger.info(f"  [Heritage] {len(heritage_lots)}件取得")
        except Exception as e:
            logger.warning(f"  [Heritage] 取得エラー: {e}")

    # ── NumisBids (Noble / Noonans / Spink / SINCONA)
    if source in ("all", "numisbids", "noble", "noonans", "spink", "sincona"):
        try:
            from scripts.numisbids_fetcher import fetch_numisbids_lots, NUMISBIDS_SOURCES
            nb_sources = None  # all
            if source not in ("all", "numisbids"):
                nb_sources = [source]
            numisbids_lots = fetch_numisbids_lots(
                sources=nb_sources,
                dry_run=args.dry_run,
                max_pages=args.pages,
            )
            all_lots.extend(numisbids_lots)
            logger.info(f"  [NumisBids] {len(numisbids_lots)}件取得")
        except Exception as e:
            logger.warning(f"  [NumisBids] 取得エラー: {e}")

    if not all_lots:
        logger.info("取得ロット: 0件 — 終了")
        return

    logger.info(f"合計取得: {len(all_lots)}件 → daily_candidates へ書き込み")

    # ── candidates_writer で判定・DB書き込み・Slack通知
    from scripts.candidates_writer import write_candidates
    result = write_candidates(all_lots, dry_run=args.dry_run)

    logger.info(
        f"\n=== overseas-watch 完了 ===\n"
        f"  取得: {len(all_lots)}件\n"
        f"  OK:     {result.get('ok', 0)}件\n"
        f"  REVIEW: {result.get('review', 0)}件\n"
        f"  CEO判断: {result.get('ceo', 0)}件\n"
        f"  NG:     {result.get('ng', 0)}件\n"
        f"  エラー: {result.get('error', 0)}件"
    )


def cmd_yahoo_sync():
    """Yahoo!落札データを yahoo_sold_lots_staging に同期する (Day 2)。
    使い方:
        python run.py yahoo-sync                        # 通常実行
        python run.py yahoo-sync --dry-run              # 書かずに変換結果のみ表示
        python run.py yahoo-sync --new-only             # 未登録のみ upsert
        python run.py yahoo-sync --since 2024-01-01     # 指定日以降のみ
        python run.py yahoo-sync --limit 100            # 件数上限 (テスト用)
    """
    from scripts.yahoo_sold_sync import main as sync_main
    sync_main()


def cmd_yahoo_promote():
    """APPROVED_TO_MAIN の staging レコードを yahoo_sold_lots に昇格する (Day 4)。
    使い方:
        python run.py yahoo-promote                     # 通常実行
        python run.py yahoo-promote --dry-run           # 書かずに確認のみ
        python run.py yahoo-promote --limit 200         # 件数上限
        python run.py yahoo-promote --status-only       # 昇格可能件数を確認して終了
    """
    from scripts.yahoo_promoter import main as promote_main
    promote_main()


def cmd_seed_generate():
    """yahoo_sold_lots から探索用 seed を生成し yahoo_coin_seeds に保存する (Day 4)。
    使い方:
        python run.py seed-generate                     # 通常実行
        python run.py seed-generate --dry-run           # 書かずに確認のみ
        python run.py seed-generate --new-only          # seed がない lot のみ処理
        python run.py seed-generate --since 2024-01-01  # 指定日以降の lot のみ
        python run.py seed-generate --limit 500         # 件数上限
        python run.py seed-generate --status-only       # yahoo_sold_lots 件数確認
    """
    from scripts.seed_generator import main as seed_main
    seed_main()


def cmd_ebay_scan():
    """Yahoo seed 起点の eBay seed スキャナー (Day 6)。
    使い方:
        python run.py ebay-scan                         # 通常実行
        python run.py ebay-scan --dry-run               # 書かずに確認のみ
        python run.py ebay-scan --smoke                 # 1 seed だけ動作確認
        python run.py ebay-scan --limit 100             # 処理 seed 数上限
        python run.py ebay-scan --seed-limit 50         # 1 seed あたり取得件数
        python run.py ebay-scan --seed-types CERT_EXACT,CERT_TITLE
        python run.py ebay-scan --status-only           # READY seed 件数確認
    """
    from scripts.ebay_seed_scanner import main as scanner_main
    scanner_main()


def cmd_ebay_ingest():
    """eBay Browse API から listing を取得し DB に保存する (Day 5)。
    使い方:
        python run.py ebay-ingest                       # 通常実行
        python run.py ebay-ingest --dry-run             # 書かずに取得確認
        python run.py ebay-ingest --smoke               # 1 seed だけ動作確認
        python run.py ebay-ingest --limit 100           # 処理 seed 数上限
        python run.py ebay-ingest --seed-limit 50       # 1 seed あたり取得件数
        python run.py ebay-ingest --seed-types CERT_EXACT,CERT_TITLE
        python run.py ebay-ingest --status-only         # READY seed 件数確認
    """
    from scripts.ebay_api_ingest import main as ingest_main
    ingest_main()


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
    "explore": cmd_explore,
    "search": cmd_search,
    "stats": cmd_stats,
    "count": cmd_count,
    "ebay-search":     cmd_ebay_search,    # eBay仕入候補探索・判定
    "ebay-integrate":  cmd_ebay_integrate, # eBay候補を daily_candidates へ統合
    "overseas-fetch":  cmd_overseas_fetch,  # 海外オークション落札済みデータ取得
    "overseas-watch":  cmd_overseas_watch,  # 全世界オークション常時監視 (Layer 1-4)
    "yahoo-sync":      cmd_yahoo_sync,      # Yahoo staging 同期 (Day 2)
    "yahoo-promote":   cmd_yahoo_promote,   # APPROVED_TO_MAIN → yahoo_sold_lots 昇格 (Day 4)
    "seed-generate":   cmd_seed_generate,   # yahoo_sold_lots → yahoo_coin_seeds (Day 4)
    "ebay-scan":       cmd_ebay_scan,       # Yahoo seed 起点 eBay スキャン (Day 6)
    "ebay-ingest":     cmd_ebay_ingest,     # eBay API listing 取得・保存 (Day 5)
    "calc-ref":        cmd_calc_ref,        # 仕入上限再計算
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
