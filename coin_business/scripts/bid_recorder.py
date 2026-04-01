# scripts/bid_recorder.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_client
from scripts.eligibility_rules import AUTO_REJECT, evaluate_candidate_eligibility


VALID_BID_STATUSES = {
    "queued",
    "submitted",
    "won",
    "lost",
    "cancelled",
    "failed",
}


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def get_candidate_by_id(candidate_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("daily_candidates")
        .select("*")
        .eq("id", str(candidate_id))
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def can_queue_candidate_for_bid(candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Day5時点の入札キュー投入可否。
    条件:
    - AUTO_REJECT ではない
    - ceo_decision == approved
    - recommended_max_bid_jpy があることが望ましい
    """
    evaluation = evaluate_candidate_eligibility(candidate_row)

    reasons: List[str] = []

    if evaluation.auto_tier == AUTO_REJECT:
        reasons.append("AUTO_REJECT candidate cannot be queued")

    ceo_decision = str(
        candidate_row.get("ceo_decision") or candidate_row.get("decision_status") or ""
    ).strip().lower()
    if ceo_decision != "approved":
        reasons.append("candidate is not CEO-approved")

    recommended_max_bid_jpy = _safe_float(candidate_row.get("recommended_max_bid_jpy"))
    if recommended_max_bid_jpy is None:
        reasons.append("recommended_max_bid_jpy missing")

    return {
        "can_queue": len([r for r in reasons if r != "recommended_max_bid_jpy missing"]) == 0,
        "reasons": reasons,
        "evaluation": evaluation,
    }


def create_bid_record(
    *,
    candidate_id: str,
    approved_by: str,
    bid_max_jpy: float,
    bid_currency: Optional[str] = None,
    bid_amount_source: Optional[float] = None,
    bid_status: str = "queued",
    external_ref: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    if bid_status not in VALID_BID_STATUSES:
        raise ValueError(f"Invalid bid_status: {bid_status}")

    client = get_client()
    payload = {
        "candidate_id": str(candidate_id),
        "approved_by": approved_by,
        "bid_max_jpy": bid_max_jpy,
        "bid_currency": bid_currency,
        "bid_amount_source": bid_amount_source,
        "bid_status": bid_status,
        "external_ref": external_ref,
        "note": note,
    }

    result = client.table("bidding_records").insert(payload).execute()
    return result.data[0] if result.data else {}


def queue_candidate_for_bid(
    *,
    candidate_id: str,
    approved_by: str = "ceo",
    bid_max_jpy: Optional[float] = None,
    bid_currency: Optional[str] = None,
    bid_amount_source: Optional[float] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    candidate_row = get_candidate_by_id(candidate_id)
    if not candidate_row:
        raise ValueError(f"Candidate not found: {candidate_id}")

    check = can_queue_candidate_for_bid(candidate_row)
    if not check["can_queue"]:
        raise ValueError(f"Candidate cannot be queued: {check['reasons']}")

    final_bid_max_jpy = bid_max_jpy
    if final_bid_max_jpy is None:
        final_bid_max_jpy = _safe_float(candidate_row.get("recommended_max_bid_jpy"))

    if final_bid_max_jpy is None:
        raise ValueError("bid_max_jpy is required")

    final_bid_currency = bid_currency or candidate_row.get("source_currency") or "USD"

    return create_bid_record(
        candidate_id=str(candidate_id),
        approved_by=approved_by,
        bid_max_jpy=float(final_bid_max_jpy),
        bid_currency=final_bid_currency,
        bid_amount_source=_safe_float(bid_amount_source),
        bid_status="queued",
        note=note,
    )


def list_bid_records(limit: int = 300, status: Optional[str] = None) -> List[Dict[str, Any]]:
    client = get_client()
    query = client.table("bidding_records").select("*").limit(limit)

    if status:
        query = query.eq("bid_status", status)

    result = query.execute()
    return result.data or []


def get_bid_record_by_id(record_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("bidding_records")
        .select("*")
        .eq("id", str(record_id))
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def get_latest_bid_record_for_candidate(candidate_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("bidding_records")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def update_bid_record_status(
    *,
    record_id: str,
    bid_status: str,
    external_ref: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    if bid_status not in VALID_BID_STATUSES:
        raise ValueError(f"Invalid bid_status: {bid_status}")

    existing = get_bid_record_by_id(record_id)
    if not existing:
        raise ValueError(f"Bid record not found: {record_id}")

    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    update_payload: Dict[str, Any] = {
        "bid_status": bid_status,
    }

    if external_ref is not None:
        update_payload["external_ref"] = external_ref

    if note is not None:
        update_payload["note"] = note

    if bid_status == "submitted":
        update_payload["submitted_at"] = now_iso
    if bid_status in {"won", "lost", "cancelled", "failed"}:
        update_payload["resolved_at"] = now_iso

    result = (
        client.table("bidding_records")
        .update(update_payload)
        .eq("id", str(record_id))
        .execute()
    )

    return result.data[0] if result.data else {}


def get_bid_records_with_candidate_context(
    limit: int = 300,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    bidding_records と daily_candidates を candidate_id で手動結合して返す。
    """
    bid_rows = list_bid_records(limit=limit, status=status)
    if not bid_rows:
        return []

    candidate_ids = list(
        {str(row["candidate_id"]) for row in bid_rows if row.get("candidate_id") is not None}
    )
    client = get_client()

    candidate_result = (
        client.table("daily_candidates")
        .select("*")
        .in_("id", candidate_ids)
        .execute()
    )
    candidate_map = {str(row["id"]): row for row in (candidate_result.data or [])}

    enriched = []
    for bid in bid_rows:
        candidate = candidate_map.get(str(bid["candidate_id"]), {})
        enriched.append({
            **bid,
            "candidate_title": candidate.get("title") or candidate.get("item_title"),
            "source": candidate.get("source") or candidate.get("source_name") or candidate.get("platform"),
            "projected_profit_jpy": candidate.get("projected_profit_jpy"),
            "projected_roi": candidate.get("projected_roi"),
            "recommended_max_bid_jpy": candidate.get("recommended_max_bid_jpy"),
            "ceo_decision": candidate.get("ceo_decision"),
            "source_currency": candidate.get("source_currency"),
            "cert_number": candidate.get("cert_number"),
            "grader": candidate.get("grader"),
        })
    return enriched


def get_bid_summary() -> Dict[str, int]:
    rows = list_bid_records(limit=1000)
    summary: Dict[str, int] = {
        "queued": 0,
        "submitted": 0,
        "won": 0,
        "lost": 0,
        "cancelled": 0,
        "failed": 0,
    }
    for row in rows:
        status = row.get("bid_status")
        if status in summary:
            summary[status] += 1
    return summary


def list_queueable_candidates(limit: int = 300) -> List[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("daily_candidates")
        .select("*")
        .limit(limit)
        .execute()
    )
    rows = result.data or []

    queueable: List[Dict[str, Any]] = []
    for row in rows:
        check = can_queue_candidate_for_bid(row)
        if check["can_queue"]:
            queueable.append(row)
    return queueable
