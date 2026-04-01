"""
coin_business/db/yahoo_promoter_repo.py
=========================================
yahoo_sold_lots_staging → yahoo_sold_lots 昇格のリポジトリ層。

責務:
  - load_approved_staging()   : APPROVED_TO_MAIN レコードを staging から取得
  - promote_to_main()         : yahoo_sold_lots へ upsert + staging を PROMOTED に更新
  - count_promotable()        : APPROVED_TO_MAIN の件数を返す
  - load_main_lot_by_lot_id() : yahoo_sold_lots から 1 件取得 (重複チェック用)
  - record_promoter_run()     : ジョブ実行記録

設計原則:
  - APPROVED_TO_MAIN のみ昇格。PENDING_CEO / HELD / REJECTED は絶対に昇格させない。
  - 冪等性: yahoo_lot_id の ON CONFLICT UPDATE で 2 重実行しても安全。
  - staging の status 更新 (APPROVED_TO_MAIN → PROMOTED) は promote_to_main() 内で行う。
    呼び出し側で status を直接書き換えない。
  - yahoo_sold_lots_staging には source_staging_id / approved_by / approved_at を保持。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timezone
from typing import Optional

from constants import YahooStagingStatus, Table

logger = logging.getLogger(__name__)

# ================================================================
# テーブル名
# ================================================================

STAGING_TABLE = Table.YAHOO_SOLD_LOTS_STAGING   # "yahoo_sold_lots_staging"
MAIN_TABLE    = Table.YAHOO_SOLD_LOTS            # "yahoo_sold_lots"
REVIEWS_TABLE = Table.YAHOO_SOLD_LOT_REVIEWS     # "yahoo_sold_lot_reviews"

# ================================================================
# PromoteResult
# ================================================================

@dataclass
class PromoteResult:
    """promote_to_main の戻り値サマリー"""
    ok:              bool = True
    promoted_count:  int  = 0   # 新規昇格件数
    skipped_count:   int  = 0   # 既に昇格済みでスキップした件数
    error_count:     int  = 0
    errors:          list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.promoted_count + self.skipped_count + self.error_count

    def __str__(self) -> str:
        return (
            f"PromoteResult(promoted={self.promoted_count}, "
            f"skipped={self.skipped_count}, "
            f"errors={self.error_count})"
        )


# ================================================================
# 公開 API
# ================================================================

def count_promotable(client) -> int:
    """
    APPROVED_TO_MAIN 件数 (昇格待ち件数) を返す。
    0 件の場合も 0 を返す。-1 は DB エラー。
    """
    try:
        resp = client.table(STAGING_TABLE).select(
            "id", count="exact"
        ).eq("status", YahooStagingStatus.APPROVED_TO_MAIN).execute()
        return resp.count or 0
    except Exception as exc:
        logger.error("count_promotable 失敗: %s", exc)
        return -1


def load_approved_staging(
    client,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """
    APPROVED_TO_MAIN ステータスの staging レコードを返す。

    Returns:
        list of dict — yahoo_sold_lots_staging の各行 (全カラム)
    """
    try:
        resp = (
            client.table(STAGING_TABLE)
            .select("*")
            .eq("status", YahooStagingStatus.APPROVED_TO_MAIN)
            .order("updated_at", desc=False)   # 古いものから昇格
            .range(offset, offset + limit - 1)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("load_approved_staging 失敗: %s", exc)
        return []


def load_main_lot_by_lot_id(client, yahoo_lot_id: str) -> Optional[dict]:
    """
    yahoo_sold_lots から yahoo_lot_id で 1 件取得する。
    昇格済みかどうかの確認に使う。None = 未昇格。
    """
    try:
        resp = (
            client.table(MAIN_TABLE)
            .select("id, yahoo_lot_id, source_staging_id, approved_by, approved_at")
            .eq("yahoo_lot_id", yahoo_lot_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        logger.error("load_main_lot_by_lot_id 失敗 lot_id=%s: %s", yahoo_lot_id, exc)
        return None


def get_approval_info(client, staging_id: str) -> dict:
    """
    staging_id に対応する最新の approved レビューを返す。
    戻り値: {"approved_by": str, "approved_at": str} or {} (見つからない場合)
    """
    try:
        resp = (
            client.table(REVIEWS_TABLE)
            .select("reviewer, reviewed_at")
            .eq("staging_id", staging_id)
            .eq("decision", "approved")
            .order("reviewed_at", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = resp.data[0]
            return {
                "approved_by": row.get("reviewer", ""),
                "approved_at": row.get("reviewed_at", ""),
            }
    except Exception as exc:
        logger.warning("get_approval_info 失敗 staging_id=%s: %s", staging_id, exc)
    return {}


def promote_to_main(
    client,
    staging_rec:  dict,
    approved_by:  str = "",
    approved_at:  str = "",
) -> bool:
    """
    staging レコード 1 件を yahoo_sold_lots に upsert し、
    staging.status を PROMOTED に更新する。

    Args:
        client:       Supabase クライアント
        staging_rec:  yahoo_sold_lots_staging の 1 レコード (dict)
        approved_by:  承認者 ('ceo' | 'cap' | 'auto')
        approved_at:  承認日時 (ISO 8601 文字列)

    Returns:
        True = 成功, False = 失敗

    Raises:
        なし (例外は内部で捕捉してログ出力)

    絶対条件:
        staging_rec["status"] == APPROVED_TO_MAIN でなければ昇格しない。
        呼び出し側はこの保証を与えること。
    """
    # ── 防衛: APPROVED_TO_MAIN 以外は昇格させない
    status = staging_rec.get("status", "")
    if status != YahooStagingStatus.APPROVED_TO_MAIN:
        logger.error(
            "昇格をスキップ: status=%s (APPROVED_TO_MAIN 以外は昇格不可) id=%s",
            status, staging_rec.get("id", "?"),
        )
        return False

    yahoo_lot_id = staging_rec.get("yahoo_lot_id")
    staging_id   = staging_rec.get("id", "")

    if not yahoo_lot_id:
        logger.error("yahoo_lot_id が空のため昇格スキップ: staging_id=%s", staging_id)
        return False

    # ── 本DB レコード構築
    main_rec: dict = {
        "yahoo_lot_id":      yahoo_lot_id,
        "source_staging_id": staging_id or None,
        "lot_title":         staging_rec.get("lot_title") or "",
    }

    # オプショナル フィールド (None のものは送らない)
    optional_fields = [
        "title_normalized", "year", "denomination",
        "cert_company", "cert_number", "grade_text",
        "sold_price_jpy", "sold_date",
        "source_url", "image_url", "parse_confidence",
    ]
    for col in optional_fields:
        v = staging_rec.get(col)
        if v is not None:
            main_rec[col] = v

    if approved_by:
        main_rec["approved_by"] = approved_by
    if approved_at:
        main_rec["approved_at"] = approved_at

    # ── 1. yahoo_sold_lots に upsert
    try:
        client.table(MAIN_TABLE).upsert(
            main_rec,
            on_conflict="yahoo_lot_id",
        ).execute()
    except Exception as exc:
        logger.error("yahoo_sold_lots upsert 失敗 lot_id=%s: %s", yahoo_lot_id, exc)
        return False

    # ── 2. staging.status を PROMOTED に更新
    try:
        client.table(STAGING_TABLE).update(
            {"status": YahooStagingStatus.PROMOTED}
        ).eq("id", staging_id).execute()
    except Exception as exc:
        logger.error(
            "staging status→PROMOTED 更新失敗 (本DBへの書き込みは完了) "
            "staging_id=%s: %s", staging_id, exc,
        )
        return False

    logger.debug("昇格完了: yahoo_lot_id=%s staging_id=%s", yahoo_lot_id, staging_id)
    return True


def record_promoter_run(
    client,
    run_date:       str,
    status:         str,
    promoted_count: int,
    skipped_count:  int = 0,
    error_count:    int = 0,
    error_message:  Optional[str] = None,
) -> bool:
    """
    job_yahoo_promoter_daily にジョブ実行記録を insert する。

    Args:
        run_date:        "YYYY-MM-DD"
        status:          "ok" | "partial" | "error"
        promoted_count:  昇格件数
        skipped_count:   スキップ件数
        error_count:     エラー件数
        error_message:   エラー時のメッセージ
    """
    try:
        record: dict = {
            "run_date":       run_date,
            "status":         status,
            "promoted_count": promoted_count,
            "skipped_count":  skipped_count,
            "error_count":    error_count,
        }
        if error_message:
            record["error_message"] = error_message[:2000]
        client.table(Table.JOB_YAHOO_PROMOTER).insert(record).execute()
        return True
    except Exception as exc:
        logger.error("ジョブ記録 insert 失敗: %s", exc)
        return False
