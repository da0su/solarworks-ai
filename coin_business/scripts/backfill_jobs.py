# scripts/backfill_jobs.py  — Day8 完成版
"""
既存 daily_candidates (518件) の evidence / pricing / auto_tier を一括バックフィル。
CLI: python -m scripts.backfill_jobs [--dry-run] [--limit N] [--only evidence|pricing|tier]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_supabase_client
from scripts.evidence_builder import build_candidate_evidence_bundle
from scripts.review_queue import refresh_auto_tier_for_candidate


# ────────────────────────────────────────────
# Fetch candidates needing backfill
# ────────────────────────────────────────────

def _fetch_candidates_for_backfill(
    limit: int = 600,
    skip_has_evidence: bool = True,
) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    q = supabase.table("daily_candidates").select("*").limit(limit)
    if skip_has_evidence:
        q = q.or_("evidence_count.is.null,evidence_count.eq.0")
    return q.execute().data or []


def _fetch_candidates_needing_pricing(limit: int = 600) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    # recommended_max_bid_jpy がまだ null の候補
    return (
        supabase.table("daily_candidates")
        .select("*")
        .is_("recommended_max_bid_jpy", "null")
        .limit(limit)
        .execute()
        .data or []
    )


# ────────────────────────────────────────────
# Per-candidate workers
# ────────────────────────────────────────────

def _backfill_evidence_for_one(candidate_id: str, dry_run: bool = False) -> Dict[str, Any]:
    if dry_run:
        return {"candidate_id": candidate_id, "status": "dry_run"}
    try:
        result = build_candidate_evidence_bundle(candidate_id, replace_generated=False)
        return {"candidate_id": candidate_id, "status": "ok", "inserted_count": result.get("inserted_count", 0)}
    except Exception as e:
        return {"candidate_id": candidate_id, "status": "error", "error": str(e)}


def _backfill_pricing_for_one(
    candidate_row: Dict[str, Any],
    market_rows: Optional[List[Dict[str, Any]]],
    dry_run: bool = False,
) -> Dict[str, Any]:
    candidate_id = str(candidate_row.get("id", ""))
    if dry_run:
        return {"candidate_id": candidate_id, "status": "dry_run"}
    try:
        # purchase_price_jpy: buy_limit_jpy or current_price * exchange fallback
        purchase_jpy = _resolve_purchase_jpy(candidate_row)
        if purchase_jpy is None:
            return {"candidate_id": candidate_id, "status": "skip", "reason": "no purchase price"}

        from scripts.pricing_engine import build_and_save_candidate_pricing_snapshot
        snapshot = build_and_save_candidate_pricing_snapshot(
            candidate_row,
            purchase_price_jpy=purchase_jpy,
            import_tax_jpy=0.0,
            market_rows=market_rows,
        )
        return {
            "candidate_id": candidate_id,
            "status": "ok",
            "expected_sale_price_jpy": snapshot.expected_sale_price_jpy,
            "projected_margin": snapshot.projected_margin,
        }
    except Exception as e:
        return {"candidate_id": candidate_id, "status": "error", "error": str(e)}


def _backfill_tier_for_one(candidate_id: str, dry_run: bool = False) -> Dict[str, Any]:
    if dry_run:
        return {"candidate_id": candidate_id, "status": "dry_run"}
    try:
        tier = refresh_auto_tier_for_candidate(candidate_id)
        return {"candidate_id": candidate_id, "status": "ok", "tier": tier}
    except Exception as e:
        return {"candidate_id": candidate_id, "status": "error", "error": str(e)}


def _resolve_purchase_jpy(row: Dict[str, Any]) -> Optional[float]:
    """
    候補行から仕入れ価格（円）を解決する。
    優先: buy_limit_jpy → current_price_jpy → current_price * 150 (USD→JPY rough)
    """
    for key in ("buy_limit_jpy", "estimated_buy_price", "estimated_cost_jpy",
                "current_price_jpy", "price_jpy",
                "ref1_buy_limit_20k_jpy", "ref2_buy_limit_20k_jpy"):
        v = row.get(key)
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except Exception:
                continue

    # 外貨建て価格 × fx_rate で JPY 換算
    fx = row.get("fx_rate")
    for key in ("current_price", "price", "price_usd"):
        v = row.get(key)
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    rate = float(fx) if fx else 150.0
                    return f * rate
            except Exception:
                continue

    return None


# ────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────

def run_evidence_backfill(
    *,
    limit: int = 600,
    dry_run: bool = False,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    candidates = _fetch_candidates_for_backfill(limit=limit, skip_has_evidence=True)
    print(f"[evidence_backfill] {len(candidates)} candidates to process (dry_run={dry_run})")

    results = []
    ok = error = skip = 0
    for i, c in enumerate(candidates):
        cid = str(c["id"])
        result = _backfill_evidence_for_one(cid, dry_run=dry_run)
        results.append(result)
        if result["status"] == "ok":
            ok += 1
        elif result["status"] == "error":
            error += 1
        else:
            skip += 1

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(candidates)}] ok={ok} error={error} skip={skip}")
        if sleep_sec > 0 and not dry_run:
            time.sleep(sleep_sec)

    print(f"[evidence_backfill] done. ok={ok} error={error} skip={skip}")
    return {"ok": ok, "error": error, "skip": skip, "results": results}


def run_pricing_backfill(
    *,
    limit: int = 600,
    dry_run: bool = False,
    sleep_sec: float = 0.3,
) -> Dict[str, Any]:
    from scripts.pricing_engine import fetch_market_transactions
    candidates = _fetch_candidates_needing_pricing(limit=limit)
    print(f"[pricing_backfill] {len(candidates)} candidates to process (dry_run={dry_run})")

    # 市場データは一括 fetch して使い回す
    market_rows = None
    if not dry_run:
        print("  fetching market_transactions...")
        market_rows = fetch_market_transactions(limit=30000)
        print(f"  market_rows={len(market_rows)}")

    results = []
    ok = error = skip = 0
    for i, c in enumerate(candidates):
        result = _backfill_pricing_for_one(c, market_rows, dry_run=dry_run)
        results.append(result)
        if result["status"] == "ok":
            ok += 1
        elif result["status"] == "error":
            error += 1
        else:
            skip += 1

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(candidates)}] ok={ok} error={error} skip={skip}")
        if sleep_sec > 0 and not dry_run:
            time.sleep(sleep_sec)

    print(f"[pricing_backfill] done. ok={ok} error={error} skip={skip}")
    return {"ok": ok, "error": error, "skip": skip, "results": results}


def run_tier_backfill(
    *,
    limit: int = 600,
    dry_run: bool = False,
    sleep_sec: float = 0.1,
) -> Dict[str, Any]:
    supabase = get_supabase_client()
    candidates = (
        supabase.table("daily_candidates")
        .select("id")
        .limit(limit)
        .execute()
        .data or []
    )
    print(f"[tier_backfill] {len(candidates)} candidates to process (dry_run={dry_run})")

    ok = error = 0
    tier_counts: Dict[str, int] = {}
    for i, c in enumerate(candidates):
        cid = str(c["id"])
        result = _backfill_tier_for_one(cid, dry_run=dry_run)
        if result["status"] == "ok":
            ok += 1
            tier_counts[result.get("tier", "?")] = tier_counts.get(result.get("tier", "?"), 0) + 1
        else:
            error += 1

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(candidates)}] ok={ok} error={error} tiers={tier_counts}")
        if sleep_sec > 0 and not dry_run:
            time.sleep(sleep_sec)

    print(f"[tier_backfill] done. ok={ok} error={error} tiers={tier_counts}")
    return {"ok": ok, "error": error, "tier_counts": tier_counts}


def run_full_backfill(
    *,
    limit: int = 600,
    dry_run: bool = False,
    only: Optional[str] = None,
) -> Dict[str, Any]:
    """
    evidence → pricing → tier の順で全候補をバックフィル。
    only: 'evidence' | 'pricing' | 'tier' で個別実行可能。
    """
    results: Dict[str, Any] = {}

    if only in (None, "evidence"):
        results["evidence"] = run_evidence_backfill(limit=limit, dry_run=dry_run)

    if only in (None, "pricing"):
        results["pricing"] = run_pricing_backfill(limit=limit, dry_run=dry_run)

    if only in (None, "tier"):
        results["tier"] = run_tier_backfill(limit=limit, dry_run=dry_run)

    return results


# ────────────────────────────────────────────
# CLI entrypoint
# ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill evidence/pricing/tier for daily_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to DB")
    parser.add_argument("--limit", type=int, default=600, help="Max candidates to process")
    parser.add_argument("--only", choices=["evidence", "pricing", "tier"], help="Run only one phase")
    args = parser.parse_args()

    results = run_full_backfill(
        limit=args.limit,
        dry_run=args.dry_run,
        only=args.only,
    )
    print("\n=== Backfill Summary ===")
    for phase, r in results.items():
        print(f"  {phase}: ok={r.get('ok')} error={r.get('error')} skip={r.get('skip','n/a')}")


if __name__ == "__main__":
    main()
