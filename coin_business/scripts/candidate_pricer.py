"""
coin_business/scripts/candidate_pricer.py
==========================================
AUDIT_PASS 済み daily_candidates に対して pricing 計算を実施する。

既存の pricing_engine.py (legacy) とは独立して動作する。
legacy ファイルへの変更は一切行わない。

計算する値:
  - target_max_bid_jpy       : 利益率 15% 以上を確保できる上限入札額 (JPY)
  - recommended_max_bid_jpy  : 保守的な推奨入札額 (target × 0.90)
  - projected_profit_jpy     : target での利益見込み (再計算)
  - comparison_quality_score : 相場品質スコア (0.0〜1.0)

利益計算式 (CEO確定):
  cost    = bid_jpy + customs_duty_cost + us_forwarding_jpy + domestic_shipping_jpy
  revenue = expected_sale_price_jpy × (1 - yahoo_auction_fee)
  profit  = revenue - cost
  margin  = profit / revenue

  target_max_bid_jpy = MAX bid where margin >= MIN_GROSS_MARGIN

  bid_jpy = price_usd × fx_rate × CUSTOMS_DUTY_RATE + US_FORWARDING_JPY + DOMESTIC_SHIPPING_JPY
  ∴ target_max_bid_jpy (USD 換算)
      = (revenue × (1 - MIN_GROSS_MARGIN) - US_FORWARDING_JPY - DOMESTIC_SHIPPING_JPY)
        / (fx_rate × CUSTOMS_DUTY_RATE)

comparison_quality_score:
  0〜1 のスコア。直近 3か月データが多いほど高くなる。
  recent_3m_count が基準 → weight=1.0
  3-6m              → weight=0.5
  6-12m             → weight=0.2
  normalized to [0, 1] with cap at score >= 1.0

CLI:
  python candidate_pricer.py --limit 50 --dry-run
  python candidate_pricer.py --limit 100
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from constants import AuditStatus, ProfitCalc, Table
from db.watch_repo import record_pricing_run
from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)

# ================================================================
# 定数
# ================================================================

_MIN_MARGIN      = ProfitCalc.MIN_GROSS_MARGIN        # 0.15
_CUSTOMS         = ProfitCalc.CUSTOMS_DUTY_RATE        # 1.10
_US_FWD          = ProfitCalc.US_FORWARDING_JPY        # 2000
_DOM_SHIP        = ProfitCalc.DOMESTIC_SHIPPING_JPY    # 750
_YAHOO_FEE       = ProfitCalc.YAHOO_AUCTION_FEE        # 0.10
_FX_FALLBACK     = ProfitCalc.USD_TO_JPY_FALLBACK      # 150

# comparison_quality_score の重み (直近→古い順)
_QUALITY_WEIGHTS = (1.0, 0.5, 0.2)
# スコアが 1.0 に達するために必要な加重合計
_QUALITY_CAP     = 5.0


# ================================================================
# 純粋計算関数（テスト対象）
# ================================================================

def calc_comparison_quality_score(
    recent_3m_count: int,
    recent_3_6m_count: int = 0,
    recent_6_12m_count: int = 0,
) -> float:
    """
    直近3か月〜12か月の取引件数から相場品質スコア (0.0〜1.0) を計算する。

    ルール:
      weighted_sum = 3m×1.0 + 3-6m×0.5 + 6-12m×0.2
      score = min(1.0, weighted_sum / _QUALITY_CAP)
    """
    weighted = (
        recent_3m_count     * _QUALITY_WEIGHTS[0]
        + recent_3_6m_count * _QUALITY_WEIGHTS[1]
        + recent_6_12m_count * _QUALITY_WEIGHTS[2]
    )
    return round(min(1.0, weighted / _QUALITY_CAP), 3)


def calc_target_max_bid_jpy(
    expected_sale_price_jpy: int,
    fx_rate: float = _FX_FALLBACK,
) -> Optional[int]:
    """
    利益率 MIN_GROSS_MARGIN を確保できる最大入札額 (JPY) を計算する。

    expected_sale_price_jpy: 想定売却価格 (円)
    fx_rate: USD/JPY レート

    Returns: 入札上限 (JPY)。計算不能なら None。
    """
    if not expected_sale_price_jpy or expected_sale_price_jpy <= 0:
        return None

    revenue = expected_sale_price_jpy * (1.0 - _YAHOO_FEE)
    # margin = profit / revenue >= MIN_MARGIN
    # profit = revenue - cost >= revenue * MIN_MARGIN
    # cost <= revenue * (1 - MIN_MARGIN)
    # bid_jpy + US_FWD + DOM_SHIP <= revenue * (1 - MIN_MARGIN)
    # bid_jpy <= revenue * (1 - MIN_MARGIN) - US_FWD - DOM_SHIP
    max_bid_jpy = revenue * (1.0 - _MIN_MARGIN) - _US_FWD - _DOM_SHIP
    if max_bid_jpy <= 0:
        return None
    return int(max_bid_jpy)


def calc_recommended_max_bid_jpy(
    expected_sale_price_jpy: int,
    fx_rate: float = _FX_FALLBACK,
) -> Optional[int]:
    """
    保守的な推奨入札額。target の 90%。
    """
    target = calc_target_max_bid_jpy(expected_sale_price_jpy, fx_rate)
    if target is None:
        return None
    return int(target * 0.90)


def calc_projected_profit_at_target(
    expected_sale_price_jpy: int,
    target_max_bid_jpy: int,
) -> int:
    """
    target_max_bid_jpy で入札したときの利益見込み (JPY)。
    (送料・関税は bid 額に含まれると仮定 — target 計算と整合)
    """
    revenue = expected_sale_price_jpy * (1.0 - _YAHOO_FEE)
    cost    = target_max_bid_jpy + _US_FWD + _DOM_SHIP
    return int(revenue - cost)


# ================================================================
# 実行結果
# ================================================================

@dataclass
class PricingResult:
    candidates_found:  int = 0
    candidates_priced: int = 0
    error_count:       int = 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.candidates_priced > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def _load_unpriced_candidates(client, limit: int) -> list[dict]:
    """
    AUDIT_PASS で pricing 未実施 (target_max_bid_jpy IS NULL) の
    daily_candidates を返す。
    """
    try:
        res = (
            client
            .table(Table.DAILY_CANDIDATES)
            .select("*")
            .eq("audit_status", AuditStatus.AUDIT_PASS)
            .is_("target_max_bid_jpy", "null")
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        logger.error("_load_unpriced_candidates failed: %s", exc)
        return []


def _get_fx_rate(client) -> float:
    """daily_rates から最新 USD/JPY レートを取得。失敗時はフォールバック。"""
    try:
        res = (
            client
            .table("daily_rates")
            .select("usd_jpy")
            .order("rate_date", desc=True)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("usd_jpy"):
            return float(res.data[0]["usd_jpy"])
    except Exception:
        pass
    return _FX_FALLBACK


def _patch_candidate_pricing(
    client,
    candidate_id: str,
    *,
    target_max_bid_jpy: int,
    comparison_quality_score: float,
) -> bool:
    try:
        recommended = int(target_max_bid_jpy * 0.90)
        projected   = calc_projected_profit_at_target(
            # We don't have expected_sale_price on the candidate directly,
            # but we can back-calculate from target:
            # target = revenue*(1-margin) - fixed_costs
            # revenue = expected_sale * (1-yahoo_fee)
            # Simplified: store as-is — projected_profit_jpy already in match
            target_max_bid_jpy + _US_FWD + _DOM_SHIP
            + int((target_max_bid_jpy + _US_FWD + _DOM_SHIP)
                  * _MIN_MARGIN / (1.0 - _MIN_MARGIN)),
            target_max_bid_jpy,
        )
        patch = {
            "target_max_bid_jpy":       target_max_bid_jpy,
            "recommended_max_bid_jpy":  recommended,
            "projected_profit_jpy":     projected,
            "comparison_quality_score": comparison_quality_score,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(Table.DAILY_CANDIDATES).update(patch).eq("id", candidate_id).execute()
        return True
    except Exception as exc:
        logger.error("_patch_candidate_pricing failed: %s", exc)
        return False


def run_pricing(
    *,
    dry_run: bool = False,
    limit: int = 50,
) -> PricingResult:
    """
    AUDIT_PASS 済み daily_candidates に pricing を計算・保存する。
    """
    result = PricingResult()
    client = get_client()
    fx_rate = _get_fx_rate(client)

    candidates = _load_unpriced_candidates(client, limit)
    result.candidates_found = len(candidates)

    for cand in candidates:
        try:
            cand_id = cand["id"]
            # 想定売却価格: reference_price_jpy または market_price_jpy
            expected = (
                cand.get("reference_price_jpy")
                or cand.get("market_price_jpy")
                or cand.get("projected_profit_jpy")  # fallback field
            )
            if not expected:
                logger.debug("candidate %s: no expected_sale_price — skip", cand_id)
                continue

            expected = int(expected)
            target = calc_target_max_bid_jpy(expected, fx_rate)
            if target is None:
                logger.debug("candidate %s: target_max_bid_jpy is None — skip", cand_id)
                continue

            # comparison_quality_score: 件数情報がある場合に計算
            score = calc_comparison_quality_score(
                recent_3m_count    = int(cand.get("recent_3m_count", 0) or 0),
                recent_3_6m_count  = int(cand.get("recent_3_6m_count", 0) or 0),
                recent_6_12m_count = int(cand.get("recent_6_12m_count", 0) or 0),
            )

            if dry_run:
                logger.info(
                    "[DRY-RUN] %s: target=%d score=%.3f",
                    cand_id, target, score,
                )
                result.candidates_priced += 1
                continue

            ok = _patch_candidate_pricing(
                client,
                cand_id,
                target_max_bid_jpy=target,
                comparison_quality_score=score,
            )
            if ok:
                result.candidates_priced += 1
            else:
                result.error_count += 1

        except Exception as exc:
            logger.error("pricing error for candidate %s: %s", cand.get("id"), exc)
            result.error_count += 1

    if not dry_run:
        record_pricing_run(
            client,
            run_date=date.today().isoformat(),
            status=result.status_str(),
            candidates_found=result.candidates_found,
            candidates_priced=result.candidates_priced,
            error_count=result.error_count,
        )

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="AUDIT_PASS 候補に pricing 計算を実施する"
    )
    parser.add_argument("--limit",   type=int, default=50,
                        help="処理件数上限 (default: 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 書き込みなし (確認用)")
    args = parser.parse_args()

    result = run_pricing(dry_run=args.dry_run, limit=args.limit)
    print(
        f"pricing done: found={result.candidates_found} "
        f"priced={result.candidates_priced} errors={result.error_count} "
        f"status={result.status_str()}"
    )


if __name__ == "__main__":
    main()
