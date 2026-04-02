"""
coin_business/scripts/yahoo_sold_sync.py
=========================================
Yahoo!落札データを market_transactions から読み取り、
正規化・パースして yahoo_sold_lots_staging に upsert するスクリプト。

【絶対ルール】
  - 書き込み先は yahoo_sold_lots_staging のみ。
  - 本DB yahoo_sold_lots には絶対に書かない。
  - 初期ステータスは必ず PENDING_CEO。
  - yahoo_lot_id を重複キーとして冪等に動作する。

使い方:
  cd coin_business

  # dry-run（DB には書かず、取得・変換結果のみ表示）
  python scripts/yahoo_sold_sync.py --dry-run

  # 通常実行（upsert する）
  python scripts/yahoo_sold_sync.py

  # 件数上限指定（テスト）
  python scripts/yahoo_sold_sync.py --limit 50

  # 新規のみ（既に staging に存在する yahoo_lot_id をスキップ）
  python scripts/yahoo_sold_sync.py --new-only

  # 指定日以降の落札データのみ
  python scripts/yahoo_sold_sync.py --since 2024-01-01

終了コード:
  0 = 成功（WARN ありでも 0）
  1 = エラーあり

daily ジョブ登録:
  このスクリプトは register_jobs.py / daily cron から
  毎日 09:00 JST に呼び出すことを想定している。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# プロジェクトルート解決
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from constants import Table, YahooStagingStatus, Source
from yahoo.normalizer import normalize_lot_record
from yahoo.parser import parse_lot_title
from db.yahoo_repo import (
    UpsertResult,
    upsert_staging_records,
    get_already_synced_ids,
    count_by_status,
    record_job_run,
)

# ================================================================
# ロギング設定
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("yahoo_sold_sync")

# ================================================================
# 定数
# ================================================================

FETCH_BATCH_SIZE = 1000   # market_transactions 取得バッチ
REPORT_INTERVAL  = 500    # 進捗ログを出すレコード数間隔


# ================================================================
# フェッチ
# ================================================================

def fetch_yahoo_market_transactions(
    client,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    market_transactions から Yahoo! ソースのレコードを全件取得する。

    Args:
        since: "YYYY-MM-DD" 以降の sold_date のみ取得 (None = 全件)
        limit: 最大取得件数 (None = 上限なし)

    Returns:
        list of dict (各 row に id/title/price_jpy/sold_date/url/item_id 等を含む)
    """
    all_rows: list[dict] = []
    last_id  = 0
    fetched  = 0

    while True:
        q = (
            client.table(Table.MARKET_TRANSACTIONS)
            .select(
                "id, title, price_jpy, sold_date, url, item_id, "
                "thumbnail_url, grader, grade, year, denomination, country"
            )
            .eq("source", Source.YAHOO)
            .gt("id", last_id)
            .order("id")
            .limit(FETCH_BATCH_SIZE)
        )
        if since:
            q = q.gte("sold_date", since)

        try:
            resp = q.execute()
        except Exception as exc:
            logger.error("market_transactions 取得失敗 (last_id=%d): %s", last_id, exc)
            break

        rows = resp.data or []
        if not rows:
            break

        all_rows.extend(rows)
        fetched += len(rows)
        last_id = rows[-1]["id"]

        if fetched % REPORT_INTERVAL == 0:
            logger.info("  取得中... %d 件", fetched)

        if limit and fetched >= limit:
            all_rows = all_rows[:limit]
            break

    logger.info("market_transactions 取得完了: %d 件", len(all_rows))
    return all_rows


# ================================================================
# 変換
# ================================================================

class SyncStats:
    """同期処理の統計情報"""
    total_fetched:    int = 0
    total_normalized: int = 0
    total_skipped:    int = 0   # yahoo_lot_id が取得できなかった件数
    parse_failures:   list[dict] = None

    def __init__(self):
        self.total_fetched    = 0
        self.total_normalized = 0
        self.total_skipped    = 0
        self.parse_failures   = []


def convert_to_staging_records(
    rows: list[dict],
    skip_existing_ids: set[str] | None = None,
) -> tuple[list[dict], "SyncStats"]:
    """
    market_transactions の rows を staging レコードに変換する。

    Args:
        rows:               fetch_yahoo_market_transactions の戻り値
        skip_existing_ids:  既に staging に存在する yahoo_lot_id のセット

    Returns:
        (records, stats)
    """
    stats = SyncStats()
    stats.total_fetched = len(rows)

    records: list[dict] = []
    skip_ids = skip_existing_ids or set()

    for row in rows:
        raw_title = row.get("title", "") or ""

        # yahoo_lot_id 候補
        item_id = (
            row.get("item_id")
            or _derive_lot_id_from_url(row.get("url", ""))
        )

        # lot_id が取れない場合は parse_failure として記録しスキップ
        if not item_id:
            stats.total_skipped += 1
            if raw_title:
                stats.parse_failures.append({
                    "reason":    "yahoo_lot_id_missing",
                    "title_raw": raw_title[:100],
                    "url":       row.get("url", ""),
                })
            continue

        # 既存 ID スキップ (--new-only モード)
        if item_id in skip_ids:
            stats.total_skipped += 1
            continue

        # 正規化・パース
        try:
            staging_rec = normalize_lot_record(row, yahoo_listing_id=item_id)
        except Exception as exc:
            stats.total_skipped += 1
            stats.parse_failures.append({
                "reason":    f"normalize_error: {exc}",
                "title_raw": raw_title[:100],
                "item_id":   item_id,
            })
            continue

        # parse_confidence が極端に低い場合は failures にも記録 (スキップはしない)
        parsed = parse_lot_title(raw_title)
        if parsed.parse_confidence < 0.2 and raw_title:
            stats.parse_failures.append({
                "reason":    "low_confidence",
                "title_raw": raw_title[:100],
                "confidence": parsed.parse_confidence,
                "item_id":   item_id,
            })

        records.append(staging_rec)
        stats.total_normalized += 1

    return records, stats


def _derive_lot_id_from_url(url: str | None) -> str | None:
    """Yahoo!落札 URL から item ID を抽出する (normalizer と同じロジック)。"""
    if not url:
        return None
    import re
    m = re.search(r'/auction/([a-z]\d+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-z0-9]+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# ================================================================
# dataclass 代替 (Python 3.9 互換)
# ================================================================

def dataclass_like(cls):
    """単純な __init__ 付きクラスに使うデコレータ (標準 dataclass の代替)。"""
    return cls


# ================================================================
# レポート出力
# ================================================================

def print_sync_report(
    stats: SyncStats,
    upsert_result: UpsertResult,
    status_counts: dict[str, int],
    dry_run: bool,
) -> None:
    """同期結果のサマリーを標準出力に表示する。"""
    tag = "[DRY-RUN] " if dry_run else ""
    print()
    print("=" * 60)
    print(f"{tag}Yahoo Sold Sync -- 実行結果")
    print("=" * 60)
    print(f"  取得件数 (market_transactions.source=yahoo): {stats.total_fetched}")
    print(f"  変換成功:   {stats.total_normalized}")
    print(f"  スキップ:   {stats.total_skipped}  (lot_id 不明 / 既存 / 変換エラー)")
    print()
    print(f"  DB upsert:  {upsert_result.upserted_count} 件")
    if upsert_result.error_count:
        print(f"  upsert エラー: {upsert_result.error_count} 件")
        for err in upsert_result.errors[:5]:
            print(f"    - {err}")
    print()

    if stats.parse_failures:
        print(f"  パース WARN: {len(stats.parse_failures)} 件 (スキップ含む)")
        for pf in stats.parse_failures[:10]:
            reason = pf.get("reason", "?")
            title  = pf.get("title_raw", "")[:60]
            conf   = pf.get("confidence", "")
            conf_s = f" (conf={conf})" if conf != "" else ""
            print(f"    [{reason}]{conf_s} {title!r}")
        if len(stats.parse_failures) > 10:
            print(f"    ... and {len(stats.parse_failures) - 10} more")
    print()

    if not dry_run:
        print("  staging ステータス別件数:")
        for status, cnt in status_counts.items():
            print(f"    {status}: {cnt}")
    print("=" * 60)


# ================================================================
# メインロジック
# ================================================================

def run_sync(
    dry_run:   bool  = False,
    new_only:  bool  = False,
    since:     str | None = None,
    limit:     int | None = None,
) -> int:
    """
    同期処理を実行する。

    Returns:
        0 = 成功, 1 = エラーあり
    """
    run_date   = date.today().isoformat()
    exit_code  = 0

    print("=" * 60)
    mode_label = "DRY-RUN" if dry_run else "LIVE"
    print(f"Yahoo Sold Sync [{mode_label}]  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if since:
        print(f"  since: {since}")
    if limit:
        print(f"  limit: {limit}")
    if new_only:
        print("  mode: new-only (既存 yahoo_lot_id をスキップ)")
    print("=" * 60)

    # Supabase 接続
    try:
        client = get_client()
    except SystemExit:
        logger.error("Supabase 接続失敗。.env を確認してください。")
        return 1
    except Exception as exc:
        logger.error("Supabase 接続エラー: %s", exc)
        return 1

    # 1. market_transactions から Yahoo! データ取得
    rows = fetch_yahoo_market_transactions(client, since=since, limit=limit)
    if not rows:
        print("取得件数 0 件 — 処理終了")
        record_job_run(client, run_date, "ok", 0, 0)
        return 0

    # 2. 既存 staging ID の取得 (--new-only 時のみ)
    skip_ids: set[str] = set()
    if new_only:
        candidate_ids = [
            r.get("item_id") or _derive_lot_id_from_url(r.get("url", ""))
            for r in rows
        ]
        candidate_ids = [cid for cid in candidate_ids if cid]
        if candidate_ids:
            skip_ids = get_already_synced_ids(client, candidate_ids)
            logger.info("既存 staging ID: %d 件 (スキップ対象)", len(skip_ids))

    # 3. 変換
    staging_records, stats = convert_to_staging_records(rows, skip_existing_ids=skip_ids)

    # 4. upsert
    upsert_result = upsert_staging_records(client, staging_records, dry_run=dry_run)
    if upsert_result.error_count:
        exit_code = 1

    # 5. staging ステータス集計
    status_counts: dict[str, int] = {}
    if not dry_run:
        status_counts = count_by_status(client)

    # 6. レポート表示
    print_sync_report(stats, upsert_result, status_counts, dry_run=dry_run)

    # 7. ジョブ記録
    if not dry_run:
        job_status = "error" if exit_code else "ok"
        if upsert_result.error_count and upsert_result.upserted_count:
            job_status = "partial"
        error_msg = "; ".join(upsert_result.errors[:3]) if upsert_result.errors else None
        record_job_run(
            client,
            run_date,
            job_status,
            fetched_count  = stats.total_fetched,
            inserted_count = upsert_result.upserted_count,
            error_message  = error_msg,
        )

    return exit_code


# ================================================================
# CLI エントリーポイント
# ================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Yahoo!落札データを yahoo_sold_lots_staging に同期する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python scripts/yahoo_sold_sync.py --dry-run
  python scripts/yahoo_sold_sync.py --limit 100
  python scripts/yahoo_sold_sync.py --new-only --since 2024-01-01
        """,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB に書かず変換結果のみ表示する",
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="既に staging に存在する yahoo_lot_id をスキップする",
    )
    parser.add_argument(
        "--since", type=str, default=None, metavar="YYYY-MM-DD",
        help="指定日以降の sold_date のみ処理する",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="処理件数の上限 (テスト用)",
    )
    args = parser.parse_args()

    return run_sync(
        dry_run  = args.dry_run,
        new_only = args.new_only,
        since    = args.since,
        limit    = args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
