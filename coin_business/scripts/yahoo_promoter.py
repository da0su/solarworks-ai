"""
coin_business/scripts/yahoo_promoter.py
==========================================
CEO 承認済み Yahoo!落札履歴を本DB (yahoo_sold_lots) に昇格するスクリプト。

フロー:
  1. yahoo_sold_lots_staging から status=APPROVED_TO_MAIN を取得
  2. 各レコードのレビュー情報 (approved_by, approved_at) を取得
  3. yahoo_sold_lots に upsert (dedup key = yahoo_lot_id)
  4. staging.status を PROMOTED に更新
  5. ジョブ実行記録を job_yahoo_promoter_daily に保存

絶対原則:
  - APPROVED_TO_MAIN のみ昇格。PENDING_CEO / HELD / REJECTED は昇格させない。
  - staging を直接 yahoo_sold_lots に置き換えない。
    必ず APPROVED_TO_MAIN の確認を db 層で再チェックする。

使い方:
  cd coin_business
  python scripts/yahoo_promoter.py                  # 通常実行
  python scripts/yahoo_promoter.py --dry-run        # DB に書かず確認のみ
  python scripts/yahoo_promoter.py --limit 50       # 上限 50 件
  python scripts/yahoo_promoter.py --status-only    # 昇格可能件数を確認して終了
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from db.yahoo_promoter_repo import (
    PromoteResult,
    count_promotable,
    get_approval_info,
    load_approved_staging,
    promote_to_main,
    record_promoter_run,
)

logger = logging.getLogger(__name__)


# ================================================================
# メイン処理
# ================================================================

def run_promote(
    dry_run:  bool = False,
    limit:    int  = 500,
) -> PromoteResult:
    """
    APPROVED_TO_MAIN レコードを yahoo_sold_lots に昇格する。

    Args:
        dry_run:  True の場合は DB に書かず、変換結果のみ返す
        limit:    一度に処理する最大件数

    Returns:
        PromoteResult
    """
    result = PromoteResult()
    client = get_client()

    # 昇格可能件数を確認
    total_promotable = count_promotable(client)
    logger.info("昇格可能件数 (APPROVED_TO_MAIN): %d 件", total_promotable)

    if total_promotable == 0:
        logger.info("昇格対象なし — 終了")
        return result

    if total_promotable < 0:
        logger.error("件数取得失敗 — 終了")
        result.ok = False
        return result

    # staging から APPROVED_TO_MAIN を取得
    staging_records = load_approved_staging(client, limit=limit)
    logger.info("取得件数: %d 件 (limit=%d)", len(staging_records), limit)

    for rec in staging_records:
        yahoo_lot_id = rec.get("yahoo_lot_id", "?")
        staging_id   = rec.get("id", "?")

        if dry_run:
            logger.info(
                "[DRY-RUN] 昇格予定: lot_id=%-20s title=%.40s",
                yahoo_lot_id, rec.get("lot_title", ""),
            )
            result.promoted_count += 1
            continue

        # 承認情報を取得 (approved_by / approved_at)
        approval = get_approval_info(client, staging_id)
        approved_by = approval.get("approved_by", "")
        approved_at = approval.get("approved_at", "")

        # 昇格実行
        ok = promote_to_main(
            client,
            staging_rec = rec,
            approved_by = approved_by,
            approved_at = approved_at,
        )

        if ok:
            result.promoted_count += 1
            logger.info(
                "昇格完了 [%d/%d]: lot_id=%-20s by=%s",
                result.promoted_count, len(staging_records),
                yahoo_lot_id, approved_by or "(不明)",
            )
        else:
            result.error_count += 1
            err_msg = f"昇格失敗: yahoo_lot_id={yahoo_lot_id} staging_id={staging_id}"
            result.errors.append(err_msg)
            logger.error(err_msg)

    # 結果サマリー
    if result.error_count > 0:
        result.ok = False

    return result


# ================================================================
# CLI エントリーポイント
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yahoo_promoter.py",
        description="APPROVED_TO_MAIN の staging レコードを yahoo_sold_lots に昇格する",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB に書かず変換結果のみ表示",
    )
    parser.add_argument(
        "--limit", type=int, default=500,
        help="一度に処理する最大件数 (デフォルト: 500)",
    )
    parser.add_argument(
        "--status-only", action="store_true",
        help="昇格可能件数を表示して終了",
    )
    args, _ = parser.parse_known_args(sys.argv[1:])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = get_client()

    if args.status_only:
        n = count_promotable(client)
        print(f"昇格可能件数 (APPROVED_TO_MAIN): {n} 件")
        return

    logger.info("=== yahoo_promoter 開始 %s dry_run=%s limit=%d ===",
                today, args.dry_run, args.limit)

    result = run_promote(dry_run=args.dry_run, limit=args.limit)

    # ジョブ記録 (dry_run は記録しない)
    if not args.dry_run:
        job_status = "ok" if result.ok else ("partial" if result.promoted_count > 0 else "error")
        err_msg = "; ".join(result.errors[:5]) if result.errors else None
        record_promoter_run(
            client,
            run_date       = today,
            status         = job_status,
            promoted_count = result.promoted_count,
            skipped_count  = result.skipped_count,
            error_count    = result.error_count,
            error_message  = err_msg,
        )

    # 結果表示
    print(f"\n=== yahoo_promoter 完了 ===")
    print(f"  昇格済み:   {result.promoted_count:>6} 件")
    print(f"  スキップ:   {result.skipped_count:>6} 件")
    print(f"  エラー:     {result.error_count:>6} 件")
    if args.dry_run:
        print("  [DRY-RUN モード — DB への書き込みは行いません]")

    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
