"""
coin_business/scripts/global_auction_sync.py
=============================================
世界オークション event 同期スクリプト。

Heritage / Stack's Bowers / Spink / Noble の公開オークション情報を取得し、
global_auction_events に保存する。

T-minus ステージ計算:
  auction_date から残り日数を算出し、TMinusStage.from_days_until() で
  t_minus_stage を決定。event upsert 時に一緒に保存する。

処理フロー:
  1. 各 fetcher.fetch_events() を呼び出す
  2. auction_date から days_until を計算
  3. TMinusStage.from_days_until() で t_minus_stage を決定
  4. 監視ウィンドウ外 (days_until > 21) の event はスキップ
  5. upsert_event() で global_auction_events に保存
  6. record_sync_run() でジョブ記録

CLI オプション:
  --dry-run         : DB 書き込みなし
  --house NAME      : 特定ハウスのみ実行 (heritage/stacks_bowers/spink/noble)
  --include-all     : 監視ウィンドウ外 (T-21以遠) の event も保存
  --status-only     : upcoming event 件数を表示して終了
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
from constants import TMinusStage
from global_auction.fetchers import ALL_FETCHERS, FETCHER_MAP
from db.global_repo import (
    upsert_event,
    load_upcoming_events,
    record_sync_run,
)

logger = logging.getLogger(__name__)


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class SyncResult:
    events_fetched: int = 0
    events_synced:  int = 0
    events_new:     int = 0
    events_skipped: int = 0
    error_count:    int = 0
    errors:         list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.events_synced > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def _compute_t_minus(event: dict) -> Optional[int]:
    """auction_date から t_minus_stage を計算する。"""
    auction_date_str = event.get("auction_date")
    if not auction_date_str:
        return None
    try:
        auction_dt = date.fromisoformat(str(auction_date_str)[:10])
        days_until  = (auction_dt - date.today()).days
        return TMinusStage.from_days_until(days_until)
    except (ValueError, TypeError):
        return None


def run_sync(
    dry_run:      bool = False,
    house:        str | None = None,
    include_all:  bool = False,
) -> SyncResult:
    """
    全 fetcher からイベントを取得し global_auction_events に保存する。

    Args:
        dry_run:     True = DB 書き込みなし
        house:       特定ハウスのみ ("heritage" 等)
        include_all: True = T-21 以遠のイベントも保存

    Returns:
        SyncResult
    """
    result = SyncResult()
    client = get_client()

    # fetcher を絞り込む
    if house:
        fetcher = FETCHER_MAP.get(house)
        if not fetcher:
            msg = f"不明な auction_house: {house}"
            result.errors.append(msg)
            result.error_count += 1
            return result
        fetchers = [fetcher]
    else:
        fetchers = ALL_FETCHERS

    for fetcher in fetchers:
        house_name = fetcher.auction_house
        try:
            events = fetcher.fetch_events()
        except Exception as exc:
            msg = f"[{house_name}] fetch_events 例外: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            result.error_count += 1
            continue

        result.events_fetched += len(events)
        logger.info("[%s] イベント取得: %d 件", house_name, len(events))

        for ev in events:
            # T-minus ステージを計算して付加
            t_minus = _compute_t_minus(ev)
            ev["t_minus_stage"] = t_minus
            ev["auction_house"] = house_name

            # 監視ウィンドウ外はスキップ (include_all でない限り)
            if not include_all and t_minus is None:
                result.events_skipped += 1
                logger.debug("[%s] T-21 以遠のためスキップ: %s",
                             house_name, ev.get("event_name", "?"))
                continue

            if dry_run:
                logger.debug("  [DRY-RUN] %s: %s t_minus=%s",
                             house_name, ev.get("event_name", ""), t_minus)
                result.events_synced += 1
                continue

            event_id = upsert_event(client, ev)
            if event_id:
                result.events_synced += 1
                result.events_new    += 1  # upsert なので新規/更新の区別なし
            else:
                result.error_count += 1

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "global_auction_sync.py",
        description = "世界オークション event を取得して global_auction_events に保存する",
    )
    parser.add_argument("--dry-run",      action="store_true",
                        help="DB 書き込みなし")
    parser.add_argument("--house",        type=str, default=None,
                        help="特定ハウスのみ (heritage/stacks_bowers/spink/noble)")
    parser.add_argument("--include-all",  action="store_true",
                        help="T-21 以遠のイベントも保存")
    parser.add_argument("--status-only",  action="store_true",
                        help="upcoming event 件数を表示して終了")
    args = parser.parse_args()

    if args.status_only:
        client = get_client()
        events = load_upcoming_events(client, limit=200)
        print(f"upcoming/active event 件数: {len(events)}")
        for ev in events[:10]:
            print(f"  [{ev.get('auction_house','')}] "
                  f"{ev.get('event_name','')[:40]} "
                  f"T-{ev.get('t_minus_stage','?')} "
                  f"({ev.get('auction_date','')})")
        return

    result = run_sync(
        dry_run     = args.dry_run,
        house       = args.house,
        include_all = args.include_all,
    )

    if not args.dry_run:
        client = get_client()
        record_sync_run(
            client        = client,
            run_date      = date.today().isoformat(),
            status        = result.status_str(),
            events_synced = result.events_synced,
            events_new    = result.events_new,
            error_count   = result.error_count,
            error_message = "; ".join(result.errors[:5]) if result.errors else None,
        )

    print(
        f"\n=== Global Auction Sync {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  events_fetched:  {result.events_fetched}\n"
        f"  events_synced:   {result.events_synced}\n"
        f"  events_skipped:  {result.events_skipped}\n"
        f"  error_count:     {result.error_count}\n"
        f"  status:          {result.status_str()}"
    )


if __name__ == "__main__":
    main()
