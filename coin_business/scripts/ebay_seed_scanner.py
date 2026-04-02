"""
coin_business/scripts/ebay_seed_scanner.py
============================================
Yahoo seed 起点の eBay seed スキャナー CLI エントリーポイント。

処理フロー:
  1. COOLDOWN 終了 seed を READY に戻す
  2. READY seed を priority 降順で取得
  3. seed ごとに eBay Browse API で検索
  4. listing を ebay_listings_raw に upsert
  5. 新規マッチを ebay_seed_hits に記録 (重複 skip)
  6. seed を COOLDOWN + next_scan_at 更新
  7. ジョブ実行記録を job_ebay_scanner_daily に保存

CLI オプション:
  --dry-run          : DB 書き込みなし (取得・変換のみ)
  --smoke            : 1 seed だけ実行して動作確認
  --limit N          : 処理する seed 数の上限 (デフォルト 50)
  --seed-limit N     : 1 seed あたりの listing 取得件数 (デフォルト 50)
  --seed-types T,T   : 処理する seed_type をカンマ区切りで指定
  --status-only      : READY seed 件数を表示して終了
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from ebay.scanner import SeedScanner
from db.ebay_repo import load_ready_seeds, record_scanner_run

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "ebay_seed_scanner.py",
        description = "Yahoo seed 起点の eBay スキャナー",
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="DB 書き込みなし (確認のみ)")
    parser.add_argument("--smoke",       action="store_true",
                        help="1 seed だけ実行して動作確認")
    parser.add_argument("--limit",       type=int, default=50,
                        help="処理する seed の最大数 (デフォルト: 50)")
    parser.add_argument("--seed-limit",  type=int, default=50,
                        help="1 seed あたりの listing 取得件数 (デフォルト: 50)")
    parser.add_argument("--seed-types",  type=str, default=None,
                        help="seed_type をカンマ区切りで指定 (例: CERT_EXACT,CERT_TITLE)")
    parser.add_argument("--status-only", action="store_true",
                        help="READY seed 件数を表示して終了")
    args = parser.parse_args()

    # --status-only
    if args.status_only:
        client = get_client()
        seeds  = load_ready_seeds(client, limit=1000)
        print(f"READY seed 件数: {len(seeds)}")
        return

    # seed_types パース
    seed_types: list[str] | None = None
    if args.seed_types:
        seed_types = [s.strip() for s in args.seed_types.split(",") if s.strip()]

    # smoke モード: limit=1
    effective_limit = 1 if args.smoke else args.limit

    # スキャン実行
    client  = get_client()
    scanner = SeedScanner(client)
    result  = scanner.run(
        limit      = effective_limit,
        dry_run    = args.dry_run,
        seed_types = seed_types,
        seed_limit = args.seed_limit,
    )

    # ジョブ記録 (dry_run は記録しない)
    if not args.dry_run:
        record_scanner_run(
            client         = client,
            run_date       = date.today().isoformat(),
            status         = result.status_str(),
            seeds_scanned  = result.seeds_scanned,
            hits_found     = result.hits_found,
            hits_saved     = result.hits_saved,
            error_count    = result.error_count,
            error_message  = "; ".join(result.errors[:5]) if result.errors else None,
        )

    # サマリー出力
    print(
        f"\n=== eBay Seed Scanner {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  seeds_scanned: {result.seeds_scanned}\n"
        f"  hits_found:    {result.hits_found}\n"
        f"  hits_saved:    {result.hits_saved}\n"
        f"  error_count:   {result.error_count}\n"
        f"  status:        {result.status_str()}"
    )

    if result.errors:
        print("\nErrors:")
        for e in result.errors[:10]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
