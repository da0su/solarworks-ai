"""
coin_business/db/match_repo.py
================================
match_engine / cap_audit_runner のリポジトリ層。

責務:
  - load_pending_ebay_listings()    : match 未処理 eBay listing
  - load_active_global_lots()       : 監視中 global_auction_lots
  - load_active_seeds()             : is_active な yahoo_coin_seeds
  - upsert_match_result()           : candidate_match_results に upsert
  - load_unaudited_level_a()        : audit 未処理 Level A match
  - update_audit_result()           : audit_status / check_results を更新
  - set_promoted_candidate()        : promoted_candidate_id をセット
  - update_listing_match_status()   : ebay_listings_raw.match_status を更新
  - record_match_run()              : job_match_engine_daily に記録
  - record_audit_run()              : job_cap_audit_daily に記録

設計原則:
  - candidate_match_results は (source_type, ebay_listing_id|global_lot_id, seed_id)
    の組み合わせで重複しないよう UPSERT
  - API エラー時は例外を外に出さず None / False / [] を返す
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from constants import (
    AuditStatus,
    Table,
)

logger = logging.getLogger(__name__)

# ================================================================
# テーブル名
# ================================================================

MATCH_TABLE       = Table.CANDIDATE_MATCH_RESULTS   # "candidate_match_results"
EBAY_RAW_TABLE    = Table.EBAY_LISTINGS_RAW          # "ebay_listings_raw"
GLOBAL_LOTS_TABLE = Table.GLOBAL_AUCTION_LOTS        # "global_auction_lots"
SEEDS_TABLE       = Table.YAHOO_COIN_SEEDS           # "yahoo_coin_seeds"
JOB_MATCH         = Table.JOB_MATCH_ENGINE           # "job_match_engine_daily"
JOB_AUDIT         = Table.JOB_CAP_AUDIT              # "job_cap_audit_daily"


# ================================================================
# 入力データ読み込み
# ================================================================

def load_pending_ebay_listings(
    client,
    limit: int = 100,
) -> list[dict]:
    """
    match 未処理のアクティブ eBay listing を返す。

    Returns:
        list of ebay_listings_raw レコード
    """
    try:
        resp = (
            client.table(EBAY_RAW_TABLE)
            .select(
                "id, ebay_item_id, title, year, country, denomination, "
                "grade, grader, cert_number, "
                "shipping_from_country, "
                "current_price_usd, currency, listing_type, "
                "bid_count, end_time, is_active, is_sold, "
                "last_fetched_at"
            )
            .eq("match_status", "pending")
            .eq("is_active", True)
            .eq("is_sold", False)
            .order("last_fetched_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_pending_ebay_listings 失敗: %s", exc)
        return []


def load_active_global_lots(
    client,
    limit: int = 100,
) -> list[dict]:
    """
    監視中の global_auction_lots を返す。

    Returns:
        list of global_auction_lots レコード
    """
    try:
        resp = (
            client.table(GLOBAL_LOTS_TABLE)
            .select(
                "id, event_id, lot_id_external, lot_number, lot_title, "
                "year, country, denomination, grade_text, grader, "
                "cert_company, cert_number, "
                "estimate_low_usd, estimate_high_usd, current_bid_usd, "
                "currency, lot_url, lot_end_at, status"
            )
            .in_("status", ["active", "upcoming"])
            .order("lot_end_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_active_global_lots 失敗: %s", exc)
        return []


def load_active_seeds(
    client,
    limit: int = 500,
) -> list[dict]:
    """
    is_active な yahoo_coin_seeds を返す。

    Returns:
        list of yahoo_coin_seeds レコード
    """
    try:
        resp = (
            client.table(SEEDS_TABLE)
            .select(
                "id, yahoo_lot_id, seed_type, search_query, "
                "cert_company, cert_number, "
                "year_min, year_max, country, denomination, "
                "grade_min, grader, "
                "ref_price_jpy, ref_sold_date"
            )
            .eq("is_active", True)
            .order("id", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_active_seeds 失敗: %s", exc)
        return []


# ================================================================
# match_results 書き込み
# ================================================================

def upsert_match_result(client, rec: dict) -> Optional[str]:
    """
    candidate_match_results に upsert する。

    dedup key: (source_type, ebay_listing_id, seed_id)
               または (source_type, global_lot_id, seed_id)

    Returns:
        保存された UUID、失敗時は None
    """
    source_type = rec.get("source_type", "")
    if source_type == "ebay_listing":
        if not rec.get("ebay_listing_id") or not rec.get("seed_id"):
            logger.warning("upsert_match_result: ebay_listing_id か seed_id が空")
            return None
        conflict_key = "source_type,ebay_listing_id,seed_id"
    elif source_type == "global_lot":
        if not rec.get("global_lot_id") or not rec.get("seed_id"):
            logger.warning("upsert_match_result: global_lot_id か seed_id が空")
            return None
        conflict_key = "source_type,global_lot_id,seed_id"
    else:
        logger.warning("upsert_match_result: 不明な source_type: %s", source_type)
        return None

    rec.setdefault("bot_matched_at", datetime.now(timezone.utc).isoformat())

    try:
        resp = (
            client.table(MATCH_TABLE)
            .upsert(rec, on_conflict=conflict_key)
            .execute()
        )
        data = resp.data or []
        if data:
            return data[0].get("id")
        return None
    except Exception as exc:
        logger.error("upsert_match_result 失敗: %s", exc)
        return None


# ================================================================
# audit 読み込み / 書き込み
# ================================================================

def load_unaudited_level_a(
    client,
    limit: int = 50,
) -> list[dict]:
    """
    audit 未処理の Level A match を返す。

    Returns:
        list of candidate_match_results レコード
    """
    try:
        resp = (
            client.table(MATCH_TABLE)
            .select("*")
            .eq("candidate_level_bot", "A")
            .is_("audit_status", "null")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_unaudited_level_a 失敗: %s", exc)
        return []


def update_audit_result(
    client,
    match_id:       str,
    audit_status:   str,
    check_results:  dict,
    fail_reasons:   list[str],
) -> bool:
    """
    candidate_match_results の audit 列を更新する。

    Args:
        match_id:       candidate_match_results.id
        audit_status:   AUDIT_PASS | AUDIT_HOLD | AUDIT_FAIL
        check_results:  {check_name: "pass"|"fail"|"warn"|"skip"}
        fail_reasons:   FAIL / WARN の理由リスト

    Returns:
        True = 成功、False = 失敗
    """
    if audit_status not in AuditStatus.ALL:
        logger.warning("update_audit_result: 不正な audit_status: %s", audit_status)
        return False

    payload = {
        "audit_status":       audit_status,
        "audit_check_results": check_results,
        "audit_fail_reasons":  fail_reasons,
        "audited_at":          datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table(MATCH_TABLE).update(payload).eq("id", match_id).execute()
        return True
    except Exception as exc:
        logger.error("update_audit_result 失敗: %s", exc)
        return False


def set_promoted_candidate(
    client,
    match_id:     str,
    candidate_id: str,
) -> bool:
    """
    AUDIT_PASS の match に promoted_candidate_id をセットする。

    Returns:
        True = 成功、False = 失敗
    """
    payload = {
        "promoted_candidate_id": candidate_id,
        "promoted_at":           datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table(MATCH_TABLE).update(payload).eq("id", match_id).execute()
        return True
    except Exception as exc:
        logger.error("set_promoted_candidate 失敗: %s", exc)
        return False


def update_listing_match_status(
    client,
    listing_id:   str,
    match_status: str,
) -> bool:
    """
    ebay_listings_raw.match_status を更新する。
    match_status: 'matched' | 'no_match' | 'audit_pass' | 'audit_fail'

    Returns:
        True = 成功、False = 失敗
    """
    try:
        client.table(EBAY_RAW_TABLE).update(
            {"match_status": match_status}
        ).eq("id", listing_id).execute()
        return True
    except Exception as exc:
        logger.error("update_listing_match_status 失敗: %s", exc)
        return False


# ================================================================
# ジョブ記録
# ================================================================

def record_match_run(
    client,
    run_date:        str,
    status:          str,
    listings_scanned: int = 0,
    lots_scanned:    int = 0,
    matches_created: int = 0,
    level_a_count:   int = 0,
    level_b_count:   int = 0,
    level_c_count:   int = 0,
    error_count:     int = 0,
    error_message:   str | None = None,
) -> None:
    rec = {
        "run_date":        run_date,
        "status":          status,
        "listings_scanned": listings_scanned,
        "lots_scanned":    lots_scanned,
        "matches_created": matches_created,
        "level_a_count":   level_a_count,
        "level_b_count":   level_b_count,
        "level_c_count":   level_c_count,
        "error_count":     error_count,
    }
    if error_message:
        rec["error_message"] = error_message
    try:
        client.table(JOB_MATCH).insert(rec).execute()
    except Exception as exc:
        logger.error("record_match_run 失敗: %s", exc)


def record_audit_run(
    client,
    run_date:         str,
    status:           str,
    audited_count:    int = 0,
    audit_pass_count: int = 0,
    audit_hold_count: int = 0,
    audit_fail_count: int = 0,
    promoted_count:   int = 0,
    error_count:      int = 0,
    error_message:    str | None = None,
) -> None:
    rec = {
        "run_date":         run_date,
        "status":           status,
        "audited_count":    audited_count,
        "audit_pass_count": audit_pass_count,
        "audit_hold_count": audit_hold_count,
        "audit_fail_count": audit_fail_count,
        "promoted_count":   promoted_count,
        "error_count":      error_count,
    }
    if error_message:
        rec["error_message"] = error_message
    try:
        client.table(JOB_AUDIT).insert(rec).execute()
    except Exception as exc:
        logger.error("record_audit_run 失敗: %s", exc)
