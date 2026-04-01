# scripts/status_refresher.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from config.business_rules import DEFAULT_STALE_HOURS
from scripts.supabase_client import get_client


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def get_candidate_by_id(candidate_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("daily_candidates")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def insert_status_check(
    *,
    candidate_id: str,
    is_active: Optional[bool],
    is_sold: Optional[bool],
    current_price: Optional[float],
    source_currency: Optional[str],
    shipping_from_country: Optional[str],
    lot_size: Optional[int],
    raw_snapshot_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    client = get_client()

    payload = {
        "candidate_id": str(candidate_id),
        "checked_at": _utcnow_iso(),
        "is_active": is_active,
        "is_sold": is_sold,
        "current_price": current_price,
        "source_currency": source_currency,
        "shipping_from_country": shipping_from_country,
        "lot_size": lot_size,
        "raw_snapshot_json": raw_snapshot_json or {},
    }

    result = client.table("candidate_status_checks").insert(payload).execute()
    return result.data[0] if result.data else {}


def sync_daily_candidate_current_status(
    *,
    candidate_id: str,
    is_active: Optional[bool],
    is_sold: Optional[bool],
    current_price: Optional[float],
    source_currency: Optional[str],
    shipping_from_country: Optional[str],
    lot_size: Optional[int],
) -> None:
    client = get_client()

    update_payload: Dict[str, Any] = {
        "last_status_checked_at": _utcnow_iso(),
    }
    # None でないフィールドだけ更新（既存値を上書きしない）
    if is_active is not None:
        update_payload["is_active"] = is_active
    if is_sold is not None:
        update_payload["is_sold"] = is_sold
    if current_price is not None:
        update_payload["current_price"] = current_price
    if source_currency is not None:
        update_payload["source_currency"] = source_currency
    if shipping_from_country is not None:
        update_payload["shipping_from_country"] = shipping_from_country
    if lot_size is not None:
        update_payload["lot_size"] = lot_size

    client.table("daily_candidates").update(update_payload).eq("id", str(candidate_id)).execute()


def refresh_candidate_status(
    *,
    candidate_id: str,
    is_active: Optional[bool],
    is_sold: Optional[bool],
    current_price: Optional[float],
    source_currency: Optional[str],
    shipping_from_country: Optional[str],
    lot_size: Optional[int],
    raw_snapshot_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Day4時点の初版:
    外部取得ロジックとは切り離し、まずは status を保存・同期する。
    将来 Playwright / scraper からこの関数を呼ぶ。
    """
    status_row = insert_status_check(
        candidate_id=candidate_id,
        is_active=is_active,
        is_sold=is_sold,
        current_price=current_price,
        source_currency=source_currency,
        shipping_from_country=shipping_from_country,
        lot_size=lot_size,
        raw_snapshot_json=raw_snapshot_json,
    )

    sync_daily_candidate_current_status(
        candidate_id=candidate_id,
        is_active=is_active,
        is_sold=is_sold,
        current_price=current_price,
        source_currency=source_currency,
        shipping_from_country=shipping_from_country,
        lot_size=lot_size,
    )

    return status_row


def get_latest_status_check(candidate_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()

    result = (
        client.table("candidate_status_checks")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("checked_at", desc=True)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]
    return None


def is_stale(last_status_checked_at: Any, stale_hours: int = DEFAULT_STALE_HOURS) -> bool:
    checked_dt = _parse_dt(last_status_checked_at)
    if checked_dt is None:
        return True

    if checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=timezone.utc)

    now_dt = datetime.now(timezone.utc)
    return checked_dt < now_dt - timedelta(hours=stale_hours)


def get_candidate_status_flags(candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    last_checked = candidate_row.get("last_status_checked_at")
    active = _safe_bool(candidate_row.get("is_active"))
    sold = _safe_bool(candidate_row.get("is_sold"))
    currency = candidate_row.get("source_currency")
    ship_from = candidate_row.get("shipping_from_country")
    lot_size = _safe_int(candidate_row.get("lot_size"))

    return {
        "is_stale": is_stale(last_checked),
        "is_active": active,
        "is_sold": sold,
        "source_currency": currency,
        "shipping_from_country": ship_from,
        "lot_size": lot_size,
        "last_status_checked_at": last_checked,
    }


def bulk_refresh_status_from_rows(status_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    例:
    [
      {
        "candidate_id": "1",
        "is_active": True,
        "is_sold": False,
        "current_price": 123.45,
        "source_currency": "USD",
        "shipping_from_country": "US",
        "lot_size": 1,
        "raw_snapshot_json": {...}
      }
    ]
    """
    results = []
    for row in status_rows:
        result = refresh_candidate_status(
            candidate_id=str(row["candidate_id"]),
            is_active=_safe_bool(row.get("is_active")),
            is_sold=_safe_bool(row.get("is_sold")),
            current_price=_safe_float(row.get("current_price")),
            source_currency=row.get("source_currency"),
            shipping_from_country=row.get("shipping_from_country"),
            lot_size=_safe_int(row.get("lot_size")),
            raw_snapshot_json=row.get("raw_snapshot_json") or {},
        )
        results.append(result)
    return results
