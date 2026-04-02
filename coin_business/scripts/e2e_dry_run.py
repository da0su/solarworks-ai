"""
coin_business/scripts/e2e_dry_run.py
======================================
Phase 1〜10 E2E dry run スクリプト。
実際の DB 書き込みや Slack/Notion 送信を一切行わず、
各ステージの処理が通ることを確認する。

ステージ:
  Stage 1  Yahoo staging      — yahoo_sold_sync.py
  Stage 2  Yahoo 昇格         — yahoo_promoter.py
  Stage 3  Seed 生成          — seed_generator.py
  Stage 4  eBay スキャン      — ebay_seed_scanner.py
  Stage 5  eBay Ingest        — ebay_api_ingest.py
  Stage 6  Global Sync        — global_auction_sync.py
  Stage 7  Global Ingest      — global_lot_ingest.py
  Stage 8  Match Engine       — match_engine.py
  Stage 9  CAP Audit          — cap_audit_runner.py
  Stage 10 Pricing            — candidate_pricer.py
  Stage 11 Keep Watch         — keep_watch_refresher.py
  Stage 12 Slack Morning Brief— slack_notifier.py
  Stage 13 Notion Sync        — notion_sync.py
  Stage 14 Dashboard          — dashboard.py

CLI:
  python e2e_dry_run.py               # 全ステージ dry run
  python e2e_dry_run.py --stage 8     # Stage 8 のみ
  python e2e_dry_run.py --from 5      # Stage 5 以降
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


# ================================================================
# ステージ定義
# ================================================================

@dataclass
class StageResult:
    stage_no:   int
    name:       str
    status:     str       # "ok" | "skip" | "error"
    elapsed_ms: int = 0
    detail:     str = ""


def _run_stage(stage_no: int, name: str, fn) -> StageResult:
    logger.info("▶ Stage %02d %s ...", stage_no, name)
    t0 = time.perf_counter()
    try:
        detail = fn() or ""
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info("  ✅ Stage %02d OK (%dms) %s", stage_no, elapsed, detail)
        return StageResult(stage_no, name, "ok", elapsed, detail)
    except Exception as exc:
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.error("  ❌ Stage %02d ERROR (%dms): %s", stage_no, elapsed, exc)
        return StageResult(stage_no, name, "error", elapsed, str(exc))


# ================================================================
# 各ステージの dry run 実装
# ================================================================

def _stage_yahoo_sync() -> str:
    from scripts.yahoo_sold_sync import run_sync
    result = run_sync(dry_run=True, limit=5)
    return f"fetched={result.fetched_count} upserted={result.upserted_count}"


def _stage_yahoo_promote() -> str:
    from scripts.yahoo_promoter import run_promotion
    result = run_promotion(dry_run=True, limit=5)
    return f"promoted={result.promoted_count}"


def _stage_seed_generate() -> str:
    from scripts.seed_generator import run_seed_generation
    result = run_seed_generation(dry_run=True, limit=5)
    return f"generated={getattr(result, 'generated_count', '?')}"


def _stage_ebay_scan() -> str:
    from scripts.ebay_seed_scanner import run_scan
    result = run_scan(dry_run=True, limit=3)
    return f"scanned={getattr(result, 'scanned_count', '?')}"


def _stage_ebay_ingest() -> str:
    from scripts.ebay_api_ingest import run_ingest
    result = run_ingest(dry_run=True, limit=3)
    return f"ingested={getattr(result, 'ingested_count', '?')}"


def _stage_global_sync() -> str:
    from scripts.global_auction_sync import run_sync as run_gsync
    result = run_gsync(dry_run=True)
    return f"synced={getattr(result, 'synced_count', '?')}"


def _stage_global_ingest() -> str:
    from scripts.global_lot_ingest import run_ingest as run_gingest
    result = run_gingest(dry_run=True, limit=5)
    return f"ingested={getattr(result, 'ingested_count', '?')}"


def _stage_match_engine() -> str:
    from scripts.match_engine import run_match_engine
    result = run_match_engine(dry_run=True, limit=5)
    return (
        f"listings={result.listings_scanned} lots={result.lots_scanned} "
        f"matches={result.matches_created} level_a={result.level_a_count}"
    )


def _stage_cap_audit() -> str:
    from scripts.cap_audit_runner import run_audit
    result = run_audit(dry_run=True, limit=5)
    return (
        f"audited={result.audited_count} pass={result.audit_pass_count} "
        f"hold={result.audit_hold_count} fail={result.audit_fail_count}"
    )


def _stage_pricing() -> str:
    from scripts.candidate_pricer import run_pricing
    result = run_pricing(dry_run=True, limit=5)
    return f"found={result.candidates_found} priced={result.candidates_priced}"


def _stage_keep_watch() -> str:
    from scripts.keep_watch_refresher import run_keep_watch
    result = run_keep_watch(dry_run=True, limit=10)
    return (
        f"checked={result.items_checked} bid_ready={result.bid_ready_count} "
        f"ended={result.ended_count}"
    )


def _stage_slack_notify() -> str:
    from scripts.supabase_client import get_client
    from scripts.slack_notifier import notify_morning_brief
    client = get_client()
    result = notify_morning_brief(client, dry_run=True)
    return f"status={result.get('status')} kpi_keys={list(result.get('kpi', {}).keys())}"


def _stage_notion_sync() -> str:
    from scripts.notion_sync import run_notion_sync
    result = run_notion_sync(dry_run=True, limit=5)
    return (
        f"candidates={result.candidates_synced} "
        f"watchlist={result.watchlist_synced} "
        f"errors={result.error_count}"
    )


def _stage_dashboard() -> str:
    from scripts.dashboard import run_dashboard
    kpi = run_dashboard(kpi_only=True)
    return (
        f"yahoo_pending={kpi.get('yahoo_pending_count', '?')} "
        f"audit_pass={kpi.get('audit_pass_count', '?')} "
        f"bid_ready={kpi.get('bid_ready_count', '?')}"
    )


# ================================================================
# ステージ一覧
# ================================================================

STAGES = [
    (1,  "Yahoo Staging Sync",   _stage_yahoo_sync),
    (2,  "Yahoo Promoter",       _stage_yahoo_promote),
    (3,  "Seed Generator",       _stage_seed_generate),
    (4,  "eBay Seed Scanner",    _stage_ebay_scan),
    (5,  "eBay API Ingest",      _stage_ebay_ingest),
    (6,  "Global Auction Sync",  _stage_global_sync),
    (7,  "Global Lot Ingest",    _stage_global_ingest),
    (8,  "Match Engine",         _stage_match_engine),
    (9,  "CAP Audit",            _stage_cap_audit),
    (10, "Pricing Engine",       _stage_pricing),
    (11, "Keep Watch Refresher", _stage_keep_watch),
    (12, "Slack Morning Brief",  _stage_slack_notify),
    (13, "Notion Sync",          _stage_notion_sync),
    (14, "Dashboard",            _stage_dashboard),
]


# ================================================================
# メイン実行
# ================================================================

def run_e2e(
    *,
    from_stage: int = 1,
    to_stage:   int = 14,
    only_stage: Optional[int] = None,
) -> list[StageResult]:
    """
    E2E dry run を実行し、各ステージの結果を返す。
    """
    results: list[StageResult] = []

    for stage_no, name, fn in STAGES:
        if only_stage is not None and stage_no != only_stage:
            continue
        if stage_no < from_stage or stage_no > to_stage:
            results.append(StageResult(stage_no, name, "skip"))
            continue
        result = _run_stage(stage_no, name, fn)
        results.append(result)

    return results


def _print_summary(results: list[StageResult]) -> None:
    ok    = sum(1 for r in results if r.status == "ok")
    error = sum(1 for r in results if r.status == "error")
    skip  = sum(1 for r in results if r.status == "skip")

    print("\n" + "=" * 70)
    print("E2E Dry Run Summary")
    print("=" * 70)
    for r in results:
        icon = {"ok": "✅", "error": "❌", "skip": "⏭"}.get(r.status, "?")
        detail = f" — {r.detail}" if r.detail else ""
        elapsed = f" ({r.elapsed_ms}ms)" if r.elapsed_ms else ""
        print(f"  {icon} Stage {r.stage_no:02d} {r.name}{elapsed}{detail}")

    print("-" * 70)
    print(f"  Total: {ok} ok / {error} error / {skip} skip")
    if error > 0:
        print("  ⚠️  一部ステージでエラーが発生しました。詳細は上記ログを確認してください。")
    else:
        print("  🎉 全ステージ完了!")
    print()


# ================================================================
# CLI
# ================================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Phase 1〜10 E2E dry run"
    )
    parser.add_argument("--stage",  type=int, help="特定ステージのみ実行")
    parser.add_argument("--from",   type=int, default=1,  dest="from_stage",
                        help="開始ステージ番号")
    parser.add_argument("--to",     type=int, default=14, dest="to_stage",
                        help="終了ステージ番号")
    args = parser.parse_args()

    results = run_e2e(
        from_stage  = args.from_stage,
        to_stage    = args.to_stage,
        only_stage  = args.stage,
    )
    _print_summary(results)

    # エラーがあれば exit code 1
    has_error = any(r.status == "error" for r in results)
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
