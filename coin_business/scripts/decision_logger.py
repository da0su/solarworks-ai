# scripts/decision_logger.py
# CEO判断を candidate_decisions（正本）に保存し、
# トリガー経由で daily_candidates を自動同期する
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.business_rules import VALID_DECISIONS
from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)


def save_ceo_decision(
    *,
    candidate_id: str,
    decision: str,
    reason_code: Optional[str] = None,
    decision_note: Optional[str] = None,
    decided_by: str = "ceo",
    source_screen: str = "dashboard",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    CEO判断を candidate_decisions テーブルに insert する。
    DBトリガーが daily_candidates を自動更新する。

    Returns:
        挿入された行（dict）
    Raises:
        ValueError: decision が不正値
        RuntimeError: DB保存失敗
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision '{decision}'. Must be one of: {VALID_DECISIONS}"
        )

    client = get_client()

    payload: Dict[str, Any] = {
        "candidate_id": str(candidate_id),
        "decision":      decision,
        "reason_code":   reason_code,
        "decision_note": decision_note,
        "decided_by":    decided_by,
        "source_screen": source_screen,
        "metadata":      metadata or {},
    }

    try:
        result = client.table("candidate_decisions").insert(payload).execute()
        if not result.data:
            raise RuntimeError("Insert returned no data")
        row = result.data[0]
        logger.info(
            "CEO decision saved: candidate_id=%s decision=%s",
            candidate_id, decision
        )
        return row
    except Exception as exc:
        logger.error(
            "Failed to save CEO decision: candidate_id=%s error=%s",
            candidate_id, exc
        )
        raise RuntimeError(f"DB保存失敗: {exc}") from exc


def get_latest_decision(candidate_id: str) -> Optional[Dict[str, Any]]:
    """
    指定候補の最新判断を返す（なければ None）
    """
    client = get_client()
    result = (
        client.table("candidate_decisions")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("decided_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_decision_history(
    candidate_id: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """
    指定候補の判断履歴を新しい順で返す
    """
    client = get_client()
    result = (
        client.table("candidate_decisions")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("decided_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_pending_candidates(limit: int = 100) -> List[Dict[str, Any]]:
    """
    CEO判断待ち候補一覧（decision_status が NULL or 'pending'）
    """
    client = get_client()
    result = (
        client.table("daily_candidates")
        .select("*")
        .or_("decision_status.is.null,decision_status.eq.pending")
        .order("priority_score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_approved_candidates(limit: int = 50) -> List[Dict[str, Any]]:
    """
    承認済み候補一覧（入札キュー用）
    """
    client = get_client()
    result = (
        client.table("daily_candidates")
        .select("*")
        .eq("decision_status", "approved")
        .order("decision_last_updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
