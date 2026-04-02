"""
coin_business/scripts/dashboard.py
=====================================
ターミナル向けダッシュボード。

表示内容:
  1. KPI サマリー
     - Yahoo staging PENDING_CEO 件数
     - AUDIT_PASS 件数
     - KEEP 件数 (watchlist ACTIVE)
     - BID_READY 件数

  2. 候補一覧 (daily_candidates)
     - audit_status, watch_status, target_max_bid_jpy,
       comparison_quality_score, title/lot_title, country, grade

CLI:
  python dashboard.py                     # フル表示
  python dashboard.py --kpi-only          # KPI のみ
  python dashboard.py --candidates --limit 20   # 候補一覧のみ
  python dashboard.py --watchlist         # watchlist のみ
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from constants import AuditStatus, Table, WatchStatus
from scripts.supabase_client import get_client

# ANSI カラーコード (TTY 以外では無効化)
_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

GREEN   = lambda t: _c("32", t)
YELLOW  = lambda t: _c("33", t)
RED     = lambda t: _c("31", t)
CYAN    = lambda t: _c("36", t)
BOLD    = lambda t: _c("1",  t)
DIM     = lambda t: _c("2",  t)


# ================================================================
# KPI 取得
# ================================================================

def fetch_kpi(client) -> dict:
    kpi = {
        "yahoo_pending_count": 0,
        "audit_pass_count":    0,
        "audit_hold_count":    0,
        "audit_fail_count":    0,
        "keep_count":          0,
        "bid_ready_count":     0,
        "total_candidates":    0,
        "total_watchlist":     0,
    }

    _q = {
        "yahoo_pending_count": lambda db: (
            db.table(Table.YAHOO_SOLD_LOTS_STAGING)
            .select("id", count="exact")
            .eq("status", "PENDING_CEO")
        ),
        "audit_pass_count": lambda db: (
            db.table(Table.DAILY_CANDIDATES)
            .select("id", count="exact")
            .eq("audit_status", AuditStatus.AUDIT_PASS)
        ),
        "audit_hold_count": lambda db: (
            db.table(Table.DAILY_CANDIDATES)
            .select("id", count="exact")
            .eq("audit_status", AuditStatus.AUDIT_HOLD)
        ),
        "audit_fail_count": lambda db: (
            db.table(Table.DAILY_CANDIDATES)
            .select("id", count="exact")
            .eq("audit_status", AuditStatus.AUDIT_FAIL)
        ),
        "keep_count": lambda db: (
            db.table(Table.CANDIDATE_WATCHLIST)
            .select("id", count="exact")
            .in_("status", list(WatchStatus.ACTIVE))
        ),
        "bid_ready_count": lambda db: (
            db.table(Table.CANDIDATE_WATCHLIST)
            .select("id", count="exact")
            .eq("status", WatchStatus.BID_READY)
        ),
        "total_candidates": lambda db: (
            db.table(Table.DAILY_CANDIDATES).select("id", count="exact")
        ),
        "total_watchlist": lambda db: (
            db.table(Table.CANDIDATE_WATCHLIST).select("id", count="exact")
        ),
    }

    for key, build_query in _q.items():
        try:
            res = build_query(client).execute()
            kpi[key] = res.count or 0
        except Exception:
            pass

    return kpi


# ================================================================
# 候補一覧取得 (audit_status + watch_status + pricing)
# ================================================================

def fetch_candidates_with_watch(client, limit: int = 20) -> list[dict]:
    """
    daily_candidates を取得し、watchlist の status を JOIN して返す。
    Supabase の Python クライアントは SQL JOIN をサポートしていないため、
    候補取得後に watchlist を dict で引いて結合する。
    """
    try:
        res = (
            client.table(Table.DAILY_CANDIDATES)
            .select(
                "id, title, lot_title, country, grade, year, source, "
                "audit_status, target_max_bid_jpy, comparison_quality_score, "
                "created_at"
            )
            .in_("audit_status", [
                AuditStatus.AUDIT_PASS,
                AuditStatus.AUDIT_HOLD,
                AuditStatus.AUDIT_FAIL,
            ])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        candidates = res.data or []
    except Exception as exc:
        print(f"  [エラー] candidates fetch: {exc}")
        return []

    # watchlist を candidate_id で引く
    cand_ids = [c["id"] for c in candidates]
    watch_map: dict = {}
    if cand_ids:
        try:
            wres = (
                client.table(Table.CANDIDATE_WATCHLIST)
                .select("candidate_id, status, current_price_jpy, max_bid_jpy")
                .in_("candidate_id", cand_ids)
                .execute()
            )
            for w in (wres.data or []):
                watch_map[w["candidate_id"]] = w
        except Exception:
            pass

    for c in candidates:
        w = watch_map.get(c["id"])
        c["watch_status"]     = w["status"]       if w else None
        c["current_price_jpy"]= w.get("current_price_jpy") if w else None

    return candidates


# ================================================================
# 表示関数
# ================================================================

def _print_kpi(kpi: dict) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print()
    print(BOLD("=" * 60))
    print(BOLD(f"  [coin] SolarWorks AI -- coin_business Dashboard  {now}"))
    print(BOLD("=" * 60))
    print()
    print(BOLD("  📊 KPI サマリー"))
    print(f"  {'Yahoo Pending':<30} {YELLOW(str(kpi['yahoo_pending_count'])):>8} 件")
    print(f"  {'AUDIT_PASS 候補':<30} {GREEN(str(kpi['audit_pass_count'])):>8} 件")
    print(f"  {'AUDIT_HOLD 候補':<30} {YELLOW(str(kpi['audit_hold_count'])):>8} 件")
    print(f"  {'AUDIT_FAIL 候補':<30} {DIM(str(kpi['audit_fail_count'])):>8} 件")
    print(f"  {'KEEP 監視中':<30} {CYAN(str(kpi['keep_count'])):>8} 件")

    bid_ready_str = (
        RED(f"⚡ {kpi['bid_ready_count']}")
        if kpi["bid_ready_count"] > 0
        else str(kpi["bid_ready_count"])
    )
    print(f"  {'BID_READY':<30} {bid_ready_str:>8} 件")
    print(f"  {'候補総数':<30} {str(kpi['total_candidates']):>8} 件")
    print(f"  {'Watchlist 総数':<30} {str(kpi['total_watchlist']):>8} 件")
    print()


_AUDIT_COLOR = {
    AuditStatus.AUDIT_PASS: GREEN,
    AuditStatus.AUDIT_HOLD: YELLOW,
    AuditStatus.AUDIT_FAIL: RED,
}
_WATCH_COLOR = {
    WatchStatus.BID_READY:   RED,
    WatchStatus.ENDING_SOON: YELLOW,
    WatchStatus.PRICE_OK:    GREEN,
    WatchStatus.PRICE_HIGH:  YELLOW,
    WatchStatus.WATCHING:    CYAN,
}


def _print_candidates(candidates: list[dict]) -> None:
    if not candidates:
        print("  候補なし")
        return
    print(BOLD("  📋 候補一覧 (直近)"))
    print(
        f"  {'audit':<12} {'watch':<14} {'target_bid':>10} {'score':>6}  "
        f"{'country':<10} {'grade':<8} {'title'}"
    )
    print("  " + "-" * 100)
    for c in candidates:
        audit = c.get("audit_status") or "-"
        watch = c.get("watch_status") or "-"
        target = c.get("target_max_bid_jpy")
        score  = c.get("comparison_quality_score")
        t_str  = f"¥{target:,}" if target else "-"
        s_str  = f"{score:.2f}" if score is not None else "-"
        title  = (c.get("title") or c.get("lot_title") or "-")[:40]
        country = (c.get("country") or "-")[:10]
        grade   = (c.get("grade")   or "-")[:8]

        a_color = _AUDIT_COLOR.get(audit, DIM)
        w_color = _WATCH_COLOR.get(watch, DIM)

        print(
            f"  {a_color(audit):<12} {w_color(watch):<14} "
            f"{t_str:>10} {s_str:>6}  {country:<10} {grade:<8} {DIM(title)}"
        )
    print()


def _print_watchlist(client, limit: int = 10) -> None:
    try:
        res = (
            client.table(Table.CANDIDATE_WATCHLIST)
            .select("id, status, current_price_jpy, max_bid_jpy, time_left_seconds, auction_end_at")
            .in_("status", list(WatchStatus.ACTIVE))
            .order("next_refresh_at")
            .limit(limit)
            .execute()
        )
        items = res.data or []
    except Exception as exc:
        print(f"  [エラー] watchlist fetch: {exc}")
        return

    if not items:
        print("  KEEP 監視中アイテムなし")
        return

    print(BOLD("  👁 KEEP 監視台帳 (ACTIVE)"))
    print(f"  {'status':<14} {'current':>10} {'max_bid':>10} {'残り(分)':>8}  id")
    print("  " + "-" * 80)
    for item in items:
        status  = item.get("status", "-")
        cur     = item.get("current_price_jpy")
        max_b   = item.get("max_bid_jpy")
        left    = item.get("time_left_seconds")
        wid     = str(item.get("id", ""))[:8]
        c_str   = f"¥{cur:,}" if cur else "-"
        m_str   = f"¥{max_b:,}" if max_b else "-"
        l_str   = f"{left // 60}" if left else "-"
        w_color = _WATCH_COLOR.get(status, DIM)
        print(f"  {w_color(status):<14} {c_str:>10} {m_str:>10} {l_str:>8}  {wid}...")
    print()


# ================================================================
# メイン
# ================================================================

def run_dashboard(
    *,
    kpi_only:       bool = False,
    candidates_only:bool = False,
    watchlist_only: bool = False,
    limit:          int  = 20,
) -> dict:
    """
    ダッシュボードを表示し、KPI dict を返す。
    """
    client = get_client()
    kpi    = fetch_kpi(client)

    if not candidates_only and not watchlist_only:
        _print_kpi(kpi)

    if kpi_only:
        return kpi

    if not watchlist_only:
        candidates = fetch_candidates_with_watch(client, limit=limit)
        _print_candidates(candidates)

    if not candidates_only:
        _print_watchlist(client, limit=limit // 2 or 5)

    return kpi


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="コイン仕入れ Dashboard")
    parser.add_argument("--kpi-only",        action="store_true", help="KPI のみ表示")
    parser.add_argument("--candidates",      action="store_true", help="候補一覧のみ")
    parser.add_argument("--watchlist",       action="store_true", help="watchlist のみ")
    parser.add_argument("--limit",           type=int, default=20, help="表示件数上限")
    args = parser.parse_args()

    run_dashboard(
        kpi_only        = args.kpi_only,
        candidates_only = args.candidates,
        watchlist_only  = args.watchlist,
        limit           = args.limit,
    )


if __name__ == "__main__":
    main()
