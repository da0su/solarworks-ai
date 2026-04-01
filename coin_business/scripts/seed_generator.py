"""
coin_business/scripts/seed_generator.py
==========================================
yahoo_sold_lots から探索用 seed を生成し yahoo_coin_seeds に upsert するスクリプト。

フロー:
  1. yahoo_sold_lots から全レコード (または差分) を取得
  2. seeds/builder.py で各レコードの seed を生成
  3. yahoo_coin_seeds に upsert (dedup key = yahoo_lot_id + seed_type)
  4. ジョブ実行記録を job_seed_generator_daily に保存

絶対原則:
  - yahoo_sold_lots_staging からは生成しない。yahoo_sold_lots のみ入力。
  - staging を探索母集団に混入させない。
  - ON CONFLICT で冪等に動作する (同一レコードを 2 回処理しても重複しない)。

使い方:
  cd coin_business
  python scripts/seed_generator.py                      # 通常実行
  python scripts/seed_generator.py --dry-run            # DB に書かず確認のみ
  python scripts/seed_generator.py --limit 200          # 上限 200 件
  python scripts/seed_generator.py --since 2024-01-01   # 指定日以降の新規 lot のみ
  python scripts/seed_generator.py --status-only        # 件数確認して終了
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
from seeds.builder import build_seeds_for_lot
from constants import SeedStatus, Table

logger = logging.getLogger(__name__)

MAIN_TABLE  = Table.YAHOO_SOLD_LOTS    # "yahoo_sold_lots"
SEEDS_TABLE = Table.YAHOO_COIN_SEEDS   # "yahoo_coin_seeds"
BATCH_SIZE  = 200                      # Supabase REST API の安全バッチサイズ


# ================================================================
# データ取得
# ================================================================

def load_main_lots(
    client,
    limit:  int = 1000,
    offset: int = 0,
    since:  str | None = None,
) -> list[dict]:
    """
    yahoo_sold_lots から seed 生成対象レコードを取得する。

    Args:
        client:  Supabase クライアント
        limit:   最大取得件数
        offset:  ページングオフセット
        since:   "YYYY-MM-DD" 以降に created_at があるレコードのみ
                 (差分実行に使用)
    Returns:
        list of dict
    """
    try:
        q = (
            client.table(MAIN_TABLE)
            .select(
                "id, yahoo_lot_id, lot_title, title_normalized, "
                "year, denomination, cert_company, cert_number, grade_text, "
                "sold_price_jpy, sold_date, parse_confidence, created_at"
            )
            .order("created_at", desc=False)
        )
        if since:
            q = q.gte("created_at", since)
        q = q.range(offset, offset + limit - 1)
        resp = q.execute()
        return resp.data or []
    except Exception as exc:
        logger.error("load_main_lots 失敗: %s", exc)
        return []


def count_main_lots(client) -> int:
    """yahoo_sold_lots の総件数を返す。"""
    try:
        resp = client.table(MAIN_TABLE).select("id", count="exact").execute()
        return resp.count or 0
    except Exception as exc:
        logger.error("count_main_lots 失敗: %s", exc)
        return -1


def get_already_seeded_lot_ids(client, lot_ids: list[str]) -> set[str]:
    """
    渡した yahoo_lot_id のうち、すでに yahoo_coin_seeds にある lot_id の集合を返す。
    差分実行で「seed がない lot だけ処理」するために使う。
    """
    if not lot_ids:
        return set()
    try:
        resp = (
            client.table(SEEDS_TABLE)
            .select("yahoo_lot_id")
            .in_("yahoo_lot_id", lot_ids)
            .execute()
        )
        return {row["yahoo_lot_id"] for row in (resp.data or [])}
    except Exception as exc:
        logger.warning("get_already_seeded_lot_ids 失敗: %s", exc)
        return set()


# ================================================================
# seed upsert
# ================================================================

def upsert_seeds(client, seeds: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """
    yahoo_coin_seeds に seeds を batch upsert する。

    ON CONFLICT (yahoo_lot_id, seed_type) で冪等動作。

    Returns:
        (upserted_count, error_count)
    """
    if not seeds:
        return 0, 0

    if dry_run:
        logger.info("[DRY-RUN] upsert 予定 seed 件数: %d", len(seeds))
        return len(seeds), 0

    total_upserted = 0
    error_count    = 0

    for i in range(0, len(seeds), BATCH_SIZE):
        batch = seeds[i:i + BATCH_SIZE]
        try:
            resp = client.table(SEEDS_TABLE).upsert(
                batch,
                on_conflict="yahoo_lot_id,seed_type",
            ).execute()
            upserted = len(resp.data) if resp.data else len(batch)
            total_upserted += upserted
            logger.debug("seeds batch[%d-%d] upserted %d rows", i, i + len(batch), upserted)
        except Exception as exc:
            logger.error("seeds batch[%d-%d] upsert 失敗: %s", i, i + len(batch), exc)
            error_count += 1

    return total_upserted, error_count


# ================================================================
# メイン処理
# ================================================================

def run_seed_generator(
    dry_run:     bool       = False,
    limit:       int        = 1000,
    since:       str | None = None,
    new_only:    bool       = False,
) -> dict:
    """
    yahoo_sold_lots から seed を生成して yahoo_coin_seeds に upsert する。

    Args:
        dry_run:   True = DB に書かず確認のみ
        limit:     最大処理件数
        since:     "YYYY-MM-DD" 以降の lot のみ処理
        new_only:  True = 既に seed がある lot をスキップ

    Returns:
        dict: {
            "lots_processed": int,
            "lots_skipped": int,
            "seeds_generated": int,
            "seeds_upserted": int,
            "error_count": int,
        }
    """
    client = get_client()
    stats = {
        "lots_processed":  0,
        "lots_skipped":    0,
        "seeds_generated": 0,
        "seeds_upserted":  0,
        "error_count":     0,
    }

    # yahoo_sold_lots から取得
    lots = load_main_lots(client, limit=limit, since=since)
    logger.info("yahoo_sold_lots 取得件数: %d", len(lots))

    if not lots:
        logger.info("seed 生成対象なし — 終了")
        return stats

    # new_only: 既に seed があるものをスキップ
    if new_only:
        lot_ids = [lot["yahoo_lot_id"] for lot in lots if lot.get("yahoo_lot_id")]
        already_seeded = get_already_seeded_lot_ids(client, lot_ids)
        original_count = len(lots)
        lots = [lot for lot in lots if lot.get("yahoo_lot_id") not in already_seeded]
        skipped = original_count - len(lots)
        stats["lots_skipped"] += skipped
        logger.info("new_only: %d 件をスキップ (既存 seed あり)", skipped)

    if not lots:
        logger.info("new_only フィルタ後 seed 生成対象なし — 終了")
        return stats

    # seed 生成
    all_seeds: list[dict] = []
    for lot in lots:
        seeds = build_seeds_for_lot(lot)
        if seeds:
            all_seeds.extend(seeds)
            stats["lots_processed"] += 1
        else:
            stats["lots_skipped"] += 1
            logger.debug(
                "seed 生成なし: lot_id=%s title=%.40s",
                lot.get("yahoo_lot_id", "?"), lot.get("lot_title", ""),
            )

    stats["seeds_generated"] = len(all_seeds)
    logger.info("seed 生成件数: %d (lot %d 件から)", len(all_seeds), stats["lots_processed"])

    if not all_seeds:
        return stats

    # upsert
    upserted, errors = upsert_seeds(client, all_seeds, dry_run=dry_run)
    stats["seeds_upserted"] = upserted
    stats["error_count"]    = errors

    return stats


# ================================================================
# ジョブ記録
# ================================================================

def record_seed_generator_run(
    client,
    run_date:        str,
    status:          str,
    generated_count: int,
    skipped_count:   int = 0,
    error_count:     int = 0,
    error_message:   str | None = None,
) -> bool:
    """job_seed_generator_daily にジョブ実行記録を insert する。"""
    try:
        record: dict = {
            "run_date":        run_date,
            "status":          status,
            "generated_count": generated_count,
            "skipped_count":   skipped_count,
            "error_count":     error_count,
        }
        if error_message:
            record["error_message"] = error_message[:2000]
        client.table(Table.JOB_SEED_GENERATOR).insert(record).execute()
        return True
    except Exception as exc:
        logger.error("seed ジョブ記録失敗: %s", exc)
        return False


# ================================================================
# CLI エントリーポイント
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="seed_generator.py",
        description="yahoo_sold_lots から探索用 seed を生成して yahoo_coin_seeds に保存する",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB に書かず確認のみ",
    )
    parser.add_argument(
        "--limit", type=int, default=1000,
        help="最大処理件数 (デフォルト: 1000)",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="YYYY-MM-DD 以降に作成された lot のみ処理",
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="seed がない lot のみ処理 (差分実行)",
    )
    parser.add_argument(
        "--status-only", action="store_true",
        help="yahoo_sold_lots の件数を確認して終了",
    )
    args, _ = parser.parse_known_args(sys.argv[1:])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = get_client()

    if args.status_only:
        n = count_main_lots(client)
        print(f"yahoo_sold_lots 件数: {n} 件")
        return

    logger.info(
        "=== seed_generator 開始 %s dry_run=%s limit=%d since=%s new_only=%s ===",
        today, args.dry_run, args.limit, args.since or "なし", args.new_only,
    )

    stats = run_seed_generator(
        dry_run  = args.dry_run,
        limit    = args.limit,
        since    = args.since,
        new_only = args.new_only,
    )

    # ジョブ記録 (dry_run は記録しない)
    if not args.dry_run:
        job_status = "error" if stats["error_count"] > 0 and stats["seeds_upserted"] == 0 else \
                     ("partial" if stats["error_count"] > 0 else "ok")
        record_seed_generator_run(
            client,
            run_date        = today,
            status          = job_status,
            generated_count = stats["seeds_upserted"],
            skipped_count   = stats["lots_skipped"],
            error_count     = stats["error_count"],
        )

    # 結果表示
    print(f"\n=== seed_generator 完了 ===")
    print(f"  Lot 処理済み: {stats['lots_processed']:>6} 件")
    print(f"  Lot スキップ: {stats['lots_skipped']:>6} 件")
    print(f"  seed 生成:   {stats['seeds_generated']:>6} 件")
    print(f"  seed upsert: {stats['seeds_upserted']:>6} 件")
    print(f"  エラー:       {stats['error_count']:>6} 件")
    if args.dry_run:
        print("  [DRY-RUN モード — DB への書き込みは行いません]")


if __name__ == "__main__":
    main()
