"""
coin_business/scripts/cap_audit_runner.py
==========================================
BOT 抽出結果 (candidate_match_results Level A) に対して
AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL を付与し、
AUDIT_PASS のみ daily_candidates に昇格する。

監査チェック項目 (AuditCheck):
  FAIL 条件 (1つでも → AUDIT_FAIL):
    profit_condition   : projected_profit_jpy >= 0 でない
    shipping_valid     : eBay の発送元が US / UK 以外
    lot_size_single    : タイトルに "LOT OF" / "2X" / "×2" 等の複数表記
    not_sold           : is_sold = True
    not_ended          : end_time / lot_end_at が過去

  WARN 条件 (1つ以上でFAIL なし → AUDIT_HOLD):
    cert_validity      : cert_match_flag=True なのに cert_number が None
    title_consistency  : タイトルに矛盾キーワード
    grade_delta        : 評価グレード差が大きすぎる (>5 notch)
    year_delta         : 年代差が大きい (>3年)
    not_stale          : 6h 以内に取得されていない

昇格処理:
  AUDIT_PASS → daily_candidates に upsert し、
               candidate_match_results.promoted_candidate_id をセット

CLI オプション:
  --dry-run       : DB 書き込みなし
  --limit N       : 処理 match 件数上限
  --status-only   : 未審査 Level A 件数を表示して終了
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from constants import (
    AuditCheck,
    AuditStatus,
    CandidateLevel,
    CeoDecision,
    Table,
)
from db.match_repo import (
    load_unaudited_level_a,
    update_audit_result,
    set_promoted_candidate,
    record_audit_run,
)

logger = logging.getLogger(__name__)

# 発送元として有効な国コード / 国名 (eBay のみ)
_VALID_SHIPPING_ORIGINS = frozenset([
    "US", "GB", "UNITED STATES", "UNITED KINGDOM", "UK",
])

# stale 判定: 6 時間以内に取得されていること
_STALE_THRESHOLD_HOURS = 6

# タイトルに含まれる「複数品 lot」の判定キーワード
_MULTI_LOT_KEYWORDS = (
    "LOT OF", "2X", "×2", "X2", "3X", "4X", "5X",
    " 2 COINS", " 3 COINS", " SET OF",
)


# ================================================================
# 監査チェック関数群
# ================================================================

def _check_profit_condition(match: dict) -> str:
    """projected_profit_jpy >= 0 なら pass。"""
    profit = match.get("projected_profit_jpy")
    if profit is None:
        return AuditCheck.CHECK_RESULT_SKIP
    return (
        AuditCheck.CHECK_RESULT_PASS
        if profit >= 0
        else AuditCheck.CHECK_RESULT_FAIL
    )


def _check_shipping_valid(match: dict, listing: dict | None) -> str:
    """
    eBay listing は発送元が US / UK のみ許可。
    global_lot は skip。
    """
    if match.get("source_type") != "ebay_listing":
        return AuditCheck.CHECK_RESULT_SKIP
    if listing is None:
        return AuditCheck.CHECK_RESULT_SKIP
    origin = (listing.get("shipping_from_country") or "").upper().strip()
    if not origin:
        return AuditCheck.CHECK_RESULT_WARN
    return (
        AuditCheck.CHECK_RESULT_PASS
        if origin in _VALID_SHIPPING_ORIGINS
        else AuditCheck.CHECK_RESULT_FAIL
    )


def _check_lot_size_single(title: str) -> str:
    """タイトルに複数 lot キーワードがないか確認。"""
    t = (title or "").upper()
    for kw in _MULTI_LOT_KEYWORDS:
        if kw in t:
            return AuditCheck.CHECK_RESULT_FAIL
    return AuditCheck.CHECK_RESULT_PASS


def _check_not_sold(listing: dict | None) -> str:
    """is_sold = True なら fail。global_lot は skip。"""
    if listing is None:
        return AuditCheck.CHECK_RESULT_SKIP
    is_sold = listing.get("is_sold")
    if is_sold is None:
        return AuditCheck.CHECK_RESULT_SKIP
    return (
        AuditCheck.CHECK_RESULT_FAIL
        if is_sold
        else AuditCheck.CHECK_RESULT_PASS
    )


def _check_not_ended(listing: dict | None, match: dict) -> str:
    """
    end_time / lot_end_at が未来なら pass。
    過去なら fail。None なら skip。
    """
    now = datetime.now(timezone.utc)

    # end_time フィールドを source_type に応じて取得
    end_str = None
    if listing:
        end_str = listing.get("end_time") or listing.get("lot_end_at")
    if not end_str:
        return AuditCheck.CHECK_RESULT_SKIP

    try:
        end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
        return (
            AuditCheck.CHECK_RESULT_PASS
            if end_dt > now
            else AuditCheck.CHECK_RESULT_FAIL
        )
    except (ValueError, TypeError):
        return AuditCheck.CHECK_RESULT_SKIP


def _check_cert_validity(match: dict) -> str:
    """
    cert_match_flag=True のとき cert_number が None なら warn。
    cert_match_flag=False なら skip。
    """
    if not match.get("cert_match_flag"):
        return AuditCheck.CHECK_RESULT_SKIP
    details = match.get("bot_match_details") or {}
    cert_number = details.get("cert_number") or match.get("cert_number")
    if cert_number:
        return AuditCheck.CHECK_RESULT_PASS
    return AuditCheck.CHECK_RESULT_WARN


def _check_title_consistency(title: str, seed: dict | None) -> str:
    """
    タイトルに矛盾するキーワードがないか簡易確認。
    seed が None なら skip。
    """
    if not seed or not title:
        return AuditCheck.CHECK_RESULT_SKIP
    t = title.upper()
    country = (seed.get("country") or "").upper()
    denomination = (seed.get("denomination") or "").upper()
    # 最低限: country / denomination のいずれかがタイトルに含まれていれば pass
    if country and country in t:
        return AuditCheck.CHECK_RESULT_PASS
    if denomination and denomination in t:
        return AuditCheck.CHECK_RESULT_PASS
    # 含まれていなければ warn (完全除外ではない)
    if country or denomination:
        return AuditCheck.CHECK_RESULT_WARN
    return AuditCheck.CHECK_RESULT_SKIP


def _check_not_stale(listing: dict | None, match: dict) -> str:
    """last_fetched_at / last_refreshed_at が 6h 以内なら pass。"""
    if listing is None:
        return AuditCheck.CHECK_RESULT_SKIP
    ts_str = listing.get("last_fetched_at") or listing.get("last_refreshed_at")
    if not ts_str:
        return AuditCheck.CHECK_RESULT_SKIP
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return (
            AuditCheck.CHECK_RESULT_PASS
            if age_hours <= _STALE_THRESHOLD_HOURS
            else AuditCheck.CHECK_RESULT_WARN
        )
    except (ValueError, TypeError):
        return AuditCheck.CHECK_RESULT_SKIP


def _check_grade_delta(match: dict, seed: dict | None) -> str:
    """
    grade_advantage_flag=True のとき grade_delta が大きすぎないか。
    5 notch 超なら warn。
    """
    if not match.get("grade_advantage_flag"):
        return AuditCheck.CHECK_RESULT_SKIP
    details = match.get("bot_match_details") or {}
    from scripts.match_engine import _grade_rank
    l_grade = str(details.get("listing_grade") or "")
    s_grade = str(details.get("seed_grade_min") or "")
    l_rank  = _grade_rank(l_grade)
    s_rank  = _grade_rank(s_grade)
    if l_rank == 0 or s_rank == 0:
        return AuditCheck.CHECK_RESULT_SKIP
    delta = l_rank - s_rank
    return (
        AuditCheck.CHECK_RESULT_WARN
        if delta > 5
        else AuditCheck.CHECK_RESULT_PASS
    )


def _check_year_delta(match: dict, seed: dict | None) -> str:
    """
    year_tolerance_flag=True のとき年代差が 3 年超なら warn。
    """
    if not match.get("year_tolerance_flag"):
        return AuditCheck.CHECK_RESULT_SKIP
    details = match.get("bot_match_details") or {}
    l_year    = details.get("listing_year")
    seed_mid  = details.get("seed_year_mid")
    if l_year is None or seed_mid is None:
        return AuditCheck.CHECK_RESULT_SKIP
    delta = abs(int(l_year) - float(seed_mid))
    return (
        AuditCheck.CHECK_RESULT_WARN
        if delta > 3
        else AuditCheck.CHECK_RESULT_PASS
    )


# ================================================================
# 監査ステータス決定
# ================================================================

def determine_audit_status(check_results: dict) -> str:
    """
    チェック結果 dict から AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL を決定する。

    - FAIL が 1 つでも → AUDIT_FAIL
    - FAIL なし + WARN が 1 つ以上 → AUDIT_HOLD
    - all pass/skip → AUDIT_PASS

    Returns:
        AuditStatus.AUDIT_PASS | AUDIT_HOLD | AUDIT_FAIL
    """
    has_fail = any(v == AuditCheck.CHECK_RESULT_FAIL for v in check_results.values())
    has_warn = any(v == AuditCheck.CHECK_RESULT_WARN  for v in check_results.values())

    if has_fail:
        return AuditStatus.AUDIT_FAIL
    if has_warn:
        return AuditStatus.AUDIT_HOLD
    return AuditStatus.AUDIT_PASS


def run_checks(
    match:   dict,
    listing: dict | None = None,
    seed:    dict | None = None,
) -> dict:
    """
    1 match レコードに対して全チェックを実行し、結果 dict を返す。

    Returns:
        {check_name: "pass"|"fail"|"warn"|"skip"}
    """
    title = ""
    if listing:
        title = listing.get("title") or listing.get("lot_title") or ""

    return {
        AuditCheck.PROFIT_CONDITION:  _check_profit_condition(match),
        AuditCheck.SHIPPING_VALID:    _check_shipping_valid(match, listing),
        AuditCheck.LOT_SIZE_SINGLE:   _check_lot_size_single(title),
        AuditCheck.NOT_SOLD:          _check_not_sold(listing),
        AuditCheck.NOT_ENDED:         _check_not_ended(listing, match),
        AuditCheck.CERT_VALIDITY:     _check_cert_validity(match),
        AuditCheck.TITLE_CONSISTENCY: _check_title_consistency(title, seed),
        AuditCheck.GRADE_DELTA:       _check_grade_delta(match, seed),
        AuditCheck.YEAR_DELTA:        _check_year_delta(match, seed),
        AuditCheck.NOT_STALE:         _check_not_stale(listing, match),
    }


# ================================================================
# daily_candidates 昇格
# ================================================================

def _promote_to_candidates(
    client,
    match:  dict,
    seed:   dict | None,
) -> Optional[str]:
    """
    AUDIT_PASS の match を daily_candidates に upsert する。

    Returns:
        保存された daily_candidates.id、失敗時は None
    """
    from scripts.candidates_writer import make_lot_dedup_key

    source_type = match.get("source_type", "")
    projected_profit = match.get("projected_profit_jpy") or 0
    match_reason = match.get("match_reason", "")
    match_type   = match.get("match_type", "")
    level        = match.get("candidate_level_bot", "")

    # 昇格に必要な最低限フィールドを構築
    lot_title = ""
    lot_url   = ""
    estimated_cost_jpy = 0
    if source_type == "ebay_listing":
        lot_url   = ""  # listing には lot_url がない場合あり
        auction_house = "eBay"
    else:
        lot_url   = ""
        auction_house = "global_lot"

    # seed から ref_price_jpy を取得
    ref_price_jpy = int(seed.get("ref_price_jpy") or 0) if seed else 0

    # dedup_key: match_id ベース
    import hashlib
    raw = f"match|{match.get('id', '')}|{seed.get('id', '') if seed else ''}"
    dedup_key = hashlib.md5(raw.encode()).hexdigest()

    rec = {
        "auction_house":        auction_house,
        "lot_title":            lot_title or match_reason[:100],
        "lot_url":              lot_url,
        "judgment":             "OK",
        "judgment_reason":      f"[match_engine] {match_reason}",
        "buy_limit_jpy":        ref_price_jpy,
        "estimated_cost_jpy":   ref_price_jpy - projected_profit if projected_profit else 0,
        "estimated_margin_pct": (
            round(projected_profit / ref_price_jpy * 100, 1)
            if ref_price_jpy > 0 else 0.0
        ),
        "match_score":          match.get("match_score"),
        "coin_match_status":    "matched",
        "ceo_decision":         CeoDecision.PENDING,
        "status":               "pending",
        "dedup_key":            dedup_key,
        "source":               source_type,
        "match_type":           match_type,
        "candidate_level":      level,
    }

    try:
        resp = (
            client.table(Table.DAILY_CANDIDATES)
            .upsert(rec, on_conflict="dedup_key")
            .execute()
        )
        data = resp.data or []
        if data:
            return data[0].get("id")
        return None
    except Exception as exc:
        logger.error("_promote_to_candidates 失敗: %s", exc)
        return None


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class AuditResult:
    audited_count:    int = 0
    audit_pass_count: int = 0
    audit_hold_count: int = 0
    audit_fail_count: int = 0
    promoted_count:   int = 0
    error_count:      int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.audited_count > 0:
            return "partial"
        return "error"


# ================================================================
# メイン処理
# ================================================================

def run_audit(
    dry_run: bool = False,
    limit:   int  = 50,
) -> AuditResult:
    """
    未審査 Level A match を処理し audit_status を付与する。

    Args:
        dry_run: True = DB 書き込みなし
        limit:   処理件数上限

    Returns:
        AuditResult
    """
    result = AuditResult()
    client = get_client()

    matches = load_unaudited_level_a(client, limit=limit)
    if not matches:
        logger.info("未審査 Level A match が 0 件 — 終了")
        return result

    logger.info("未審査 Level A match: %d 件", len(matches))

    for match in matches:
        match_id    = match.get("id", "")
        source_type = match.get("source_type", "")

        # listing / seed を取得 (チェックに必要なフィールドが match dict に含まれる場合もある)
        # ここでは match dict 自体をそのまま listing として流用（フィールドが入っている前提）
        # 実際の fetch は match.bot_match_details に含まれるフィールドを利用
        listing = match  # match dict には listing フィールドが合算されている
        seed    = None   # seed フィールドは bot_match_details から取れる場合のみ

        try:
            check_results = run_checks(match, listing=listing, seed=seed)
            audit_status  = determine_audit_status(check_results)
            fail_reasons  = [
                k for k, v in check_results.items()
                if v in (AuditCheck.CHECK_RESULT_FAIL, AuditCheck.CHECK_RESULT_WARN)
            ]
        except Exception as exc:
            logger.error("[audit] 例外 match_id=%s: %s", match_id, exc)
            result.error_count += 1
            result.errors.append(str(exc))
            continue

        result.audited_count += 1

        if audit_status == AuditStatus.AUDIT_PASS:
            result.audit_pass_count += 1
        elif audit_status == AuditStatus.AUDIT_HOLD:
            result.audit_hold_count += 1
        else:
            result.audit_fail_count += 1

        if dry_run:
            logger.debug(
                "  [DRY-RUN] match %s → %s (fails=%s)",
                match_id[:8], audit_status, fail_reasons,
            )
            continue

        # audit 結果を保存
        update_audit_result(
            client,
            match_id      = match_id,
            audit_status  = audit_status,
            check_results = check_results,
            fail_reasons  = fail_reasons,
        )

        # AUDIT_PASS のみ daily_candidates に昇格
        if audit_status == AuditStatus.AUDIT_PASS:
            candidate_id = _promote_to_candidates(client, match, seed)
            if candidate_id:
                set_promoted_candidate(client, match_id, candidate_id)
                result.promoted_count += 1
                logger.info(
                    "  昇格: match %s → candidate %s",
                    match_id[:8], candidate_id[:8],
                )
            else:
                logger.warning("  昇格失敗: match %s", match_id[:8])

        logger.debug("  match %s → %s", match_id[:8], audit_status)

    return result


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "cap_audit_runner.py",
        description = "Level A match に audit gate を適用し AUDIT_PASS を daily_candidates に昇格する",
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="DB 書き込みなし")
    parser.add_argument("--limit",       type=int, default=50,
                        help="処理件数上限 (デフォルト 50)")
    parser.add_argument("--status-only", action="store_true",
                        help="未審査 Level A 件数を表示して終了")
    args = parser.parse_args()

    if args.status_only:
        client  = get_client()
        matches = load_unaudited_level_a(client, limit=200)
        print(f"未審査 Level A match: {len(matches)} 件")
        for m in matches[:5]:
            print(f"  [{m.get('source_type','')}] "
                  f"match_type={m.get('match_type','')} "
                  f"score={m.get('match_score','?')}")
        return

    result = run_audit(
        dry_run = args.dry_run,
        limit   = args.limit,
    )

    if not args.dry_run:
        client = get_client()
        record_audit_run(
            client            = client,
            run_date          = date.today().isoformat(),
            status            = result.status_str(),
            audited_count     = result.audited_count,
            audit_pass_count  = result.audit_pass_count,
            audit_hold_count  = result.audit_hold_count,
            audit_fail_count  = result.audit_fail_count,
            promoted_count    = result.promoted_count,
            error_count       = result.error_count,
            error_message     = "; ".join(result.errors[:5]) if result.errors else None,
        )

    print(
        f"\n=== CAP Audit Runner {'[DRY-RUN] ' if args.dry_run else ''}完了 ===\n"
        f"  audited_count:    {result.audited_count}\n"
        f"  audit_pass_count: {result.audit_pass_count}\n"
        f"  audit_hold_count: {result.audit_hold_count}\n"
        f"  audit_fail_count: {result.audit_fail_count}\n"
        f"  promoted_count:   {result.promoted_count}\n"
        f"  error_count:      {result.error_count}\n"
        f"  status:           {result.status_str()}"
    )


if __name__ == "__main__":
    main()
