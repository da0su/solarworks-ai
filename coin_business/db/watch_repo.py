"""
coin_business/db/watch_repo.py
================================
candidate_watchlist / watchlist_snapshots の CRUD。

責務:
  - load_active_watchlist()      : 監視中アイテム一覧 (next_refresh_at が到来済み)
  - load_watchlist_item()        : 1件取得
  - add_watchlist_entry()        : CEO KEEP → watchlist 登録
  - update_watchlist_status()    : status / 価格 / 次回 refresh 時刻を更新
  - save_watchlist_snapshot()    : 価格スナップショットを保存
  - record_keep_watch_run()      : job_keep_watch_daily に実行履歴を記録
  - record_pricing_run()         : job_pricing_engine_daily に実行履歴を記録

設計原則:
  - API エラーは例外を外に出さず None / False / [] を返す
  - 全テーブル名は constants.Table から参照
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from constants import Table, WatchStatus

logger = logging.getLogger(__name__)


# ================================================================
# watchlist 取得
# ================================================================

def load_active_watchlist(
    client,
    *,
    due_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    """
    ACTIVE ステータス（watching / price_ok / ending_soon / bid_ready）の
    watchlist アイテムを返す。

    due_only=True のとき next_refresh_at <= NOW() のものだけ返す。
    """
    try:
        query = (
            client
            .table(Table.CANDIDATE_WATCHLIST)
            .select("*")
            .in_("status", list(WatchStatus.ACTIVE))
        )
        if due_only:
            now_iso = datetime.now(timezone.utc).isoformat()
            query = query.lte("next_refresh_at", now_iso)

        res = query.order("next_refresh_at").limit(limit).execute()
        return res.data or []
    except Exception as exc:
        logger.error("load_active_watchlist failed: %s", exc)
        return []


def load_watchlist_item(client, watchlist_id: str) -> Optional[dict]:
    """watchlist_id を指定して 1件取得する。"""
    try:
        res = (
            client
            .table(Table.CANDIDATE_WATCHLIST)
            .select("*")
            .eq("id", watchlist_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        logger.error("load_watchlist_item failed: %s", exc)
        return None


# ================================================================
# watchlist 登録
# ================================================================

def add_watchlist_entry(client, entry: dict) -> Optional[str]:
    """
    CEO KEEP 後に watchlist へ登録する。

    entry 必須キー:
        candidate_id, max_bid_jpy, auction_end_at (ISO string)
    オプション:
        ebay_item_id, global_lot_id, max_bid_usd, watch_mode, added_by

    Returns: 挿入された id (UUID string) or None on error
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "status": WatchStatus.WATCHING,
            "refresh_count": 0,
            "added_at": now,
            "created_at": now,
            "updated_at": now,
            **entry,
        }
        res = (
            client
            .table(Table.CANDIDATE_WATCHLIST)
            .insert(rec)
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as exc:
        logger.error("add_watchlist_entry failed: %s", exc)
        return None


# ================================================================
# watchlist 更新
# ================================================================

def update_watchlist_status(
    client,
    watchlist_id: str,
    *,
    status: str,
    current_price_jpy: Optional[int] = None,
    current_price_usd: Optional[float] = None,
    bid_count: Optional[int] = None,
    time_left_seconds: Optional[int] = None,
    is_bid_ready: Optional[bool] = None,
    bid_ready_reason: Optional[str] = None,
    next_refresh_at: Optional[str] = None,
    refresh_interval_seconds: Optional[int] = None,
    last_refreshed_at: Optional[str] = None,
) -> bool:
    """
    watchlist アイテムのステータスと価格情報を更新する。

    Returns True on success, False on error.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        patch: dict = {
            "status": status,
            "updated_at": now,
        }
        if current_price_jpy is not None:
            patch["current_price_jpy"] = current_price_jpy
        if current_price_usd is not None:
            patch["current_price_usd"] = current_price_usd
        if bid_count is not None:
            patch["bid_count"] = bid_count
        if time_left_seconds is not None:
            patch["time_left_seconds"] = time_left_seconds
        if is_bid_ready is not None:
            patch["is_bid_ready"] = is_bid_ready
        if bid_ready_reason is not None:
            patch["bid_ready_reason"] = bid_ready_reason
        if next_refresh_at is not None:
            patch["next_refresh_at"] = next_refresh_at
        if refresh_interval_seconds is not None:
            patch["refresh_interval_seconds"] = refresh_interval_seconds
        if last_refreshed_at is not None:
            patch["last_refreshed_at"] = last_refreshed_at

        # refresh_count は RPC なしでインクリメントできないため +1 相当を
        # 呼び出し側で計算して渡す設計としている (現状は省略)
        (
            client
            .table(Table.CANDIDATE_WATCHLIST)
            .update(patch)
            .eq("id", watchlist_id)
            .execute()
        )
        return True
    except Exception as exc:
        logger.error("update_watchlist_status failed: %s", exc)
        return False


# ================================================================
# snapshot 保存
# ================================================================

def save_watchlist_snapshot(
    client,
    watchlist_id: str,
    *,
    price_jpy: Optional[int] = None,
    price_usd: Optional[float] = None,
    bid_count: Optional[int] = None,
    time_left_seconds: Optional[int] = None,
    is_active: bool = True,
) -> bool:
    """watchlist_snapshots に 1行追加する。"""
    try:
        rec = {
            "watchlist_id": watchlist_id,
            "price_jpy": price_jpy,
            "price_usd": price_usd,
            "bid_count": bid_count,
            "time_left_seconds": time_left_seconds,
            "is_active": is_active,
            "snapped_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(Table.WATCHLIST_SNAPSHOTS).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("save_watchlist_snapshot failed: %s", exc)
        return False


# ================================================================
# job 実行履歴
# ================================================================

def record_pricing_run(
    client,
    *,
    run_date: str,
    status: str,
    candidates_found: int = 0,
    candidates_priced: int = 0,
    error_count: int = 0,
    error_message: Optional[str] = None,
) -> bool:
    """job_pricing_engine_daily に実行履歴を記録する。"""
    try:
        rec = {
            "run_date": run_date,
            "status": status,
            "candidates_found": candidates_found,
            "candidates_priced": candidates_priced,
            "error_count": error_count,
            "error_message": error_message,
        }
        client.table(Table.JOB_PRICING_ENGINE).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("record_pricing_run failed: %s", exc)
        return False


def record_keep_watch_run(
    client,
    *,
    status: str,
    items_checked: int = 0,
    items_updated: int = 0,
    bid_ready_count: int = 0,
    ended_count: int = 0,
    error_count: int = 0,
    error_message: Optional[str] = None,
) -> bool:
    """job_keep_watch_daily に実行履歴を記録する。"""
    try:
        rec = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "items_checked": items_checked,
            "items_updated": items_updated,
            "bid_ready_count": bid_ready_count,
            "ended_count": ended_count,
            "error_count": error_count,
            "error_message": error_message,
        }
        client.table(Table.JOB_KEEP_WATCH).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("record_keep_watch_run failed: %s", exc)
        return False
