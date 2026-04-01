# scripts/review_queue.py  — Day7 完成版
"""
CEO確認キューへの candidate 投入・取得・ペイロード生成。
dashboard の CEO確認タブが consume する。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_supabase_client
from scripts.evidence_builder import (
    get_candidate_evidence,
    group_candidate_evidence,
    evidence_summary,
)
from scripts.eligibility_rules import evaluate_candidate_eligibility


# ────────────────────────────────────────────
# Queue query helpers
# ────────────────────────────────────────────

def get_review_queue(
    *,
    limit: int = 100,
    source: Optional[str] = None,
    min_evidence_count: int = 0,
) -> List[Dict[str, Any]]:
    """
    CEO確認待ちの候補を返す。
    条件:
      - ceo_decision IS NULL or 'pending'
      - is_active IS TRUE  (出品中)
      - is_sold IS FALSE
    """
    supabase = get_supabase_client()
    # 'ng' は旧パイプライン自動判定。新システムでCEOが再確認するためpendingと同等扱い
    q = (
        supabase.table("daily_candidates")
        .select("*")
        .or_("ceo_decision.is.null,ceo_decision.eq.pending,ceo_decision.eq.ng")
        .eq("is_active", True)
        .eq("is_sold", False)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if source:
        q = q.eq("source", source)

    rows = q.execute().data or []

    if min_evidence_count > 0:
        rows = [r for r in rows if (r.get("evidence_count") or 0) >= min_evidence_count]

    return rows


def get_approved_queue(limit: int = 50) -> List[Dict[str, Any]]:
    """CEOが承認済みで未入札の候補"""
    supabase = get_supabase_client()
    return (
        supabase.table("daily_candidates")
        .select("*")
        .eq("ceo_decision", "approved")
        .eq("is_active", True)
        .eq("is_sold", False)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


def get_rejected_queue(limit: int = 100) -> List[Dict[str, Any]]:
    """CEOがNGにした候補"""
    supabase = get_supabase_client()
    return (
        supabase.table("daily_candidates")
        .select("*")
        .eq("ceo_decision", "rejected")
        .order("ceo_decided_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


def get_auto_review_queue(limit: int = 100) -> List[Dict[str, Any]]:
    """eligibility_rules で AUTO_REVIEW に分類された候補"""
    supabase = get_supabase_client()
    return (
        supabase.table("daily_candidates")
        .select("*")
        .eq("auto_tier", "AUTO_REVIEW")
        .or_("ceo_decision.is.null,ceo_decision.eq.pending")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


# ────────────────────────────────────────────
# auto_tier sync helper
# ────────────────────────────────────────────

def refresh_auto_tier_for_candidate(candidate_id: str) -> str:
    """
    eligibility_rules を再評価して daily_candidates.auto_tier を更新する。
    戻り値: auto_tier 文字列 ('AUTO_PASS' / 'AUTO_REVIEW' / 'AUTO_REJECT')
    """
    supabase = get_supabase_client()
    row_res = (
        supabase.table("daily_candidates")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    rows = row_res.data or []
    if not rows:
        raise ValueError(f"candidate not found: {candidate_id}")

    row = rows[0]
    evaluation = evaluate_candidate_eligibility(row)
    tier = evaluation.auto_tier

    supabase.table("daily_candidates").update(
        {
            "auto_tier": tier,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", candidate_id).execute()

    return tier


def bulk_refresh_auto_tiers(limit: int = 500) -> Dict[str, int]:
    """全候補の auto_tier を一括更新する"""
    supabase = get_supabase_client()
    rows = (
        supabase.table("daily_candidates")
        .select("*")
        .limit(limit)
        .execute()
        .data or []
    )
    counts: Dict[str, int] = {"AUTO_PASS": 0, "AUTO_REVIEW": 0, "AUTO_REJECT": 0}
    for row in rows:
        try:
            tier = refresh_auto_tier_for_candidate(str(row["id"]))
            counts[tier] = counts.get(tier, 0) + 1
        except Exception:
            pass
    return counts


# ────────────────────────────────────────────
# CEO Review Payload
# ────────────────────────────────────────────

def compose_ceo_review_payload(
    candidate_id: str,
    *,
    include_evidence: bool = True,
    include_pricing: bool = True,
    include_eligibility: bool = True,
) -> Dict[str, Any]:
    """
    dashboard の CEO確認カードが使う全情報パッケージ。
    Supabase から candidate + evidence + pricing + eligibility を集約して返す。
    """
    supabase = get_supabase_client()
    row_res = (
        supabase.table("daily_candidates")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    rows = row_res.data or []
    if not rows:
        raise ValueError(f"candidate not found: {candidate_id}")
    candidate = rows[0]

    payload: Dict[str, Any] = {"candidate": candidate}

    # ── Evidence ──
    if include_evidence:
        evidence_rows = get_candidate_evidence(candidate_id)
        payload["evidence_grouped"] = group_candidate_evidence(evidence_rows)
        payload["evidence_summary"] = evidence_summary(evidence_rows)
        payload["evidence_total"] = len(evidence_rows)

    # ── Pricing snapshot ──
    if include_pricing:
        snap_res = (
            supabase.table("candidate_pricing_snapshots")
            .select("*")
            .eq("candidate_id", candidate_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        payload["pricing_snapshot"] = (snap_res.data or [None])[0]

    # ── Eligibility ──
    if include_eligibility:
        evaluation = evaluate_candidate_eligibility(candidate)
        payload["eligibility"] = {
            "auto_tier": evaluation.auto_tier,
            "hard_fail_codes": evaluation.hard_fail_codes,
            "warning_codes": evaluation.warning_codes,
            "approval_blocked": evaluation.approval_blocked,
        }

    return payload


def compose_review_queue_payloads(
    *,
    limit: int = 50,
    source: Optional[str] = None,
    min_evidence_count: int = 0,
    include_evidence: bool = True,
    include_pricing: bool = True,
    include_eligibility: bool = True,
) -> List[Dict[str, Any]]:
    """
    review キュー全体を一括でペイロード化する。
    dashboard が初期ロードで呼ぶユースケース。
    """
    candidates = get_review_queue(
        limit=limit,
        source=source,
        min_evidence_count=min_evidence_count,
    )
    result = []
    for c in candidates:
        try:
            payload = compose_ceo_review_payload(
                str(c["id"]),
                include_evidence=include_evidence,
                include_pricing=include_pricing,
                include_eligibility=include_eligibility,
            )
            result.append(payload)
        except Exception:
            result.append({"candidate": c})
    return result


# ────────────────────────────────────────────
# Queue stats
# ────────────────────────────────────────────

def get_queue_summary() -> Dict[str, int]:
    """CEO確認キューの件数サマリー"""
    supabase = get_supabase_client()
    all_res = (
        supabase.table("daily_candidates")
        .select("ceo_decision, auto_tier", count="exact")
        .execute()
    )
    rows = all_res.data or []

    summary = {
        "total": len(rows),
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "auto_pass": 0,
        "auto_review": 0,
        "auto_reject": 0,
    }
    for r in rows:
        decision = (r.get("ceo_decision") or "pending").lower()
        tier = (r.get("auto_tier") or "").upper()
        if decision in ("pending", ""):
            summary["pending"] += 1
        elif decision == "approved":
            summary["approved"] += 1
        elif decision == "rejected":
            summary["rejected"] += 1
        if tier == "AUTO_PASS":
            summary["auto_pass"] += 1
        elif tier == "AUTO_REVIEW":
            summary["auto_review"] += 1
        elif tier == "AUTO_REJECT":
            summary["auto_reject"] += 1
    return summary
