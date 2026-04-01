# scripts/nightly_ops.py  — Day12 完成版
"""
夜間バッチ: status / evidence / pricing の日次リフレッシュ。
毎晩 CEOがスリープ中に自動実行する。

実行:
  python -m scripts.nightly_ops
  python -m scripts.nightly_ops --skip-pricing
  python -m scripts.nightly_ops --only-status
  python -m scripts.nightly_ops --dry-run
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_supabase_client


# ────────────────────────────────────────────
# Phase 1: Status refresh
# ────────────────────────────────────────────

def phase_status_refresh(
    *,
    limit: int = 600,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    daily_candidates の is_active / is_sold / lot_size を再チェックする。
    現時点は DB レコードの NULL 値を確定させる（将来: eBay/Heritage API でライブ確認）。
    """
    from scripts.status_refresher import sync_daily_candidate_current_status

    supabase = get_supabase_client()
    rows = (
        supabase.table("daily_candidates")
        .select("id, is_active, is_sold")
        .eq("is_active", True)
        .limit(limit)
        .execute()
        .data or []
    )

    print(f"[nightly:status] {len(rows)} active candidates to refresh (dry_run={dry_run})")
    ok = error = 0
    for row in rows:
        cid = str(row["id"])
        if dry_run:
            ok += 1
            continue
        try:
            sync_daily_candidate_current_status(cid, {})
            ok += 1
        except Exception as e:
            error += 1
            if error <= 5:
                print(f"  ERROR {cid}: {e}")

    return {"ok": ok, "error": error, "total": len(rows)}


# ────────────────────────────────────────────
# Phase 2: Evidence refresh for stale candidates
# ────────────────────────────────────────────

def phase_evidence_refresh(
    *,
    limit: int = 100,
    dry_run: bool = False,
    stale_hours: int = 24,
) -> Dict[str, Any]:
    """
    evidence_count=0 または ancient な候補の evidence を再生成。
    """
    from scripts.evidence_builder import build_candidate_evidence_bundle

    supabase = get_supabase_client()

    # evidence_count=0 の候補を優先
    rows = (
        supabase.table("daily_candidates")
        .select("id, evidence_count, is_active")
        .or_("evidence_count.is.null,evidence_count.eq.0")
        .eq("is_active", True)
        .limit(limit)
        .execute()
        .data or []
    )

    print(f"[nightly:evidence] {len(rows)} candidates need evidence (dry_run={dry_run})")
    ok = error = 0
    for row in rows:
        cid = str(row["id"])
        if dry_run:
            ok += 1
            continue
        try:
            build_candidate_evidence_bundle(cid, replace_generated=False)
            ok += 1
            time.sleep(0.1)
        except Exception as e:
            error += 1
            if error <= 5:
                print(f"  ERROR {cid}: {e}")

    return {"ok": ok, "error": error, "total": len(rows)}


# ────────────────────────────────────────────
# Phase 3: Pricing refresh
# ────────────────────────────────────────────

def phase_pricing_refresh(
    *,
    limit: int = 100,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    recommended_max_bid_jpy が null の候補に対して pricing snapshot を作成。
    """
    from scripts.pricing_engine import (
        build_and_save_candidate_pricing_snapshot,
        fetch_market_transactions,
    )

    supabase = get_supabase_client()
    rows = (
        supabase.table("daily_candidates")
        .select("*")
        .is_("recommended_max_bid_jpy", "null")
        .eq("is_active", True)
        .limit(limit)
        .execute()
        .data or []
    )

    print(f"[nightly:pricing] {len(rows)} candidates need pricing (dry_run={dry_run})")
    if not rows:
        return {"ok": 0, "error": 0, "total": 0}

    # 市場データ一括取得
    market_rows = None
    if not dry_run:
        market_rows = fetch_market_transactions(limit=30000)
        print(f"  market_rows={len(market_rows)}")

    ok = error = skip = 0
    for row in rows:
        cid = str(row["id"])
        if dry_run:
            ok += 1
            continue

        # 仕入れ価格解決
        purchase_jpy = _resolve_purchase_jpy(row)
        if purchase_jpy is None:
            skip += 1
            continue

        try:
            build_and_save_candidate_pricing_snapshot(
                row,
                purchase_price_jpy=purchase_jpy,
                import_tax_jpy=0.0,
                market_rows=market_rows,
            )
            ok += 1
            time.sleep(0.2)
        except Exception as e:
            error += 1
            if error <= 5:
                print(f"  ERROR {cid}: {e}")

    return {"ok": ok, "error": error, "skip": skip, "total": len(rows)}


def _resolve_purchase_jpy(row: Dict[str, Any]) -> Optional[float]:
    for key in ("buy_limit_jpy", "current_price_jpy", "price_jpy"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except Exception:
                continue
    for key in ("current_price", "price", "price_usd"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v) * 150.0
            except Exception:
                continue
    return None


# ────────────────────────────────────────────
# Phase 4: auto_tier sync
# ────────────────────────────────────────────

def phase_tier_sync(
    *,
    limit: int = 600,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    auto_tier が null の候補に eligibility_rules を適用して更新する。
    """
    from scripts.review_queue import refresh_auto_tier_for_candidate

    supabase = get_supabase_client()
    rows = (
        supabase.table("daily_candidates")
        .select("id")
        .is_("auto_tier", "null")
        .limit(limit)
        .execute()
        .data or []
    )

    print(f"[nightly:tier] {len(rows)} candidates need tier sync (dry_run={dry_run})")
    ok = error = 0
    tier_counts: Dict[str, int] = {}
    for row in rows:
        cid = str(row["id"])
        if dry_run:
            ok += 1
            continue
        try:
            tier = refresh_auto_tier_for_candidate(cid)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            ok += 1
        except Exception as e:
            error += 1

    return {"ok": ok, "error": error, "tier_counts": tier_counts, "total": len(rows)}


# ────────────────────────────────────────────
# Main orchestrator
# ────────────────────────────────────────────

def run_nightly_ops(
    *,
    skip_status: bool = False,
    skip_evidence: bool = False,
    skip_pricing: bool = False,
    skip_tier: bool = False,
    dry_run: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"  Nightly Ops — {started_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  dry_run={dry_run}  limit={limit}")
    print(f"{'='*60}")

    results: Dict[str, Any] = {}

    if not skip_status:
        results["status"] = phase_status_refresh(limit=limit, dry_run=dry_run)
        _print_phase_result("status", results["status"])

    if not skip_evidence:
        results["evidence"] = phase_evidence_refresh(limit=limit // 2, dry_run=dry_run)
        _print_phase_result("evidence", results["evidence"])

    if not skip_pricing:
        results["pricing"] = phase_pricing_refresh(limit=limit // 2, dry_run=dry_run)
        _print_phase_result("pricing", results["pricing"])

    if not skip_tier:
        results["tier"] = phase_tier_sync(limit=limit, dry_run=dry_run)
        _print_phase_result("tier", results["tier"])

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    print(f"\n{'='*60}")
    print(f"  Nightly Ops complete — {elapsed:.1f}s")
    print(f"{'='*60}\n")

    return results


def _print_phase_result(name: str, result: Dict[str, Any]) -> None:
    ok = result.get("ok", 0)
    error = result.get("error", 0)
    total = result.get("total", 0)
    extra = ""
    if "tier_counts" in result:
        extra = f"  tiers={result['tier_counts']}"
    print(f"  [{name}] ok={ok} error={error} total={total}{extra}")


# ────────────────────────────────────────────
# CLI entrypoint
# ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="夜間バッチ: status/evidence/pricing/tier refresh")
    parser.add_argument("--dry-run", action="store_true", help="DB書き込みなしでドライラン")
    parser.add_argument("--limit", type=int, default=200, help="各フェーズの処理件数上限")
    parser.add_argument("--skip-status", action="store_true")
    parser.add_argument("--skip-evidence", action="store_true")
    parser.add_argument("--skip-pricing", action="store_true")
    parser.add_argument("--skip-tier", action="store_true")
    parser.add_argument("--only-status", action="store_true", help="ステータスフェーズのみ実行")
    args = parser.parse_args()

    if args.only_status:
        result = phase_status_refresh(limit=args.limit, dry_run=args.dry_run)
        _print_phase_result("status", result)
        return

    run_nightly_ops(
        skip_status=args.skip_status,
        skip_evidence=args.skip_evidence,
        skip_pricing=args.skip_pricing,
        skip_tier=args.skip_tier,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
