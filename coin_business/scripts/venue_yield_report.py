"""
venue_yield_report.py — 会場別歩留まり集計レポート

CEO確認タブでの OK/NG/HOLD 判断結果を会場別に集計し、
歩留まり（OK率）を表示する。

使い方:
  python scripts/venue_yield_report.py
  python scripts/venue_yield_report.py --date 2026-04-03
  python scripts/venue_yield_report.py --all-time
  python run.py yield-report
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from supabase_client import get_client


def run_yield_report(
    scan_date: str | None = None,
    all_time: bool = False,
    verbose: bool = True,
) -> dict:
    """
    会場別歩留まりを集計して辞書で返す。

    Returns:
        {
          'date': str,
          'all_time': bool,
          'rows': [
            { 'house': str, 'total': int, 'pending': int, 'ok': int,
              'ng': int, 'hold': int, 'judged': int, 'ok_rate': float|None }
          ],
          'grand_total': dict,
        }
    """
    c = get_client()

    # クエリ構築
    q = c.table("ceo_review_log").select(
        "auction_house,source_group,ceo_decision,review_bucket,scan_date"
    )
    if not all_time:
        target_date = scan_date or str(date.today())
        q = q.eq("scan_date", target_date)

    r = q.execute()
    rows = r.data or []

    # 集計
    agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        house = row.get("auction_house") or "EBAY"
        dec = row.get("ceo_decision") or "PENDING"
        agg[house][dec] += 1
        agg[house]["TOTAL"] += 1

    # 行データ構築（会場順: EBAY → NOBLE → SPINK → その他）
    HOUSE_ORDER = ["EBAY", "NOBLE", "SPINK", "NOONANS", "HERITAGE",
                   "STACKS_BOWERS", "GREATCOLLECTIONS", "SIXBID",
                   "CATAWIKI", "MA_SHOPS", "OTHER"]
    all_houses = sorted(agg.keys(), key=lambda h: (HOUSE_ORDER.index(h) if h in HOUSE_ORDER else 99, h))

    result_rows = []
    grand = defaultdict(int)
    for house in all_houses:
        d = agg[house]
        total   = d["TOTAL"]
        pending = d["PENDING"]
        ok      = d["OK"]
        ng      = d["NG"]
        hold    = d["HOLD"]
        judged  = ok + ng + hold
        ok_rate = (ok / judged * 100) if judged > 0 else None

        result_rows.append({
            "house":   house,
            "total":   total,
            "pending": pending,
            "ok":      ok,
            "ng":      ng,
            "hold":    hold,
            "judged":  judged,
            "ok_rate": ok_rate,
        })
        for k in ("total", "pending", "ok", "ng", "hold", "judged"):
            grand[k] += result_rows[-1][k]

    grand["ok_rate"] = (grand["ok"] / grand["judged"] * 100) if grand["judged"] > 0 else None

    result = {
        "date":        target_date if not all_time else "ALL",
        "all_time":    all_time,
        "rows":        result_rows,
        "grand_total": dict(grand),
        "total_rows":  len(rows),
    }

    if verbose:
        _print_report(result)

    return result


def _print_report(result: dict) -> None:
    """テキスト形式で歩留まりレポートを表示。"""
    date_str = result["date"]
    total_rows = result["total_rows"]

    print()
    print(f"{'='*60}")
    print(f"  会場別歩留まりレポート  [{date_str}]  ({total_rows}件)")
    print(f"{'='*60}")
    print(f"{'会場':<18} {'件数':>5} {'未判断':>6} {'OK':>5} {'NG':>5} {'HOLD':>5} {'OK率':>7}")
    print(f"{'-'*60}")

    HOUSE_LABEL = {
        "EBAY":             "eBay",
        "NOBLE":            "Noble Numismatics",
        "SPINK":            "Spink",
        "NOONANS":          "Noonans",
        "HERITAGE":         "Heritage",
        "STACKS_BOWERS":    "Stack's Bowers",
        "GREATCOLLECTIONS": "GreatCollections",
        "SIXBID":           "Sixbid",
        "CATAWIKI":         "Catawiki",
        "MA_SHOPS":         "MA-Shops",
        "OTHER":            "Other",
    }

    for row in result["rows"]:
        house_label = HOUSE_LABEL.get(row["house"], row["house"])
        ok_rate_str = f"{row['ok_rate']:.0f}%" if row["ok_rate"] is not None else "-"
        print(
            f"{house_label:<18} {row['total']:>5} {row['pending']:>6} "
            f"{row['ok']:>5} {row['ng']:>5} {row['hold']:>5} {ok_rate_str:>7}"
        )

    g = result["grand_total"]
    ok_rate_str = f"{g['ok_rate']:.0f}%" if g.get("ok_rate") is not None else "-"
    print(f"{'-'*60}")
    print(
        f"{'合計':<18} {g['total']:>5} {g['pending']:>6} "
        f"{g['ok']:>5} {g['ng']:>5} {g['hold']:>5} {ok_rate_str:>7}"
    )
    print(f"{'='*60}")

    # 判断済み詳細
    judged = g.get("judged", 0)
    if judged > 0:
        print(f"\n  判断済み {judged}件 / 全{g['total']}件")
        ok = g.get("ok", 0)
        ng = g.get("ng", 0)
        hold = g.get("hold", 0)
        print(f"  ✅ OK: {ok}件  ❌ NG: {ng}件  ⏸ HOLD: {hold}件")
    else:
        print(f"\n  ⚠️  判断済み 0件 — CEO確認タブで OK/NG/HOLD を入力してください")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="会場別歩留まりレポート")
    parser.add_argument("--date", help="対象日 (YYYY-MM-DD, default: today)")
    parser.add_argument("--all-time", action="store_true", help="全期間集計")
    args = parser.parse_args()

    run_yield_report(
        scan_date=args.date,
        all_time=args.all_time,
        verbose=True,
    )
