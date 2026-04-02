"""
coin_business/scripts/global_lot_ingest.py
============================================
世界オークション lot 取り込みスクリプト。

各 event の T-minus ステージと last_synced_at から「次回 ingest 必要」かを判断し、
対象 event の lot を取得して global_auction_lots / global_lot_price_snapshots に保存する。

T-minus cadence:
  T-21: 24h ごと   (初期収集)
  T-7:  12h ごと   (週次更新)
  T-3:   6h ごと   (直前監視)
  T-1:   1h ごと   (当日)

処理フロー:
  1. load_events_due_for_ingest() で ingest 対象 event を取得
  2. event ごとに fetcher.fetch_lots() を呼び出す
  3. lot を global_auction_lots に upsert
  4. 前回 bid_usd と比較して global_lot_price_snapshots に INSERT
  5. event の last_synced_at を更新
  6. record_ingest_run() でジョブ記録

CLI オプション:
  --dry-run         : DB 書き込みなし
  --force           : cadence に関わらず全 upcoming event を処理
  --house NAME      : 特定ハウスのみ実行
  --event-id UUID   : 特定 event のみ実行
  --status-only     : ingest 待ち event 件数を表示して終了
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from global_auction.fetchers import FETCHER_MAP
from db.global_repo import (
    load_events_due_for_ingest,
    load_upcoming_events,
    upsert_lot,
    load_lots_for_event,
    insert_lot_snapshot,
    update_event_t_minus,
    record_ingest_run,
)
from constants import TMinusStage

logger = logging.getLogger(__name__)


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class IngestResult:
    events_processed: int = 0
    lots_fetched:     int = 0
    lots_saved:       int = 0
    snapshots_saved:  int = 0
    error_count:      int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.lots_saved > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def _t_minus_for_event(event: dict) -> Optional[int]:
    """event の auction_date から現在の T-minus ステージを計算する。"""
    auction_date_str = event.get("auction_date")
    if not auction_date_str:
        return event.get("t_minus_stage")  # DB の値を使う
    try:
        auction_dt = date.fromisoformat(str(auction_date_str)[:10])
        days_until  = (auction_dt - date.today()).days
        return TMinusStage.from_days_until(days_until)
    except (ValueError, TypeError):
        return event.get("t_minus_stage")


def _ingest_event(
    client,
    event:   dict,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """
    1 event の lot を取得・保存する。

    Returns:
        (lots_fetched, lots_saved, snapshots_saved)
    """
    house_name = event.get("auction_house", "")
    event_id   = event.get("id", "")

    fetcher = FETCHER_MAP.get(house_name)
    if not fetcher:
        logger.warning("fetcher が見つからない: %s", house_name)
        return 0, 0, 0

    # lot 取得
    try:
        lots = fetcher.fetch_lots(event)
    except Exception as exc:
        logger.error("[%s] fetch_lots 例外: %s", house_name, exc)
        return 0, 0, 0

    if not lots:
        logger.info("[%s] event=%s: lot 0 件", house_name,
                    event.get("event_id_external", "?"))
        return 0, 0, 0

    # 既存 lot の bid_usd を取得 (snapshot 差分計算用)
    existing_bids: dict[str, float] = {}
    if not dry_run:
        existing = load_lots_for_event(client, event_id)
        for ex in existing:
            ext_id = ex.get("lot_id_external") or ex.get("id", "")
            bid    = ex.get("current_bid_usd")
            if ext_id and bid is not None:
                existing_bids[ext_id] = float(bid)

    lots_saved     = 0
    snapshots_saved = 0

    for lot in lots:
        if dry_run:
            logger.debug("  [DRY-RUN] lot %s: %s",
                         lot.get("lot_number", "?"),
                         lot.get("lot_title", "")[:50])
            lots_saved      += 1
            snapshots_saved += 1
            continue

        # lot upsert
        lot_id = upsert_lot(client, event_id, lot)
        if not lot_id:
            continue
        lots_saved += 1

        # snapshot insert
        ext_id       = lot.get("lot_id_external", "")
        prev_bid_usd = existing_bids.get(ext_id)
        saved = insert_lot_snapshot(
            client          = client,
            lot_id          = lot_id,
            current_bid_usd = lot.get("current_bid_usd"),
            bid_count       = lot.get("bid_count"),
            lot_end_at      = lot.get("lot_end_at"),
            prev_bid_usd    = prev_bid_usd,
        )
        if saved:
            snapshots_saved += 1

    return len(lots), lots_saved, snapshots_saved


def run_ingest(
    dry_run:    bool = False,
    force:      bool = False,
    house:      str | None = None,
    event_id:   str | None = None,
) -> IngestResult:
    """
    T-minus cadence に基づいて lot を取得・保存する。

    Args:
        dry_run:  True = DB 書き込みなし
        force:    True = cadence 無視で全 upcoming event を処理
        house:    特定ハウスのみ
        event_id: 特定 event のみ

    Returns:
        IngestResult
    """
    result = IngestResult()
    client = get_client()

    # 処理対象 event を決定
    if event_id:
        events = [{"id": event_id, "auction_house": house or "", "t_minus_stage": None}]
    elif force:
        events = load_upcoming_events(client, limit=100)
    else:
        events = load_events_due_for_ingest(client, limit=20)

    if house:
        events = [ev for ev in events if ev.get("auction_house") == house]

    if not events:
        logger.info("処理対象の event がありません")
        return result

    logger.info("処理対象 event: %d 件 (dry_run=%s)", len(events), dry_run)

    for event in events:
        ev_id      = event.get("id", "?")
        ev_name    = event.get("event_name", "?")
        house_name = event.get("auction_house", "?")

        logger.info("[%s] %s ...", house_name, ev_name[:50])

        fetched, saved, snaps = _ingest_event(client, event, dry_run=dry_run)
        result.events_processed += 1
        result.lots_fetched     += fetched
        result.lots_saved       += saved
        result.snapshots_saved  += snaps

        # event の T-minus と last_synced_at を更新
        if not dry_run:
            new_stage = _t_minus_for_event(event)
            update_event_t_minus(client, ev_id, new_stage)

        logger.info(
            "  完了: fetched=%d saved=%d snapshots=%d",
            fetched, saved, snaps,
        )

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "global_lot_ingest.py",
        description = "世界オークション lot を取得して DB に保存する",
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="DB 書き込みなし")
    parser.add_argument("--force",       action="store_true",
                        help="cadence 無視で全 upcoming event を処理")
    parser.add_argument("--house",       type=str, default=None,
                        help="特定ハウスのみ (heritage/stacks_bowers/spink/noble)")
    parser.add_argument("--event-id",    type=str, default=None,
                        help="特定 event のみ処理 (UUID)")
    parser.add_argument("--status-only", action="store_true",
                        help="ingest 待ち event 件数を表示して終了")
    args = parser.parse_args()

    if args.status_only:
        client = get_client()
        events = load_events_due_for_ingest(client, limit=50)
        print(f"ingest 待ち event 件数: {len(events)}")
        for ev in events[:10]:
            print(f"  [{ev.get('auction_house','')}] "
                  f"{ev.get('event_name','')[:40]} "
                  f"T-{ev.get('t_minus_stage','?')}")
        return

    result = run_ingest(
        dry_run  = args.dry_run,
        force    = args.force,
        house    = args.house,
        event_id = args.event_id,
    )

    if not args.dry_run:
        client = get_client()
        record_ingest_run(
            client           = client,
            run_date         = date.today().isoformat(),
            status           = result.status_str(),
            events_processed = result.events_processed,
            lots_fetched     = result.lots_fetched,
            lots_saved       = result.lots_saved,
            snapshots_saved  = result.snapshots_saved,
            error_count      = result.error_count,
            error_message    = "; ".join(result.errors[:5]) if result.errors else None,
        )

    print(
        f"\n=== Global Lot Ingest {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  events_processed: {result.events_processed}\n"
        f"  lots_fetched:     {result.lots_fetched}\n"
        f"  lots_saved:       {result.lots_saved}\n"
        f"  snapshots_saved:  {result.snapshots_saved}\n"
        f"  error_count:      {result.error_count}\n"
        f"  status:           {result.status_str()}"
    )


if __name__ == "__main__":
    main()
