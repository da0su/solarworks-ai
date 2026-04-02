"""
coin_business/db/global_repo.py
==================================
世界オークション event / lot / snapshot のリポジトリ層。

責務:
  - upsert_event()              : global_auction_events に upsert
  - upsert_lot()                : global_auction_lots に upsert
  - insert_lot_snapshot()       : global_lot_price_snapshots に INSERT
  - load_upcoming_events()      : 監視対象 event 一覧
  - load_events_due_for_ingest(): next_ingest 閾値を過ぎた event
  - update_event_t_minus()      : t_minus_stage + last_synced_at 更新
  - load_lots_for_event()       : event に紐づく lot 一覧
  - record_sync_run()           : job_global_auction_sync_daily に記録
  - record_ingest_run()         : job_global_lot_ingest_daily に記録

設計原則:
  - event は (auction_house, event_id_external) で UPSERT
  - lot は (event_id, lot_id_external) で UPSERT
  - snapshot は常に INSERT (時系列追跡)
  - API エラー時は例外を外に出さず False / None / [] を返す
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from constants import Table, TMinusCadence, TMinusStage

logger = logging.getLogger(__name__)

# ================================================================
# テーブル名
# ================================================================

EVENTS_TABLE    = Table.GLOBAL_AUCTION_EVENTS    # "global_auction_events"
LOTS_TABLE      = Table.GLOBAL_AUCTION_LOTS      # "global_auction_lots"
SNAPS_TABLE     = Table.GLOBAL_LOT_SNAPSHOTS     # "global_lot_price_snapshots"
JOB_SYNC        = Table.JOB_GLOBAL_SYNC          # "job_global_auction_sync_daily"
JOB_INGEST      = Table.JOB_GLOBAL_INGEST        # "job_global_lot_ingest_daily"


# ================================================================
# event 管理
# ================================================================

def upsert_event(client, event: dict) -> Optional[str]:
    """
    global_auction_events に event を upsert する。

    dedup key: (auction_house, event_id_external)

    Args:
        client: Supabase クライアント
        event:  event フィールド dict (fetcher が返す形式)

    Returns:
        保存された UUID 文字列、失敗時は None
    """
    auction_house = event.get("auction_house", "")
    ext_id        = event.get("event_id_external", "")
    if not auction_house or not ext_id:
        logger.warning("auction_house または event_id_external が空 — スキップ")
        return None

    safe_fields = [
        "auction_house", "event_name", "event_url", "event_id_external",
        "auction_date", "auction_start_at", "auction_end_at",
        "location", "is_online", "coin_lot_count", "total_lot_count",
        "t_minus_stage", "status",
    ]
    rec: dict = {f: event[f] for f in safe_fields if f in event and event[f] is not None}
    rec["last_synced_at"] = datetime.now(timezone.utc).isoformat()

    try:
        resp = client.table(EVENTS_TABLE).upsert(
            rec,
            on_conflict="auction_house,event_id_external",
        ).execute()
        if resp.data:
            return resp.data[0].get("id")
        return None
    except Exception as exc:
        logger.error("global_auction_events upsert 失敗 %s/%s: %s",
                     auction_house, ext_id, exc)
        return None


def load_upcoming_events(
    client,
    limit:    int = 100,
    statuses: list[str] | None = None,
) -> list[dict]:
    """
    監視対象の event を取得する。

    Args:
        limit:    最大取得件数
        statuses: フィルタするステータス (None = upcoming + active)

    Returns:
        list of global_auction_events レコード
    """
    if statuses is None:
        statuses = ["upcoming", "active"]
    try:
        resp = (
            client.table(EVENTS_TABLE)
            .select(
                "id, auction_house, event_name, event_url, event_id_external, "
                "auction_date, auction_start_at, auction_end_at, "
                "t_minus_stage, status, last_synced_at, coin_lot_count"
            )
            .in_("status", statuses)
            .order("auction_date", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_upcoming_events 失敗: %s", exc)
        return []


def load_events_due_for_ingest(client, limit: int = 20) -> list[dict]:
    """
    次回 ingest が必要な event を返す。

    判定: last_synced_at + cadence_hours <= NOW()
    t_minus_stage が NULL の event は対象外。

    Returns:
        list of global_auction_events レコード (lot ingest が必要なもの)
    """
    now = datetime.now(timezone.utc)
    try:
        resp = (
            client.table(EVENTS_TABLE)
            .select(
                "id, auction_house, event_name, event_id_external, "
                "auction_date, auction_end_at, t_minus_stage, "
                "status, last_synced_at, coin_lot_count"
            )
            .in_("status", ["upcoming", "active"])
            .not_.is_("t_minus_stage", "null")
            .order("auction_date", desc=False)
            .limit(limit * 3)   # 多めに取って Python 側でフィルタ
            .execute()
        )
        events = resp.data or []
    except Exception as exc:
        logger.error("load_events_due_for_ingest 失敗: %s", exc)
        return []

    # 次回 ingest 時刻をチェック
    due = []
    for ev in events:
        stage          = ev.get("t_minus_stage")
        last_synced_str = ev.get("last_synced_at")
        interval_h     = TMinusCadence.interval_hours(stage)

        if last_synced_str is None:
            due.append(ev)   # 未 ingest → 即実行
            continue

        try:
            last_synced = datetime.fromisoformat(
                str(last_synced_str).replace("Z", "+00:00")
            )
            if now >= last_synced + timedelta(hours=interval_h):
                due.append(ev)
        except (ValueError, TypeError):
            due.append(ev)   # パース不可 → 安全側に倒して実行

        if len(due) >= limit:
            break

    return due


def update_event_t_minus(
    client,
    event_id:       str,
    t_minus_stage:  int | None,
    last_synced_at: str | None = None,
) -> bool:
    """event の t_minus_stage と last_synced_at を更新する。"""
    update: dict = {"t_minus_stage": t_minus_stage}
    update["last_synced_at"] = last_synced_at or datetime.now(timezone.utc).isoformat()
    try:
        client.table(EVENTS_TABLE).update(update).eq("id", event_id).execute()
        return True
    except Exception as exc:
        logger.error("update_event_t_minus 失敗 event_id=%s: %s", event_id, exc)
        return False


# ================================================================
# lot 管理
# ================================================================

def upsert_lot(client, event_id: str, lot: dict) -> Optional[str]:
    """
    global_auction_lots に lot を upsert する。

    dedup key: (event_id, lot_id_external)

    Args:
        client:   Supabase クライアント
        event_id: global_auction_events.id (UUID)
        lot:      lot フィールド dict (fetcher が返す形式)

    Returns:
        保存された UUID 文字列、失敗時は None
    """
    ext_id = lot.get("lot_id_external", "")
    if not ext_id:
        logger.warning("lot_id_external が空 — スキップ event=%s", event_id)
        return None

    safe_fields = [
        "lot_number", "lot_url", "lot_id_external", "lot_title",
        "year", "country", "denomination", "grade", "grade_text",
        "grader", "cert_company", "cert_number",
        "estimate_low_usd", "estimate_high_usd",
        "current_bid_usd", "currency",
        "image_url", "thumbnail_url",
        "lot_end_at", "status",
    ]
    rec: dict = {f: lot[f] for f in safe_fields if f in lot and lot[f] is not None}
    rec["event_id"]          = event_id
    rec["last_refreshed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        resp = client.table(LOTS_TABLE).upsert(
            rec,
            on_conflict="event_id,lot_id_external",
        ).execute()
        if resp.data:
            return resp.data[0].get("id")
        return None
    except Exception as exc:
        logger.error("global_auction_lots upsert 失敗 event=%s lot=%s: %s",
                     event_id, ext_id, exc)
        return None


def load_lots_for_event(client, event_id: str) -> list[dict]:
    """event に紐づく全 lot を取得する。"""
    try:
        resp = (
            client.table(LOTS_TABLE)
            .select(
                "id, lot_number, lot_id_external, lot_title, "
                "estimate_low_usd, estimate_high_usd, current_bid_usd, "
                "currency, lot_end_at, status, last_refreshed_at"
            )
            .eq("event_id", event_id)
            .order("lot_number", desc=False)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_lots_for_event 失敗 event_id=%s: %s", event_id, exc)
        return []


# ================================================================
# snapshot 管理
# ================================================================

def insert_lot_snapshot(
    client,
    lot_id:          str,
    current_bid_usd: float | None,
    bid_count:       int | None,
    lot_end_at:      str | None,
    prev_bid_usd:    float | None = None,
) -> bool:
    """
    global_lot_price_snapshots に 1 行を INSERT する。

    常に INSERT (UPSERT なし)。時系列追跡が目的。

    Args:
        client:          Supabase クライアント
        lot_id:          global_auction_lots.id (UUID)
        current_bid_usd: 現在の入札額
        bid_count:       入札数
        lot_end_at:      lot 終了日時 (ISO string)
        prev_bid_usd:    前回の入札額 (bid_delta 計算用、省略可)

    Returns:
        True = 成功
    """
    snap: dict = {"lot_id": lot_id}

    if current_bid_usd is not None:
        snap["current_bid_usd"] = round(float(current_bid_usd), 2)
    if bid_count is not None:
        snap["bid_count"] = int(bid_count)

    # 残時間 (lot_end_at から計算)
    if lot_end_at:
        try:
            end_dt = datetime.fromisoformat(str(lot_end_at).replace("Z", "+00:00"))
            now    = datetime.now(timezone.utc)
            delta  = (end_dt - now).total_seconds() / 3600  # hours
            snap["time_left_hours"] = round(max(0.0, delta), 2)
        except (ValueError, TypeError):
            pass

    # 前回比差分
    if prev_bid_usd is not None and current_bid_usd is not None:
        try:
            snap["bid_delta"] = round(float(current_bid_usd) - float(prev_bid_usd), 2)
        except (TypeError, ValueError):
            pass

    try:
        client.table(SNAPS_TABLE).insert(snap).execute()
        return True
    except Exception as exc:
        logger.error("global_lot_price_snapshots insert 失敗 lot_id=%s: %s", lot_id, exc)
        return False


# ================================================================
# ジョブ記録
# ================================================================

def record_sync_run(
    client,
    run_date:      str,
    status:        str,
    events_synced: int = 0,
    events_new:    int = 0,
    error_count:   int = 0,
    error_message: Optional[str] = None,
) -> bool:
    """job_global_auction_sync_daily に sync 実行記録を insert する。"""
    try:
        rec: dict = {
            "run_date":      run_date,
            "status":        status,
            "events_synced": events_synced,
            "events_new":    events_new,
            "error_count":   error_count,
        }
        if error_message:
            rec["error_message"] = error_message[:2000]
        client.table(JOB_SYNC).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("sync ジョブ記録失敗: %s", exc)
        return False


def record_ingest_run(
    client,
    run_date:         str,
    status:           str,
    events_processed: int = 0,
    lots_fetched:     int = 0,
    lots_saved:       int = 0,
    snapshots_saved:  int = 0,
    error_count:      int = 0,
    error_message:    Optional[str] = None,
) -> bool:
    """job_global_lot_ingest_daily に ingest 実行記録を insert する。"""
    try:
        rec: dict = {
            "run_date":         run_date,
            "status":           status,
            "events_processed": events_processed,
            "lots_fetched":     lots_fetched,
            "lots_saved":       lots_saved,
            "snapshots_saved":  snapshots_saved,
            "error_count":      error_count,
        }
        if error_message:
            rec["error_message"] = error_message[:2000]
        client.table(JOB_INGEST).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("ingest ジョブ記録失敗: %s", exc)
        return False
