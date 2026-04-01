"""
coin_business/db/yahoo_review_repo.py
========================================
yahoo_sold_lots_staging レビュー操作のリポジトリ層。

責務:
  - load_pending_review()    : PENDING_CEO / HELD 一覧を取得
  - load_staging_record()    : 単一レコード + レビュー履歴取得
  - save_review_decision()   : yahooレビュー保存 + staging.status 更新 (アトミック)
  - get_review_history()     : staging_id に紐づく過去レビュー一覧

設計方針:
  - ダブルレビュー防止: 最新 decision が approved/rejected の場合は警告を返す。
    ただし HELD → approve/reject への遷移は常に許可する。
  - staging の status 更新は save_review_decision() 内で必ず行う。
    呼び出し側で status を直接書き換えない。
  - 決定は constants.YahooStagingStatus に従う。
    API 呼び出し側で文字列を直書きしない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from constants import YahooStagingStatus, Table

logger = logging.getLogger(__name__)

# ================================================================
# テーブル名
# ================================================================

STAGING_TABLE = Table.YAHOO_SOLD_LOTS_STAGING   # "yahoo_sold_lots_staging"
REVIEWS_TABLE = Table.YAHOO_SOLD_LOT_REVIEWS     # "yahoo_sold_lot_reviews"

# ================================================================
# decision → status マッピング
# ================================================================

DECISION_TO_STATUS: dict[str, str] = {
    "approved": YahooStagingStatus.APPROVED_TO_MAIN,
    "rejected": YahooStagingStatus.REJECTED,
    "held":     YahooStagingStatus.HELD,
}

VALID_DECISIONS = frozenset(DECISION_TO_STATUS.keys())

# ================================================================
# ReviewResult
# ================================================================

@dataclass
class ReviewResult:
    """save_review_decision の戻り値"""
    ok:              bool   = False
    staging_status:  str    = ""   # 更新後の status
    review_id:       str    = ""   # 保存された review の UUID
    warning:         str    = ""   # ダブルレビュー等の警告メッセージ
    error:           str    = ""   # エラーメッセージ


# ================================================================
# 公開 API
# ================================================================

def load_pending_review(
    client,
    status_filter: list[str] | None = None,
    sort_by: str = "sold_date_desc",
    limit: int = 200,
    offset: int = 0,
    cert_filter: str | None = None,
    min_confidence: float | None = None,
) -> list[dict]:
    """
    CEO確認待ちの staging レコード一覧を返す。

    Args:
        client:         Supabase クライアント
        status_filter:  表示するステータスのリスト。
                        None → [PENDING_CEO, HELD] を表示
        sort_by:        "sold_date_desc" | "sold_date_asc" | "confidence_desc" | "fetched_desc"
        limit:          最大取得件数
        offset:         ページングオフセット
        cert_filter:    "NGC" | "PCGS" | None
        min_confidence: parse_confidence の下限 (0.0-1.0)

    Returns:
        list of dict — yahoo_sold_lots_staging の各行
    """
    if status_filter is None:
        status_filter = [YahooStagingStatus.PENDING_CEO, YahooStagingStatus.HELD]

    try:
        q = (
            client.table(STAGING_TABLE)
            .select(
                "id, yahoo_lot_id, lot_title, title_normalized, "
                "sold_price_jpy, sold_date, "
                "cert_company, cert_number, year, denomination, grade_text, "
                "source_url, image_url, thumbnail_url, "
                "parse_confidence, status, fetched_at, created_at"
            )
            .in_("status", status_filter)
        )

        if cert_filter:
            q = q.eq("cert_company", cert_filter.upper())

        if min_confidence is not None:
            q = q.gte("parse_confidence", min_confidence)

        # ソート
        if sort_by == "sold_date_asc":
            q = q.order("sold_date", desc=False)
        elif sort_by == "confidence_desc":
            q = q.order("parse_confidence", desc=True).order("sold_date", desc=True)
        elif sort_by == "fetched_desc":
            q = q.order("fetched_at", desc=True)
        else:  # sold_date_desc (デフォルト)
            q = q.order("sold_date", desc=True)

        q = q.range(offset, offset + limit - 1)
        resp = q.execute()
        return resp.data or []

    except Exception as exc:
        logger.error("load_pending_review 失敗: %s", exc)
        return []


def count_pending_review(
    client,
    status_filter: list[str] | None = None,
) -> dict[str, int]:
    """
    ステータス別の件数を返す。
    Returns: {"PENDING_CEO": 120, "HELD": 8, ...}
    """
    if status_filter is None:
        status_filter = [
            YahooStagingStatus.PENDING_CEO,
            YahooStagingStatus.HELD,
            YahooStagingStatus.APPROVED_TO_MAIN,
            YahooStagingStatus.REJECTED,
            YahooStagingStatus.PROMOTED,
        ]

    counts: dict[str, int] = {}
    for status in status_filter:
        try:
            resp = client.table(STAGING_TABLE).select(
                "id", count="exact"
            ).eq("status", status).execute()
            counts[status] = resp.count or 0
        except Exception as exc:
            logger.warning("count_pending_review[%s] 失敗: %s", status, exc)
            counts[status] = -1
    return counts


def load_staging_record(client, staging_id: str) -> Optional[dict]:
    """
    単一の staging レコードを取得する。
    見つからない場合は None を返す。
    """
    try:
        resp = (
            client.table(STAGING_TABLE)
            .select("*")
            .eq("id", staging_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        logger.error("load_staging_record 失敗 id=%s: %s", staging_id, exc)
        return None


def get_review_history(client, staging_id: str) -> list[dict]:
    """
    staging レコードのレビュー履歴を新しい順に返す。
    """
    try:
        resp = (
            client.table(REVIEWS_TABLE)
            .select("id, decision, reason, reviewer, review_note, reviewed_at")
            .eq("staging_id", staging_id)
            .order("reviewed_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("get_review_history 失敗 staging_id=%s: %s", staging_id, exc)
        return []


def get_latest_review(client, staging_id: str) -> Optional[dict]:
    """最新の 1 件レビューを返す。"""
    history = get_review_history(client, staging_id)
    return history[0] if history else None


def save_review_decision(
    client,
    staging_id:  str,
    decision:    str,           # "approved" | "rejected" | "held"
    reviewer:    str = "ceo",
    reason:      str | None = None,
    review_note: str | None = None,
) -> ReviewResult:
    """
    レビュー決定を保存し、staging の status を更新する。

    Args:
        client:      Supabase クライアント
        staging_id:  yahoo_sold_lots_staging.id (UUID)
        decision:    "approved" | "rejected" | "held"
        reviewer:    "ceo" | "cap" | "auto"
        reason:      却下/保留の理由 (任意)
        review_note: 自由メモ (任意)

    Returns:
        ReviewResult
            ok=True  → 成功
            ok=False → 失敗 (error フィールドに詳細)
            warning  → ダブルレビュー警告など (ok=True でも返る場合あり)

    二重レビュー防止:
        - 最新 decision が "approved" の場合、再度 approved は warning を返す (処理は進む)
        - 最新 decision が "rejected" の場合、re-reject は warning を返す (処理は進む)
        - HELD → approve/reject は常に許可
    """
    result = ReviewResult()

    # 引数バリデーション
    if decision not in VALID_DECISIONS:
        result.error = f"不正な decision: {decision!r}. 有効値: {sorted(VALID_DECISIONS)}"
        return result

    if not staging_id:
        result.error = "staging_id が空です"
        return result

    # ダブルレビューチェック
    latest = get_latest_review(client, staging_id)
    if latest:
        prev_decision = latest.get("decision", "")
        if prev_decision == decision and decision in ("approved", "rejected"):
            result.warning = (
                f"この案件はすでに {decision} です "
                f"(前回: {latest.get('reviewed_at', '?')}, "
                f"by {latest.get('reviewer', '?')}). "
                f"再度同じ決定を保存します。"
            )

    # 1. yahoo_sold_lot_reviews に INSERT
    review_rec = {
        "staging_id":   staging_id,
        "decision":     decision,
        "reviewer":     reviewer,
    }
    if reason:
        review_rec["reason"] = reason[:1000]
    if review_note:
        review_rec["review_note"] = review_note[:2000]

    try:
        resp = client.table(REVIEWS_TABLE).insert(review_rec).execute()
        if resp.data:
            result.review_id = resp.data[0].get("id", "")
    except Exception as exc:
        result.error = f"レビュー保存失敗: {exc}"
        logger.error("save_review_decision insert 失敗: %s", exc)
        return result

    # 2. yahoo_sold_lots_staging の status を更新
    new_status = DECISION_TO_STATUS[decision]
    try:
        client.table(STAGING_TABLE).update(
            {"status": new_status}
        ).eq("id", staging_id).execute()
        result.staging_status = new_status
    except Exception as exc:
        # レビューは保存されたが status 更新に失敗した場合
        result.error = f"status 更新失敗 (レビューは保存済み review_id={result.review_id}): {exc}"
        logger.error("save_review_decision status update 失敗: %s", exc)
        return result

    result.ok = True
    return result
