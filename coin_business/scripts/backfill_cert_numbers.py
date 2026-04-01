# -*- coding: utf-8 -*-
"""
backfill_cert_numbers.py
========================
daily_candidates の既存レコードに対して lot_title から
grading_company / cert_number を抽出してバックフィルする。

実行前に migration 011_cert_columns.sql が Supabase に適用済みであること。

使い方:
  cd coin_business
  python scripts/backfill_cert_numbers.py [--dry-run]
"""

from __future__ import annotations

import sys
import io
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.supabase_client import get_client
from scripts.candidates_writer import extract_cert_info


def main(dry_run: bool = False) -> None:
    client = get_client()

    # migration 適用確認
    try:
        r = client.table("daily_candidates").select("grading_company,cert_number").limit(1).execute()
    except Exception as e:
        print(f"ERROR: migration 011 not applied yet: {e}")
        print("Run: python scripts/apply_migration_011.py")
        return

    print("=== backfill_cert_numbers ===")
    print(f"dry_run: {dry_run}")

    # cert_number が NULL の全レコードを取得（ページネーション）
    page = 500
    offset = 0
    total_updated = 0
    total_skipped = 0

    while True:
        resp = (
            client.table("daily_candidates")
            .select("dedup_key,lot_title,grading_company,cert_number")
            .is_("cert_number", "null")
            .not_.is_("lot_title", "null")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = resp.data
        if not rows:
            break

        updates: list[dict] = []
        for row in rows:
            title = row.get("lot_title") or ""
            gc, cert = extract_cert_info(title)
            if not gc:
                total_skipped += 1
                continue
            updates.append({
                "dedup_key":       row["dedup_key"],
                "grading_company": gc,
                "cert_number":     cert,   # None if not extractable from title
            })

        if updates:
            print(f"  batch offset={offset}: {len(updates)} records to update "
                  f"({sum(1 for u in updates if u['cert_number'])} with cert)")
            if not dry_run:
                for upd in updates:
                    try:
                        client.table("daily_candidates").update({
                            "grading_company": upd["grading_company"],
                            "cert_number":     upd["cert_number"],
                        }).eq("dedup_key", upd["dedup_key"]).execute()
                        total_updated += 1
                    except Exception as e:
                        print(f"  WARN: update failed for {upd['dedup_key'][:12]}: {e}")
            else:
                total_updated += len(updates)
                for upd in updates[:3]:
                    print(f"    [DRY] {upd['dedup_key'][:16]} -> "
                          f"gc={upd['grading_company']} cert={upd['cert_number']}")

        offset += page
        if len(rows) < page:
            break

    print()
    print(f"Done: updated={total_updated}, skipped={total_skipped}")
    if dry_run:
        print("(dry-run: no DB changes made)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
