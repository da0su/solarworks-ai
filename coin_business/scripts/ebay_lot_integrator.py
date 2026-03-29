"""
ebay_lot_integrator.py  ─  eBay候補を overseas_lot 形式に変換して daily_candidates へ統合

ebay_auction_search.py が生成した ~/.slack_bridge/ebay_review_candidates.json を読み込み、
overseas_lot スキーマに変換して daily_candidates テーブルへ upsert する。

役割:
  - eBay (ceo_confirmed=True) は estimated_cost_jpy が実計算される
  - 他の海外オークション (ceo_confirmed=False) は estimated_cost_jpy=None
  - eBay の lot は "active auction" ではなく "仕入れ機会" として扱う

フロー:
  ebay_auction_search.py
    → ~/.slack_bridge/ebay_review_candidates.json
      → ebay_lot_integrator.py (このスクリプト)
        → overseas_lot schema + enrich_lot_with_cost()
          → daily_candidates (Supabase)

使い方:
  from scripts.ebay_lot_integrator import integrate_ebay_candidates

  result = integrate_ebay_candidates(dry_run=False)
  # → {"ok": 3, "review": 2, "ceo": 1, "ng": 5, "error": 0}

  # スタンドアロン
  python scripts/ebay_lot_integrator.py --dry-run
  python scripts/ebay_lot_integrator.py --fx 150.0
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ファイルパス
_SLACK_BRIDGE_DIR = Path.home() / ".slack_bridge"
EBAY_CANDIDATES_FILE = _SLACK_BRIDGE_DIR / "ebay_review_candidates.json"

# デフォルト為替レート
DEFAULT_USD_JPY = 150.0


# ── eBay候補ファイル読み込み ─────────────────────────────────────

def load_ebay_candidates() -> list[dict]:
    """
    ~/.slack_bridge/ebay_review_candidates.json からeBay候補を読み込む。
    ファイルが存在しない場合は空リスト。
    """
    if not EBAY_CANDIDATES_FILE.exists():
        logger.info(f"  [eBay integrator] 候補ファイルなし: {EBAY_CANDIDATES_FILE}")
        return []

    try:
        data = json.loads(EBAY_CANDIDATES_FILE.read_text(encoding="utf-8"))
        candidates = data.get("candidates", [])
        logger.info(
            f"  [eBay integrator] {len(candidates)}件読み込み "
            f"(searched_at={data.get('searched_at', 'N/A')})"
        )
        return candidates
    except Exception as e:
        logger.warning(f"  [eBay integrator] ファイル読み込みエラー: {e}")
        return []


# ── eBay候補 → overseas_lot スキーマ変換 ─────────────────────────

def ebay_candidate_to_overseas_lot(
    candidate: dict,
    fx_rate: float = DEFAULT_USD_JPY,
) -> dict:
    """
    ebay_auction_search.py の候補 dict を overseas_lot スキーマに変換。

    eBay候補フィールド:
      mgmt_no, ebay_title, api_price_usd, ebay_limit_usd, ebay_limit_jpy,
      ebay_url, db_grader, db_grade, db_line1, db_line2, db_material,
      bid_count, approved

    overseas_lot フィールドとのマッピング:
      source            = "ebay"
      auction_house     = "eBay"
      lot_title         = ebay_title
      lot_url           = ebay_url
      current_price     = api_price_usd (None → 0.0)
      currency          = "USD"
      management_no     = mgmt_no
      buy_limit_jpy     = ebay_limit_jpy
      coin_match_status = "matched" (DB管理番号と紐付け済み)
      match_score       = 1.0 (eBayは完全一致で候補選定されている)
    """
    from scripts.auction_cost_calculator import enrich_lot_with_cost

    price_usd  = float(candidate.get("api_price_usd") or 0.0)
    limit_jpy  = float(candidate.get("ebay_limit_jpy") or 0.0)
    limit_usd  = float(candidate.get("ebay_limit_usd") or 0.0)

    # buy_limit_jpy: ebay_limit_jpy が設定されていればそれを使う
    # なければ ebay_limit_usd * fx_rate で換算
    buy_limit_jpy = limit_jpy if limit_jpy > 0 else (limit_usd * fx_rate if limit_usd > 0 else None)

    # DB情報をタイトルに付加（lot_title は照合確認に使う）
    db_info = " | ".join(filter(None, [
        candidate.get("db_line1"),
        candidate.get("db_line2"),
        candidate.get("db_grader"),
        candidate.get("db_grade"),
    ]))
    lot_title = candidate.get("ebay_title") or db_info or "Unknown eBay Lot"

    # ai_comment に DB情報を記録
    ai_comment = f"DB: {db_info}" if db_info else None

    lot: dict = {
        # ── 出所情報
        "source":         "ebay",
        "auction_house":  "eBay",
        "auction_id":     "ebay_search",     # eBay は単一の "marketplace"
        "auction_name":   "eBay Marketplace",

        # ── ロット情報
        "lot_number":     "",                # eBay には lot番号なし
        "lot_title":      lot_title,
        "lot_url":        candidate.get("ebay_url") or "",

        # ── 価格情報
        "current_price":  price_usd,
        "realized_price": None,
        "currency":       "USD",
        "price_jpy":      int(price_usd * fx_rate) if price_usd > 0 else 0,
        "fx_rate":        fx_rate,

        # ── 時間情報
        "start_date":     None,
        "end_date":       None,
        "lot_end_time":   None,

        # ── マッチング (eBay は管理番号と紐付け済み)
        "coin_match_status": "matched",
        "management_no":     candidate.get("mgmt_no"),
        "match_score":       1.0,            # eBay候補はDB完全一致で選定

        # ── 判定 (candidates_writer で設定)
        "judgment":        "pending",
        "judgment_reason": ai_comment,
        "buy_limit_jpy":   buy_limit_jpy,

        # ── 運用メタ
        "priority":          2,              # eBay は P2 (Heritage P3より低優先)
        "is_active_auction": False,          # eBay は "オークション開催中" ではなく "仕入機会"
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "status":            "pending",

        # ── eBay固有追加情報
        "bid_count":         candidate.get("bid_count"),
        "db_material":       candidate.get("db_material"),

        # ── dedup (candidates_writer で生成)
        "dedup_key": None,
    }

    # コスト計算: eBay は ceo_confirmed=True → estimated_cost_jpy が実計算される
    lot = enrich_lot_with_cost(
        lot,
        fx_rate=fx_rate,
        buy_limit_jpy=buy_limit_jpy,
        require_confirmed=True,
    )

    return lot


# ── メイン統合関数 ─────────────────────────────────────────────

def integrate_ebay_candidates(
    fx_rate: float = DEFAULT_USD_JPY,
    dry_run: bool = False,
    only_approved: bool = False,
) -> dict:
    """
    eBay候補を overseas_lot 形式に変換して daily_candidates へ統合。

    Args:
        fx_rate        : USD/JPY 為替レート
        dry_run        : True の場合、DB書き込みと Slack通知をスキップ
        only_approved  : True の場合、approved=True の候補のみ統合

    Returns:
        write_candidates() の戻り値
        {"ok": int, "review": int, "ceo": int, "ng": int, "error": int}
    """
    from scripts.candidates_writer import write_candidates

    raw_candidates = load_ebay_candidates()
    if not raw_candidates:
        return {"ok": 0, "review": 0, "ceo": 0, "ng": 0, "error": 0, "total": 0}

    # フィルタ (オプション)
    if only_approved:
        raw_candidates = [c for c in raw_candidates if c.get("approved")]
        logger.info(f"  [eBay integrator] 承認済みのみ: {len(raw_candidates)}件")

    # overseas_lot 変換
    overseas_lots: list[dict] = []
    for cand in raw_candidates:
        try:
            lot = ebay_candidate_to_overseas_lot(cand, fx_rate=fx_rate)
            overseas_lots.append(lot)
        except Exception as e:
            logger.warning(f"  [eBay integrator] 変換エラー: {cand.get('mgmt_no')} — {e}")

    logger.info(f"  [eBay integrator] 変換完了: {len(overseas_lots)}件")

    if dry_run:
        logger.info("  [eBay integrator] DRY-RUN: 書き込みスキップ")
        for lot in overseas_lots[:3]:
            mgmt = lot.get("management_no", "?")
            cost = lot.get("estimated_cost_jpy")
            limit = lot.get("buy_limit_jpy")
            margin = lot.get("estimated_margin_pct")
            cost_str = f"cost={cost:,.0f}" if cost else "cost=N/A"
            limit_str = f"limit={limit:,.0f}" if limit else "limit=N/A"
            margin_str = f"margin={margin:.1%}" if margin else ""
            logger.info(
                f"    [{mgmt}] {lot['lot_title'][:45]} "
                f"USD{lot['current_price']:,.0f} "
                f"{cost_str} {limit_str} {margin_str}"
            )
        return {
            "ok": 0, "review": 0, "ceo": 0, "ng": 0,
            "error": 0, "total": len(overseas_lots), "dry_run": True,
        }

    # daily_candidates へ書き込み
    result = write_candidates(overseas_lots, dry_run=False)
    logger.info(
        f"  [eBay integrator] 書き込み完了: "
        f"OK={result.get('ok', 0)} REVIEW={result.get('review', 0)} "
        f"CEO={result.get('ceo', 0)} NG={result.get('ng', 0)}"
    )
    return result


# ── スタンドアロン実行 ────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="eBay候補を daily_candidates へ統合")
    parser.add_argument("--dry-run", action="store_true", help="DB書き込みなし")
    parser.add_argument("--fx",      type=float, default=DEFAULT_USD_JPY, help="USD/JPY 為替レート")
    parser.add_argument("--approved-only", action="store_true", help="承認済みのみ統合")
    parser.add_argument("--show",    action="store_true", help="候補一覧表示のみ")
    args = parser.parse_args()

    if args.show:
        candidates = load_ebay_candidates()
        print(f"\n=== eBay候補一覧 ({len(candidates)}件) ===\n")
        for c in candidates:
            mgmt  = c.get("mgmt_no", "?")
            title = c.get("ebay_title", "")[:55]
            price = c.get("api_price_usd")
            limit = c.get("ebay_limit_jpy", 0)
            appr  = c.get("approved")
            appr_str = "APPROVED" if appr else ("PENDING" if appr is None else "REJECTED")
            price_str = f"USD {price:,.0f}" if price else "BIN/N.A."
            print(f"  [{mgmt}] {appr_str:8} | {price_str:>10} | limit=JPY{limit:,.0f} | {title}")
        raise SystemExit(0)

    result = integrate_ebay_candidates(
        fx_rate=args.fx,
        dry_run=args.dry_run,
        only_approved=args.approved_only,
    )

    print(f"\n=== eBay統合結果 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
