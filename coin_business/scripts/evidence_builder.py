# scripts/evidence_builder.py
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_client


EVIDENCE_GROUP_ORDER = [
    "source_listing",
    "cert_verification",
    "yahoo_comp",
    "heritage_comp",
    "spink_comp",
    "numista_ref",
    "image",
    "other",
]


def get_candidate_evidence(candidate_id: str) -> List[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("candidate_evidence")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


def group_candidate_evidence(candidate_id: str) -> Dict[str, List[Dict[str, Any]]]:
    rows = get_candidate_evidence(candidate_id)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for row in rows:
        grouped[row.get("evidence_type") or "other"].append(row)

    # 順序固定
    ordered = {key: grouped.get(key, []) for key in EVIDENCE_GROUP_ORDER}
    return ordered


def evidence_summary(candidate_id: str) -> Dict[str, int]:
    grouped = group_candidate_evidence(candidate_id)
    return {k: len(v) for k, v in grouped.items() if len(v) > 0}


def upsert_candidate_evidence(
    *,
    candidate_id: str,
    evidence_type: str,
    evidence_url: str,
    title: Optional[str] = None,
    meta_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    client = get_client()
    payload = {
        "candidate_id": str(candidate_id),
        "evidence_type": evidence_type,
        "evidence_url": evidence_url,
        "title": title,
        "meta_json": meta_json or {},
    }
    result = client.table("candidate_evidence").insert(payload).execute()
    refresh_candidate_evidence_count(candidate_id)
    return result.data[0] if result.data else {}


def refresh_candidate_evidence_count(candidate_id: str) -> int:
    client = get_client()
    rows = get_candidate_evidence(candidate_id)
    count = len(rows)

    client.table("daily_candidates").update(
        {"evidence_count": count}
    ).eq("id", candidate_id).execute()

    return count
