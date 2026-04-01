"""
coin_business/scripts/ebay_api_ingest.py
==========================================
eBay Browse API から listing を取得し、DB に保存するインジェストスクリプト。

処理フロー:
  1. yahoo_coin_seeds から READY 状態の seed を取得
  2. seed ごとに EbayBrowseClient.search_by_seed() で listing 検索
  3. 取得した listing を ebay_listings_raw に upsert
  4. 差分計算して ebay_listing_snapshots に INSERT
  5. seed の状態を COOLDOWN に更新
  6. ジョブ実行記録を job_ebay_ingest_daily に保存

CLI オプション:
  --dry-run          : DB 書き込みなし (取得・変換のみ)
  --smoke            : 1 seed だけ実行して動作確認
  --limit N          : 処理する seed 数の上限 (デフォルト 50)
  --seed-limit N     : 1 seed あたりの listing 取得件数 (デフォルト 50)
  --status-only      : READY seed 件数を表示して終了
  --seed-types T,T   : 処理する seed_type をカンマ区切りで指定
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# ── プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from ebay.client import EbayBrowseClient
from db.ebay_repo import (
    load_ready_seeds,
    mark_seed_scanning,
    mark_seed_scanned,
    upsert_listing_raw,
    get_raw_by_item_id,
    insert_snapshot,
    requeue_cooled_seeds,
    record_ingest_run,
)

logger = logging.getLogger(__name__)


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class IngestResult:
    seeds_scanned:    int = 0
    listings_fetched: int = 0
    listings_saved:   int = 0
    snapshots_saved:  int = 0
    error_count:      int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.listings_saved > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def run_ingest(
    dry_run:    bool = False,
    smoke:      bool = False,
    limit:      int  = 50,
    seed_limit: int  = 50,
    seed_types: list[str] | None = None,
) -> IngestResult:
    """
    eBay API 取り込みを実行する。

    Args:
        dry_run:    True = DB 書き込みなし
        smoke:      True = 1 seed のみ実行
        limit:      処理する seed の最大数
        seed_limit: 1 seed あたりの listing 取得件数
        seed_types: 絞り込む seed_type リスト (None = 全種別)

    Returns:
        IngestResult
    """
    result = IngestResult()

    client  = get_client()
    ebay    = EbayBrowseClient()

    if not ebay.is_configured:
        msg = "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が未設定 — インジェストをスキップ"
        logger.warning(msg)
        result.errors.append(msg)
        result.error_count += 1
        return result

    # ── クールダウン終了 seed を READY に戻す
    requeued = requeue_cooled_seeds(client)
    if requeued:
        logger.info("COOLDOWN → READY: %d 件", requeued)

    # ── READY seed を取得
    effective_limit = 1 if smoke else limit
    seeds = load_ready_seeds(client, limit=effective_limit, seed_types=seed_types)

    if not seeds:
        logger.info("READY 状態の seed がありません — 終了")
        return result

    logger.info(
        "対象 seed: %d 件 (dry_run=%s, smoke=%s)",
        len(seeds), dry_run, smoke,
    )

    for seed in seeds:
        seed_id = seed.get("id", "?")
        query   = seed.get("search_query", "")

        logger.info("Seed [%s] query='%s'", seed_id, query[:60])

        # ── seed を SCANNING に更新
        if not dry_run:
            mark_seed_scanning(client, seed_id)

        # ── eBay 検索
        try:
            items = ebay.search_by_seed(seed, limit=seed_limit)
        except Exception as exc:
            msg = f"seed={seed_id} 検索例外: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            result.error_count += 1
            if not dry_run:
                mark_seed_scanned(client, seed_id, hit_count_delta=0)
            continue

        result.seeds_scanned    += 1
        result.listings_fetched += len(items)
        hit_count = 0

        for item in items:
            ebay_item_id = item.get("ebay_item_id", "")

            if dry_run:
                logger.debug("  [DRY-RUN] %s  %s", ebay_item_id, item.get("title", "")[:50])
                result.listings_saved  += 1
                result.snapshots_saved += 1
                hit_count += 1
                continue

            # ── 既存 raw データを取得 (差分計算用)
            prev = get_raw_by_item_id(client, ebay_item_id)

            # ── ebay_listings_raw に upsert
            listing_id = upsert_listing_raw(client, item)
            if not listing_id:
                result.error_count += 1
                continue

            result.listings_saved += 1
            hit_count += 1

            # ── ebay_listing_snapshots に INSERT
            saved = insert_snapshot(
                client      = client,
                listing_id  = listing_id,
                ebay_item_id= ebay_item_id,
                item        = item,
                prev        = prev,
            )
            if saved:
                result.snapshots_saved += 1
            else:
                result.error_count += 1

        # ── seed を COOLDOWN に更新
        if not dry_run:
            mark_seed_scanned(client, seed_id, hit_count_delta=hit_count)

        logger.info(
            "  完了: fetched=%d saved=%d snapshots=%d",
            len(items), hit_count, result.snapshots_saved,
        )

    return result


# ================================================================
# CLI エントリーポイント
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "ebay_api_ingest.py",
        description = "eBay Browse API から listing を取得して DB に保存する",
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="DB 書き込みなし (取得・変換のみ確認)")
    parser.add_argument("--smoke",       action="store_true",
                        help="1 seed だけ実行して動作確認")
    parser.add_argument("--limit",       type=int, default=50,
                        help="処理する seed の最大数 (デフォルト: 50)")
    parser.add_argument("--seed-limit",  type=int, default=50,
                        help="1 seed あたりの listing 取得件数 (デフォルト: 50)")
    parser.add_argument("--seed-types",  type=str, default=None,
                        help="処理する seed_type をカンマ区切りで指定 (例: CERT_EXACT,CERT_TITLE)")
    parser.add_argument("--status-only", action="store_true",
                        help="READY seed 件数を表示して終了")
    args = parser.parse_args()

    # ── --status-only
    if args.status_only:
        client = get_client()
        seeds  = load_ready_seeds(client, limit=1000)
        print(f"READY seed 件数: {len(seeds)}")
        return

    # ── seed_types パース
    seed_types: list[str] | None = None
    if args.seed_types:
        seed_types = [s.strip() for s in args.seed_types.split(",") if s.strip()]

    # ── 実行
    result = run_ingest(
        dry_run    = args.dry_run,
        smoke      = args.smoke,
        limit      = args.limit,
        seed_limit = args.seed_limit,
        seed_types = seed_types,
    )

    # ── ジョブ記録 (dry_run は記録しない)
    if not args.dry_run:
        client   = get_client()
        run_date = date.today().isoformat()
        record_ingest_run(
            client           = client,
            run_date         = run_date,
            status           = result.status_str(),
            seeds_scanned    = result.seeds_scanned,
            listings_fetched = result.listings_fetched,
            listings_saved   = result.listings_saved,
            snapshots_saved  = result.snapshots_saved,
            error_count      = result.error_count,
            error_message    = "; ".join(result.errors[:5]) if result.errors else None,
        )

    # ── サマリー出力
    print(
        f"\n=== eBay API Ingest {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  seeds_scanned:    {result.seeds_scanned}\n"
        f"  listings_fetched: {result.listings_fetched}\n"
        f"  listings_saved:   {result.listings_saved}\n"
        f"  snapshots_saved:  {result.snapshots_saved}\n"
        f"  error_count:      {result.error_count}\n"
        f"  status:           {result.status_str()}"
    )

    if result.errors:
        print("\nErrors:")
        for e in result.errors[:10]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
