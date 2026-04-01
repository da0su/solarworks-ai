# scripts/shadow_runner.py  — Day13 完成版
"""
シャドウランナー: システム推薦 vs CEO判断の差分を記録・分析する。

目的:
  1. CEOがNGにした案件をシステムが AUTO_PASS と判定していた場合 → ルール見直し候補
  2. CEOが承認した案件をシステムが AUTO_REJECT していた場合 → ルール緩和検討
  3. 精度向上のためのフィードバックループ

実行:
  python -m scripts.shadow_runner run        # 全候補を走査してレポート保存
  python -m scripts.shadow_runner report     # 保存済みレポートを表示
  python -m scripts.shadow_runner diff       # 不一致案件のみ表示
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from scripts.supabase_client import get_supabase_client
from scripts.eligibility_rules import evaluate_candidate_eligibility


# ────────────────────────────────────────────
# Shadow evaluation
# ────────────────────────────────────────────

def _classify_agreement(ceo_decision: Optional[str], auto_tier: str) -> str:
    """
    CEO判断 × システムtierの合意分類。

    AGREE_PASS:    CEO=approved   & AUTO_PASS
    AGREE_REJECT:  CEO=rejected   & AUTO_REJECT
    DISAGREE_FP:   CEO=rejected   & AUTO_PASS   (false positive — システムが過剰に推薦)
    DISAGREE_FN:   CEO=approved   & AUTO_REJECT (false negative — システムが過剰に拒否)
    REVIEW_AGREE:  CEO=approved   & AUTO_REVIEW
    REVIEW_NG:     CEO=rejected   & AUTO_REVIEW
    PENDING:       CEO未判断
    """
    decision = (ceo_decision or "pending").lower()
    tier = auto_tier.upper()

    if decision == "pending":
        return "PENDING"
    if decision == "approved":
        if tier == "AUTO_PASS":
            return "AGREE_PASS"
        if tier == "AUTO_REVIEW":
            return "REVIEW_AGREE"
        if tier == "AUTO_REJECT":
            return "DISAGREE_FN"
    if decision == "rejected":
        if tier == "AUTO_REJECT":
            return "AGREE_REJECT"
        if tier == "AUTO_REVIEW":
            return "REVIEW_NG"
        if tier == "AUTO_PASS":
            return "DISAGREE_FP"
    return "UNKNOWN"


def evaluate_shadow_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    候補1件のシャドウ評価を返す。
    """
    evaluation = evaluate_candidate_eligibility(row)
    auto_tier = evaluation.auto_tier

    ceo_decision = row.get("ceo_decision") or row.get("decision_status") or "pending"
    agreement = _classify_agreement(ceo_decision, auto_tier)

    return {
        "candidate_id": str(row.get("id", "")),
        "title": row.get("title") or row.get("item_title", ""),
        "source": row.get("source", ""),
        "ceo_decision": ceo_decision,
        "auto_tier": auto_tier,
        "agreement": agreement,
        "hard_fail_codes": evaluation.hard_fail_codes,
        "warning_codes": evaluation.warning_codes,
        "approval_blocked": evaluation.approval_blocked,
        "projected_margin": row.get("projected_margin"),
        "projected_profit_jpy": row.get("projected_profit_jpy"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


# ────────────────────────────────────────────
# Batch runner
# ────────────────────────────────────────────

def run_shadow_evaluation(
    *,
    limit: int = 600,
    save_to_db: bool = True,
) -> Dict[str, Any]:
    """
    全候補を評価してシャドウレポートを生成・保存する。
    """
    supabase = get_supabase_client()
    rows = (
        supabase.table("daily_candidates")
        .select("*")
        .limit(limit)
        .execute()
        .data or []
    )

    print(f"[shadow_runner] evaluating {len(rows)} candidates...")

    results: List[Dict[str, Any]] = []
    for row in rows:
        result = evaluate_shadow_candidate(row)
        results.append(result)

    # 集計
    summary = _build_summary(results)
    print_shadow_report(summary, results)

    # DB保存 (shadow_run_reports テーブルがあれば)
    if save_to_db:
        _save_shadow_report(supabase, summary, results)

    return {"summary": summary, "results": results}


def _build_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for r in results:
        ag = r["agreement"]
        counts[ag] = counts.get(ag, 0) + 1

    total = len(results)
    decided = sum(counts.get(k, 0) for k in counts if k != "PENDING")
    agree = counts.get("AGREE_PASS", 0) + counts.get("AGREE_REJECT", 0)
    disagree = counts.get("DISAGREE_FP", 0) + counts.get("DISAGREE_FN", 0)
    precision = agree / decided if decided > 0 else 0.0

    return {
        "total": total,
        "decided": decided,
        "pending": counts.get("PENDING", 0),
        "agree_pass": counts.get("AGREE_PASS", 0),
        "agree_reject": counts.get("AGREE_REJECT", 0),
        "review_agree": counts.get("REVIEW_AGREE", 0),
        "review_ng": counts.get("REVIEW_NG", 0),
        "disagree_fp": counts.get("DISAGREE_FP", 0),
        "disagree_fn": counts.get("DISAGREE_FN", 0),
        "precision": round(precision, 4),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_shadow_report(
    supabase: Any,
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> None:
    try:
        # shadow_run_reports に1行保存
        report_res = (
            supabase.table("shadow_run_reports")
            .insert({
                "run_at": summary["evaluated_at"],
                "total_candidates": summary["total"],
                "decided_count": summary["decided"],
                "agree_count": summary["agree_pass"] + summary["agree_reject"],
                "disagree_count": summary["disagree_fp"] + summary["disagree_fn"],
                "precision_rate": summary["precision"],
                "summary_json": summary,
            })
            .execute()
        )
        report_id = (report_res.data or [{}])[0].get("id")

        # shadow_run_items に各候補の評価を保存
        if report_id:
            items = []
            for r in results:
                items.append({
                    "shadow_run_report_id": str(report_id),
                    "candidate_id": r["candidate_id"],
                    "ceo_decision": r["ceo_decision"],
                    "system_tier": r["auto_tier"],
                    "agreement": r["agreement"],
                    "hard_fail_codes": r["hard_fail_codes"],
                    "warning_codes": r["warning_codes"],
                })
            # バッチinsert (50件ずつ)
            for i in range(0, len(items), 50):
                supabase.table("shadow_run_items").insert(items[i:i+50]).execute()

        print(f"[shadow_runner] saved report_id={report_id}")
    except Exception as e:
        print(f"[shadow_runner] DB save skipped (table may not exist): {e}")


# ────────────────────────────────────────────
# Report display
# ────────────────────────────────────────────

def print_shadow_report(
    summary: Dict[str, Any],
    results: Optional[List[Dict[str, Any]]] = None,
    show_disagree: bool = True,
) -> None:
    print(f"\n{'='*60}")
    print(f"  Shadow Run Report — {summary.get('evaluated_at','')[:19]}")
    print(f"{'='*60}")
    print(f"  総候補数:      {summary['total']}")
    print(f"  CEO判断済み:   {summary['decided']}")
    print(f"  未判断:        {summary['pending']}")
    print(f"  一致 (PASS):   {summary['agree_pass']}")
    print(f"  一致 (REJECT): {summary['agree_reject']}")
    print(f"  要確認 (REVIEW-AGREE): {summary['review_agree']}")
    print(f"  要確認 (REVIEW-NG):    {summary['review_ng']}")
    print(f"  不一致 FP (過剰推薦): {summary['disagree_fp']}")
    print(f"  不一致 FN (過剰拒否): {summary['disagree_fn']}")
    print(f"  Precision:     {summary['precision']:.1%}")
    print(f"{'='*60}")

    if show_disagree and results:
        disagrees = [r for r in results if r["agreement"] in ("DISAGREE_FP", "DISAGREE_FN")]
        if disagrees:
            print(f"\n--- 不一致案件 ({len(disagrees)}件) ---")
            for r in disagrees[:20]:
                ag = r["agreement"]
                label = "過剰推薦" if ag == "DISAGREE_FP" else "過剰拒否"
                print(f"  [{label}] {r['title'][:40]:40s}  CEO={r['ceo_decision']:10s}  tier={r['auto_tier']}")
                if r["hard_fail_codes"]:
                    print(f"           hard_fail={r['hard_fail_codes']}")
            if len(disagrees) > 20:
                print(f"  ... (他{len(disagrees)-20}件)")


def load_latest_shadow_report() -> Optional[Dict[str, Any]]:
    """DBから最新のシャドウレポートを取得"""
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("shadow_run_reports")
            .select("*")
            .order("run_at", desc=True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]
    except Exception:
        return None


def get_disagree_candidates(limit: int = 50) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    最新レポートの不一致案件を返す。
    戻り値: (false_positives, false_negatives)
    """
    supabase = get_supabase_client()
    try:
        report = load_latest_shadow_report()
        if not report:
            return [], []

        report_id = str(report["id"])
        fp_res = (
            supabase.table("shadow_run_items")
            .select("*")
            .eq("shadow_run_report_id", report_id)
            .eq("agreement", "DISAGREE_FP")
            .limit(limit)
            .execute()
        )
        fn_res = (
            supabase.table("shadow_run_items")
            .select("*")
            .eq("shadow_run_report_id", report_id)
            .eq("agreement", "DISAGREE_FN")
            .limit(limit)
            .execute()
        )
        return fp_res.data or [], fn_res.data or []
    except Exception:
        return [], []


# ────────────────────────────────────────────
# CLI entrypoint
# ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="シャドウランナー: システム推薦 vs CEO判断 差分分析")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="全候補を評価してレポート保存")
    p_run.add_argument("--limit", type=int, default=600)
    p_run.add_argument("--no-save", action="store_true", help="DBへの保存をスキップ")

    sub.add_parser("report", help="最新レポートをDBから取得して表示")
    sub.add_parser("diff", help="不一致案件のみ表示")

    args = parser.parse_args()

    if args.command == "run":
        run_shadow_evaluation(limit=args.limit, save_to_db=not args.no_save)

    elif args.command == "report":
        report = load_latest_shadow_report()
        if report:
            print_shadow_report(report.get("summary_json", {}))
        else:
            print("No shadow reports found. Run: python -m scripts.shadow_runner run")

    elif args.command == "diff":
        fp, fn = get_disagree_candidates()
        print(f"\n過剰推薦 (FP): {len(fp)}件")
        for r in fp[:10]:
            print(f"  {r.get('candidate_id','')} hard_fail={r.get('hard_fail_codes')}")
        print(f"\n過剰拒否 (FN): {len(fn)}件")
        for r in fn[:10]:
            print(f"  {r.get('candidate_id','')} hard_fail={r.get('hard_fail_codes')}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
