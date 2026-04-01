# scripts/bid_executor.py  — Day11 完成版
"""
入札実行ブリッジ。
- dry_run: DB への bidding_records 書き込みのみ（実際の入札なし）
- manual: CEOが手動で入札した結果を記録する manual-entry モード
- batch:  queued状態の bidding_records を一括処理

外部オークションシステムとの自動連携は将来実装。
現時点はmanual bridgeとして機能する。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scripts.supabase_client import get_supabase_client
from scripts.bid_recorder import (
    queue_candidate_for_bid,
    update_bid_record_status,
    list_bid_records,
    get_latest_bid_record_for_candidate,
    get_bid_summary,
    VALID_BID_STATUSES,
)


# ────────────────────────────────────────────
# Dry-run executor
# ────────────────────────────────────────────

def dry_run_bid(
    *,
    candidate_id: str,
    bid_max_jpy: Optional[float] = None,
    approved_by: str = "ceo",
    note: str = "dry_run",
) -> Dict[str, Any]:
    """
    実際の入札は行わず、bidding_records に queued レコードを作成する。
    eBay/Heritage/Spink の画面上で CEOが手動入札する前の事前登録として機能する。
    """
    return queue_candidate_for_bid(
        candidate_id=candidate_id,
        approved_by=approved_by,
        bid_max_jpy=bid_max_jpy,
        note=f"[dry_run] {note}",
    )


# ────────────────────────────────────────────
# Manual result recording
# ────────────────────────────────────────────

def record_manual_bid_result(
    *,
    record_id: str,
    result: str,  # 'won' | 'lost' | 'cancelled' | 'failed'
    external_ref: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    CEOが手動で入札した結果を bidding_records に記録する。
    result: 'won' | 'lost' | 'cancelled' | 'failed'
    """
    if result not in VALID_BID_STATUSES:
        raise ValueError(f"Invalid result: {result}. Must be one of {VALID_BID_STATUSES}")
    if result in ("queued", "submitted"):
        raise ValueError(f"Use submit_bid() to set status to '{result}'")

    return update_bid_record_status(
        record_id=record_id,
        bid_status=result,
        external_ref=external_ref,
        note=note,
    )


def submit_bid(
    *,
    record_id: str,
    external_ref: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    queued → submitted に遷移させる。
    CEOが実際にオークションサイトで入札した直後に呼ぶ。
    """
    return update_bid_record_status(
        record_id=record_id,
        bid_status="submitted",
        external_ref=external_ref,
        note=note,
    )


# ────────────────────────────────────────────
# Batch queue processor
# ────────────────────────────────────────────

def process_queued_bids(
    *,
    dry_run: bool = True,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    queued 状態の bidding_records を処理する。
    dry_run=True の間は submitted に遷移するだけ（実際の入札なし）。
    dry_run=False は将来の自動入札実装で使用予定。
    """
    queued = list_bid_records(limit=limit, status="queued")
    print(f"[bid_executor] {len(queued)} queued bids found (dry_run={dry_run})")

    processed = []
    for record in queued:
        record_id = str(record["id"])
        candidate_id = str(record.get("candidate_id", ""))

        if dry_run:
            # dry_run: submittedに遷移して完了とみなす
            try:
                updated = update_bid_record_status(
                    record_id=record_id,
                    bid_status="submitted",
                    note="auto-processed dry_run",
                )
                processed.append({"record_id": record_id, "candidate_id": candidate_id, "status": "submitted"})
            except Exception as e:
                processed.append({"record_id": record_id, "candidate_id": candidate_id, "status": "error", "error": str(e)})
        else:
            # 将来: 実際の入札API呼び出しをここに実装
            processed.append({
                "record_id": record_id,
                "candidate_id": candidate_id,
                "status": "pending_manual",
                "note": "外部入札システム未実装 — CEOが手動で入札してください",
            })

    return {
        "processed": len(processed),
        "dry_run": dry_run,
        "records": processed,
        "summary": get_bid_summary(),
    }


# ────────────────────────────────────────────
# Batch approve + queue
# ────────────────────────────────────────────

def auto_queue_approved_candidates(
    *,
    limit: int = 50,
    dry_run: bool = True,
    approved_by: str = "ceo",
) -> Dict[str, Any]:
    """
    ceo_decision='approved' かつ未入札の候補を自動的に bidding_records に積む。
    """
    supabase = get_supabase_client()

    # 承認済みで、まだ bidding_records にない候補を探す
    approved = (
        supabase.table("daily_candidates")
        .select("*")
        .eq("ceo_decision", "approved")
        .eq("is_active", True)
        .limit(limit)
        .execute()
        .data or []
    )

    queued_records = list_bid_records(limit=1000, status="queued")
    submitted_records = list_bid_records(limit=1000, status="submitted")
    already_queued_ids = {
        str(r["candidate_id"])
        for r in (queued_records + submitted_records)
    }

    to_queue = [c for c in approved if str(c["id"]) not in already_queued_ids]
    print(f"[auto_queue] {len(approved)} approved, {len(to_queue)} new to queue (dry_run={dry_run})")

    results = []
    ok = skip = error = 0
    for c in to_queue:
        cid = str(c["id"])
        if dry_run:
            results.append({"candidate_id": cid, "status": "would_queue"})
            skip += 1
            continue
        try:
            record = queue_candidate_for_bid(
                candidate_id=cid,
                approved_by=approved_by,
            )
            results.append({"candidate_id": cid, "status": "queued", "record_id": str(record.get("id", ""))})
            ok += 1
        except Exception as e:
            results.append({"candidate_id": cid, "status": "error", "error": str(e)})
            error += 1

    return {"ok": ok, "skip": skip, "error": error, "results": results}


# ────────────────────────────────────────────
# Status report
# ────────────────────────────────────────────

def print_bid_status_report() -> None:
    summary = get_bid_summary()
    print("\n=== 入札実績サマリー ===")
    for status, count in summary.items():
        print(f"  {status:12s}: {count}件")
    print()

    # 直近 submitted
    submitted = list_bid_records(limit=10, status="submitted")
    if submitted:
        print("--- 入札中 (submitted) ---")
        for r in submitted:
            print(f"  [{r.get('candidate_id','')}] max={r.get('bid_max_jpy','')}JPY  ref={r.get('external_ref','')}")

    # 直近 won
    won = list_bid_records(limit=5, status="won")
    if won:
        print("--- 落札済み (won) ---")
        for r in won:
            print(f"  [{r.get('candidate_id','')}] max={r.get('bid_max_jpy','')}JPY  ref={r.get('external_ref','')}")


# ────────────────────────────────────────────
# CLI entrypoint
# ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="入札実行ブリッジ")
    sub = parser.add_subparsers(dest="command")

    # queue-approved
    p_qa = sub.add_parser("queue-approved", help="承認済み候補を入札キューに積む")
    p_qa.add_argument("--dry-run", action="store_true")
    p_qa.add_argument("--limit", type=int, default=50)

    # process-queued
    p_pq = sub.add_parser("process-queued", help="キュー済み入札を処理する")
    p_pq.add_argument("--dry-run", action="store_true", default=True)
    p_pq.add_argument("--limit", type=int, default=20)

    # submit
    p_sub = sub.add_parser("submit", help="queued → submitted")
    p_sub.add_argument("record_id")
    p_sub.add_argument("--ref", dest="external_ref", default=None)
    p_sub.add_argument("--note", default=None)

    # result
    p_res = sub.add_parser("result", help="落札/落選結果を記録")
    p_res.add_argument("record_id")
    p_res.add_argument("result", choices=["won", "lost", "cancelled", "failed"])
    p_res.add_argument("--ref", dest="external_ref", default=None)
    p_res.add_argument("--note", default=None)

    # status
    sub.add_parser("status", help="入札状況レポート")

    args = parser.parse_args()

    if args.command == "queue-approved":
        r = auto_queue_approved_candidates(limit=args.limit, dry_run=args.dry_run)
        print(f"ok={r['ok']} skip={r['skip']} error={r['error']}")

    elif args.command == "process-queued":
        r = process_queued_bids(dry_run=args.dry_run, limit=args.limit)
        print(f"processed={r['processed']}")

    elif args.command == "submit":
        r = submit_bid(record_id=args.record_id, external_ref=args.external_ref, note=args.note)
        print(f"submitted: {r.get('id')}")

    elif args.command == "result":
        r = record_manual_bid_result(
            record_id=args.record_id,
            result=args.result,
            external_ref=args.external_ref,
            note=args.note,
        )
        print(f"recorded: {r.get('id')} → {args.result}")

    elif args.command == "status":
        print_bid_status_report()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
