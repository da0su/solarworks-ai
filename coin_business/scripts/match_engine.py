"""
coin_business/scripts/match_engine.py
========================================
eBay seed hit / 世界オークション lot を Yahoo 正式母集団 (yahoo_coin_seeds) と
照合し、candidate_match_results に判定結果を保存する。

照合レベル:
  Level A  : 仕入れ対象 (以下の3条件のいずれか)
               1) cert_company + cert_number 完全一致
               2) Yahoo 基準より高グレードで利益条件を満たす
               3) 年代差 ±5年 以内で利益条件を満たす
  Level B  : 価格参考のみ (タイトル類似 or 条件不完全)
  Level C  : 無関係。除外。

保存フィールド:
  match_type, match_reason, match_score,
  cert_match_flag, grade_advantage_flag, year_tolerance_flag,
  projected_profit_jpy, candidate_level_bot, bot_match_details

CLI オプション:
  --dry-run       : DB 書き込みなし
  --smoke         : eBay 1件 + global_lot 1件のみ処理
  --limit N       : 処理 listing / lot 件数上限
  --source        : 'ebay' | 'global' | 'all' (デフォルト all)
  --status-only   : 未処理件数を表示して終了
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from constants import (
    CandidateLevel,
    MatchType,
    ProfitCalc,
    Table,
)
from db.match_repo import (
    load_pending_ebay_listings,
    load_active_global_lots,
    load_active_seeds,
    upsert_match_result,
    update_listing_match_status,
    record_match_run,
)

logger = logging.getLogger(__name__)

# ================================================================
# グレード順位マップ（スコア比較用）
# ================================================================

_GRADE_RANK: dict[str, int] = {
    "P1": 1, "FR2": 2, "AG3": 3, "G4": 4, "G6": 6,
    "VG8": 8, "VG10": 10,
    "F12": 12, "F15": 15,
    "VF20": 20, "VF25": 25, "VF30": 30, "VF35": 35,
    "EF40": 40, "EF45": 45,
    "AU50": 50, "AU53": 53, "AU55": 55, "AU58": 58,
    "MS60": 60, "MS61": 61, "MS62": 62, "MS63": 63,
    "MS64": 64, "MS65": 65, "MS66": 66, "MS67": 67,
    "MS68": 68, "MS69": 69, "MS70": 70,
    "PF60": 60, "PF61": 61, "PF62": 62, "PF63": 63,
    "PF64": 64, "PF65": 65, "PF66": 66, "PF67": 67,
    "PF68": 68, "PF69": 69, "PF70": 70,
}


def _grade_rank(grade: str | None) -> int:
    """グレード文字列を数値ランクに変換する。未知グレードは 0。"""
    if not grade:
        return 0
    g = grade.strip().upper()
    # "MS-63" → "MS63" 正規化
    g = g.replace("-", "").replace(" ", "")
    return _GRADE_RANK.get(g, 0)


# ================================================================
# 利益計算
# ================================================================

def calc_projected_profit_jpy(
    price_usd:     float | None,
    ref_price_jpy: int,
    fx_rate:       float = ProfitCalc.USD_TO_JPY_FALLBACK,
) -> int:
    """
    仕入れ見込み利益（円）を計算する。

    コスト = price_usd × fx_rate × 関税率 + 米国転送費 + 国内送料
    収益   = ref_price_jpy × (1 − ヤフオク手数料)
    利益   = 収益 − コスト

    Args:
        price_usd:     現在入札額 or 見積下限（USD）
        ref_price_jpy: Yahoo 参考落札価格（円）
        fx_rate:       USD/JPY レート

    Returns:
        見込み利益（円）。入力 None の場合は 0 を返す。
    """
    if not price_usd or price_usd <= 0:
        return 0
    cost_jpy = (
        price_usd * fx_rate * ProfitCalc.CUSTOMS_DUTY_RATE
        + ProfitCalc.US_FORWARDING_JPY
        + ProfitCalc.DOMESTIC_SHIPPING_JPY
    )
    revenue_jpy = ref_price_jpy * (1.0 - ProfitCalc.YAHOO_AUCTION_FEE)
    return int(revenue_jpy - cost_jpy)


# ================================================================
# 照合ロジック
# ================================================================

def _match_one(
    listing: dict,
    seeds:   list[dict],
    source_type: str,   # 'ebay_listing' | 'global_lot'
) -> list[dict]:
    """
    1 listing/lot に対して全 seed と照合し、マッチ結果 dict のリストを返す。

    各結果 dict は candidate_match_results の upsert に渡す形式。
    """
    results: list[dict] = []

    # listing の各フィールド
    l_cert_company = (listing.get("grader") or listing.get("cert_company") or "").upper()
    l_cert_number  = (listing.get("cert_number") or "").strip()
    l_grade        = listing.get("grade") or listing.get("grade_text") or ""
    l_year_raw     = listing.get("year")
    l_year         = int(l_year_raw) if l_year_raw else None

    # 価格 (USD): eBay は current_price_usd, global_lot は current_bid_usd or estimate_low_usd
    if source_type == "ebay_listing":
        l_price_usd = listing.get("current_price_usd")
        l_id_key    = "ebay_listing_id"
    else:
        l_price_usd = listing.get("current_bid_usd") or listing.get("estimate_low_usd")
        l_id_key    = "global_lot_id"

    listing_id = listing.get("id", "")

    for seed in seeds:
        s_cert_company = (seed.get("cert_company") or "").upper()
        s_cert_number  = (seed.get("cert_number") or "").strip()
        s_grade_min    = seed.get("grade_min") or ""
        s_year_min     = seed.get("year_min")
        s_year_max     = seed.get("year_max")
        s_grader       = (seed.get("grader") or "").upper()
        ref_price_jpy  = int(seed.get("ref_price_jpy") or 0)
        seed_id        = seed.get("id", "")

        match_type            = None
        match_score           = 0.0
        cert_match_flag       = False
        grade_advantage_flag  = False
        year_tolerance_flag   = False
        match_reason          = ""
        projected_profit_jpy  = 0

        # ── Level A 判定 ──────────────────────────────────────

        # 1) CERT_EXACT: cert_company + cert_number 完全一致
        if (
            l_cert_number
            and s_cert_number
            and l_cert_number == s_cert_number
            and (not s_cert_company or l_cert_company == s_cert_company)
        ):
            projected_profit_jpy = calc_projected_profit_jpy(l_price_usd, ref_price_jpy)
            match_type           = MatchType.CERT_EXACT
            match_score          = 1.0
            cert_match_flag      = True
            match_reason         = (
                f"cert={l_cert_company}/{l_cert_number} 完全一致"
            )

        # 2) HIGH_GRADE: listing grade > seed.grade_min AND 利益条件
        elif (
            s_grade_min
            and _grade_rank(l_grade) > _grade_rank(s_grade_min)
            and (not s_grader or l_cert_company in (s_grader, ""))
        ):
            pp = calc_projected_profit_jpy(l_price_usd, ref_price_jpy)
            if pp >= CandidateLevel.MIN_PROFIT_JPY:
                projected_profit_jpy  = pp
                match_type            = MatchType.HIGH_GRADE
                match_score           = 0.8
                grade_advantage_flag  = True
                match_reason          = (
                    f"grade {l_grade} > seed.grade_min {s_grade_min},"
                    f" profit=¥{pp:,}"
                )

        # 3) YEAR_DELTA: ±5年 AND 利益条件
        elif (
            l_year is not None
            and s_year_min is not None
            and s_year_max is not None
        ):
            seed_year_mid = (int(s_year_min) + int(s_year_max)) / 2
            delta = abs(l_year - seed_year_mid)
            if delta <= CandidateLevel.YEAR_DELTA_MAX:
                pp = calc_projected_profit_jpy(l_price_usd, ref_price_jpy)
                if pp >= CandidateLevel.MIN_PROFIT_JPY:
                    projected_profit_jpy = pp
                    match_type           = MatchType.YEAR_DELTA
                    match_score          = 0.6
                    year_tolerance_flag  = True
                    match_reason         = (
                        f"year {l_year} ≈ seed {int(s_year_min)}-{int(s_year_max)}"
                        f" (Δ={delta:.0f}y), profit=¥{pp:,}"
                    )

        # ── Level B: TITLE_FUZZY (フォールバック) ─────────────
        if match_type is None:
            # タイトル類似は Level B のみ: スコア低め
            match_type   = MatchType.TITLE_FUZZY
            match_score  = 0.2
            match_reason = "title_fuzzy (補助参考)"

        # Level 決定
        if match_type in MatchType.LEVEL_A_TYPES:
            level = CandidateLevel.A
        else:
            level = CandidateLevel.B

        bot_match_details = {
            "match_type":           match_type,
            "match_score":          match_score,
            "cert_match_flag":      cert_match_flag,
            "grade_advantage_flag": grade_advantage_flag,
            "year_tolerance_flag":  year_tolerance_flag,
            "projected_profit_jpy": projected_profit_jpy,
            "listing_grade":        l_grade,
            "seed_grade_min":       s_grade_min,
            "listing_year":         l_year,
            "seed_year_mid":        (
                (int(s_year_min) + int(s_year_max)) / 2
                if s_year_min and s_year_max else None
            ),
        }

        rec: dict = {
            "source_type":           source_type,
            l_id_key:                listing_id,
            "seed_id":               seed_id,
            "match_type":            match_type,
            "match_score":           round(match_score, 3),
            "candidate_level_bot":   level,
            "match_reason":          match_reason,
            "cert_match_flag":       cert_match_flag,
            "grade_advantage_flag":  grade_advantage_flag,
            "year_tolerance_flag":   year_tolerance_flag,
            "projected_profit_jpy":  projected_profit_jpy,
            "bot_match_details":     bot_match_details,
        }
        results.append(rec)

    return results


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class MatchResult:
    listings_scanned: int = 0
    lots_scanned:     int = 0
    matches_created:  int = 0
    level_a_count:    int = 0
    level_b_count:    int = 0
    level_c_count:    int = 0
    error_count:      int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.matches_created > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def run_match(
    dry_run: bool = False,
    smoke:   bool = False,
    limit:   int  = 100,
    source:  str  = "all",
) -> MatchResult:
    """
    全 listing / lot × seed の照合を実行する。

    Args:
        dry_run: True = DB 書き込みなし
        smoke:   True = eBay 1件 + global_lot 1件のみ処理
        limit:   処理件数上限
        source:  'ebay' | 'global' | 'all'

    Returns:
        MatchResult
    """
    result = MatchResult()
    client = get_client()

    # seed は全件読み込み（照合の基準）
    seeds = load_active_seeds(client, limit=500)
    if not seeds:
        logger.info("active seed が 0 件 — 終了")
        return result

    logger.info("active seeds: %d 件", len(seeds))

    _limit = 1 if smoke else limit

    # ── eBay listing ──────────────────────────────────────────
    if source in ("ebay", "all"):
        listings = load_pending_ebay_listings(client, limit=_limit)
        logger.info("pending eBay listings: %d 件", len(listings))

        for listing in listings:
            listing_id = listing.get("id", "")
            try:
                match_recs = _match_one(listing, seeds, source_type="ebay_listing")
            except Exception as exc:
                logger.error("[eBay] match_one 例外 id=%s: %s", listing_id, exc)
                result.error_count += 1
                result.errors.append(str(exc))
                continue

            result.listings_scanned += 1
            any_a = False

            for rec in match_recs:
                level = rec.get("candidate_level_bot", CandidateLevel.C)
                if level == CandidateLevel.A:
                    result.level_a_count += 1
                    any_a = True
                elif level == CandidateLevel.B:
                    result.level_b_count += 1
                else:
                    result.level_c_count += 1

                if dry_run:
                    logger.debug(
                        "  [DRY-RUN] eBay %s × seed %s → %s (%.2f)",
                        listing_id[:8], rec.get("seed_id", "")[:8],
                        level, rec.get("match_score", 0),
                    )
                    result.matches_created += 1
                    continue

                saved = upsert_match_result(client, rec)
                if saved:
                    result.matches_created += 1

            if not dry_run:
                new_status = "matched" if any_a else "no_match"
                update_listing_match_status(client, listing_id, new_status)

    # ── global_auction_lot ───────────────────────────────────
    if source in ("global", "all"):
        lots = load_active_global_lots(client, limit=_limit)
        logger.info("active global lots: %d 件", len(lots))

        for lot in lots:
            lot_id = lot.get("id", "")
            try:
                match_recs = _match_one(lot, seeds, source_type="global_lot")
            except Exception as exc:
                logger.error("[global_lot] match_one 例外 id=%s: %s", lot_id, exc)
                result.error_count += 1
                result.errors.append(str(exc))
                continue

            result.lots_scanned += 1

            for rec in match_recs:
                level = rec.get("candidate_level_bot", CandidateLevel.C)
                if level == CandidateLevel.A:
                    result.level_a_count += 1
                elif level == CandidateLevel.B:
                    result.level_b_count += 1
                else:
                    result.level_c_count += 1

                if dry_run:
                    logger.debug(
                        "  [DRY-RUN] global_lot %s × seed %s → %s",
                        lot_id[:8], rec.get("seed_id", "")[:8], level,
                    )
                    result.matches_created += 1
                    continue

                saved = upsert_match_result(client, rec)
                if saved:
                    result.matches_created += 1

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "match_engine.py",
        description = "eBay listing / global lot を Yahoo seed と照合する",
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="DB 書き込みなし")
    parser.add_argument("--smoke",       action="store_true",
                        help="eBay 1件 + global_lot 1件のみ処理")
    parser.add_argument("--limit",       type=int, default=100,
                        help="処理件数上限 (デフォルト 100)")
    parser.add_argument("--source",      choices=["ebay", "global", "all"], default="all",
                        help="処理対象ソース (デフォルト all)")
    parser.add_argument("--status-only", action="store_true",
                        help="未処理件数を表示して終了")
    args = parser.parse_args()

    if args.status_only:
        client = get_client()
        listings = load_pending_ebay_listings(client, limit=1)
        lots     = load_active_global_lots(client, limit=1)
        seeds    = load_active_seeds(client, limit=1)
        print(f"pending eBay listings: (exists={bool(listings)})")
        print(f"active global lots:    (exists={bool(lots)})")
        print(f"active seeds:          (exists={bool(seeds)})")
        return

    result = run_match(
        dry_run = args.dry_run,
        smoke   = args.smoke,
        limit   = args.limit,
        source  = args.source,
    )

    if not args.dry_run:
        client = get_client()
        record_match_run(
            client           = client,
            run_date         = date.today().isoformat(),
            status           = result.status_str(),
            listings_scanned = result.listings_scanned,
            lots_scanned     = result.lots_scanned,
            matches_created  = result.matches_created,
            level_a_count    = result.level_a_count,
            level_b_count    = result.level_b_count,
            level_c_count    = result.level_c_count,
            error_count      = result.error_count,
            error_message    = "; ".join(result.errors[:5]) if result.errors else None,
        )

    print(
        f"\n=== Match Engine {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  listings_scanned: {result.listings_scanned}\n"
        f"  lots_scanned:     {result.lots_scanned}\n"
        f"  matches_created:  {result.matches_created}\n"
        f"  level_a_count:    {result.level_a_count}\n"
        f"  level_b_count:    {result.level_b_count}\n"
        f"  level_c_count:    {result.level_c_count}\n"
        f"  error_count:      {result.error_count}\n"
        f"  status:           {result.status_str()}"
    )


if __name__ == "__main__":
    main()
