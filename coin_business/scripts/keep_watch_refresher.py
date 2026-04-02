"""
coin_business/scripts/keep_watch_refresher.py
===============================================
candidate_watchlist の ACTIVE アイテムを定期更新する。

状態遷移:
  ENDED        : auction_end_at が過去 → 終了確定
  BID_READY    : time_left <= 1h AND current_price <= max_bid
  ENDING_SOON  : time_left <= 1h (価格超過でも)
  PRICE_OK     : current_price <= max_bid
  PRICE_TOO_HIGH: current_price > max_bid
  WATCHING     : デフォルト (価格情報なし)

refresh cadence (WatchCadence):
  > 24h  → 3時間ごと
  ≤ 24h  → 1時間ごと
  ≤ 6h   → 30分ごと
  ≤ 1h   → 10分ごと

現在価格の取得:
  eBay 案件   : ebay_listings_raw から current_price_usd を取得して JPY 換算
  global lot  : global_auction_lots から current_price_usd を取得して JPY 換算
  価格取得失敗: 価格情報なしで状態遷移 (WATCHING のまま)

CLI:
  python keep_watch_refresher.py --dry-run
  python keep_watch_refresher.py --limit 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from constants import ProfitCalc, Table, WatchCadence, WatchStatus
from db.watch_repo import (
    load_active_watchlist,
    record_keep_watch_run,
    save_watchlist_snapshot,
    update_watchlist_status,
)
from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)

_FX_FALLBACK = ProfitCalc.USD_TO_JPY_FALLBACK


# ================================================================
# 状態遷移ロジック（純粋関数 — テスト対象）
# ================================================================

def determine_watch_status(
    *,
    now: datetime,
    auction_end_at: Optional[datetime],
    current_price_jpy: Optional[int],
    max_bid_jpy: Optional[int],
    time_left_seconds: Optional[int],
) -> str:
    """
    現在の監視状態を決定する。

    Returns: WatchStatus の値
    """
    # 終了判定
    if auction_end_at is not None and auction_end_at <= now:
        return WatchStatus.ENDED

    # BID_READY: 1時間以内 かつ 価格が上限以内
    within_1h = (
        time_left_seconds is not None
        and time_left_seconds <= WatchCadence.THRESHOLD_1H
    )
    if within_1h:
        if (
            current_price_jpy is not None
            and max_bid_jpy is not None
            and current_price_jpy <= max_bid_jpy
        ):
            return WatchStatus.BID_READY
        return WatchStatus.ENDING_SOON

    # 価格判定
    if current_price_jpy is not None and max_bid_jpy is not None:
        if current_price_jpy <= max_bid_jpy:
            return WatchStatus.PRICE_OK
        return WatchStatus.PRICE_HIGH

    return WatchStatus.WATCHING


def calc_time_left_seconds(
    auction_end_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[int]:
    """auction_end_at までの残り秒数。過去または None の場合は None を返す。"""
    if auction_end_at is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    delta = auction_end_at - now
    if delta.total_seconds() <= 0:
        return None
    return int(delta.total_seconds())


def calc_next_refresh_at(
    now: datetime,
    time_left_seconds: Optional[int],
) -> str:
    """次回 refresh 時刻の ISO 文字列を返す。"""
    interval = WatchCadence.for_time_left(time_left_seconds)
    next_dt = now + timedelta(seconds=interval)
    return next_dt.isoformat()


# ================================================================
# 現在価格取得
# ================================================================

def _fetch_ebay_price(client, ebay_item_id: str, fx_rate: float) -> Optional[int]:
    """ebay_listings_raw から current_price_usd を取得して JPY 換算。"""
    try:
        res = (
            client
            .table(Table.EBAY_LISTINGS_RAW)
            .select("current_price_usd, buy_it_now_price_usd")
            .eq("item_id", ebay_item_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        price_usd = row.get("current_price_usd") or row.get("buy_it_now_price_usd")
        if price_usd is None:
            return None
        return int(float(price_usd) * fx_rate)
    except Exception as exc:
        logger.warning("_fetch_ebay_price failed: %s", exc)
        return None


def _fetch_global_lot_price(
    client, global_lot_id: str, fx_rate: float
) -> Optional[int]:
    """global_auction_lots から estimate_low_usd を取得して JPY 換算。"""
    try:
        res = (
            client
            .table(Table.GLOBAL_AUCTION_LOTS)
            .select("current_price_usd, estimate_low_usd")
            .eq("id", global_lot_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        price_usd = row.get("current_price_usd") or row.get("estimate_low_usd")
        if price_usd is None:
            return None
        return int(float(price_usd) * fx_rate)
    except Exception as exc:
        logger.warning("_fetch_global_lot_price failed: %s", exc)
        return None


def _get_fx_rate(client) -> float:
    try:
        res = (
            client
            .table("daily_rates")
            .select("usd_jpy")
            .order("rate_date", desc=True)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("usd_jpy"):
            return float(res.data[0]["usd_jpy"])
    except Exception:
        pass
    return _FX_FALLBACK


# ================================================================
# 実行結果
# ================================================================

@dataclass
class WatchRunResult:
    items_checked:  int = 0
    items_updated:  int = 0
    bid_ready_count: int = 0
    ended_count:    int = 0
    error_count:    int = 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.items_updated > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def run_keep_watch(
    *,
    dry_run: bool = False,
    limit: int = 100,
) -> WatchRunResult:
    """
    ACTIVE な watchlist アイテムを一巡して状態を更新する。
    """
    result   = WatchRunResult()
    client   = get_client()
    fx_rate  = _get_fx_rate(client)
    now      = datetime.now(timezone.utc)

    items = load_active_watchlist(client, limit=limit)
    result.items_checked = len(items)

    for item in items:
        try:
            wid          = item["id"]
            ebay_item_id = item.get("ebay_item_id")
            global_lot_id = item.get("global_lot_id")
            max_bid_jpy  = item.get("max_bid_jpy")
            end_at_raw   = item.get("auction_end_at")

            # 終了時刻のパース
            auction_end_at: Optional[datetime] = None
            if end_at_raw:
                try:
                    auction_end_at = datetime.fromisoformat(
                        end_at_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # 現在価格取得
            current_price_jpy: Optional[int] = None
            if ebay_item_id:
                current_price_jpy = _fetch_ebay_price(client, ebay_item_id, fx_rate)
            elif global_lot_id:
                current_price_jpy = _fetch_global_lot_price(
                    client, str(global_lot_id), fx_rate
                )

            # 残時間
            time_left = calc_time_left_seconds(auction_end_at, now)

            # 状態決定
            new_status = determine_watch_status(
                now               = now,
                auction_end_at    = auction_end_at,
                current_price_jpy = current_price_jpy,
                max_bid_jpy       = max_bid_jpy,
                time_left_seconds = time_left,
            )

            next_refresh = calc_next_refresh_at(now, time_left)
            is_bid_ready = (new_status == WatchStatus.BID_READY)

            if dry_run:
                logger.info(
                    "[DRY-RUN] %s: %s → %s price=%s time_left=%s",
                    wid, item.get("status"), new_status,
                    current_price_jpy, time_left,
                )
                result.items_updated += 1
            else:
                ok = update_watchlist_status(
                    client,
                    wid,
                    status              = new_status,
                    current_price_jpy   = current_price_jpy,
                    time_left_seconds   = time_left,
                    is_bid_ready        = is_bid_ready,
                    bid_ready_reason    = "price_ok_within_1h" if is_bid_ready else None,
                    next_refresh_at     = next_refresh,
                    refresh_interval_seconds = WatchCadence.for_time_left(time_left),
                    last_refreshed_at   = now.isoformat(),
                )
                if ok:
                    result.items_updated += 1
                    # snapshot 保存
                    save_watchlist_snapshot(
                        client,
                        wid,
                        price_jpy         = current_price_jpy,
                        time_left_seconds = time_left,
                        is_active         = new_status not in WatchStatus.TERMINAL,
                    )
                else:
                    result.error_count += 1

            if new_status == WatchStatus.BID_READY:
                result.bid_ready_count += 1
            if new_status == WatchStatus.ENDED:
                result.ended_count += 1

        except Exception as exc:
            logger.error("keep_watch error for item %s: %s", item.get("id"), exc)
            result.error_count += 1

    if not dry_run:
        record_keep_watch_run(
            client,
            status          = result.status_str(),
            items_checked   = result.items_checked,
            items_updated   = result.items_updated,
            bid_ready_count = result.bid_ready_count,
            ended_count     = result.ended_count,
            error_count     = result.error_count,
        )

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="KEEP watchlist の ACTIVE アイテムを更新する"
    )
    parser.add_argument("--limit",   type=int, default=100,
                        help="処理件数上限 (default: 100)")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 書き込みなし (確認用)")
    args = parser.parse_args()

    result = run_keep_watch(dry_run=args.dry_run, limit=args.limit)
    print(
        f"keep_watch done: checked={result.items_checked} "
        f"updated={result.items_updated} bid_ready={result.bid_ready_count} "
        f"ended={result.ended_count} errors={result.error_count} "
        f"status={result.status_str()}"
    )


if __name__ == "__main__":
    main()
