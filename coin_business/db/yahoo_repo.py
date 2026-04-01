"""
coin_business/db/yahoo_repo.py
================================
yahoo_sold_lots_staging へのリポジトリ層。

責務:
  - upsert_staging_records(): batch upsert (dedup key = yahoo_lot_id)
  - get_pending_ceo_records(): PENDING_CEO 件数/一覧取得
  - mark_parse_failure(): パース失敗ログ記録

設計方針:
  - status の直書き禁止。必ず constants.YahooStagingStatus を使う。
  - yahoo_sold_lots_staging にのみ書く。本DB yahoo_sold_lots には絶対に書かない。
  - upsert は yahoo_lot_id を on_conflict キーとして冪等に動作する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from constants import YahooStagingStatus, Table

logger = logging.getLogger(__name__)

# ================================================================
# 定数
# ================================================================

STAGING_TABLE = Table.YAHOO_SOLD_LOTS_STAGING  # "yahoo_sold_lots_staging"
BATCH_SIZE    = 200  # Supabase REST API の安全バッチサイズ

# ================================================================
# UpsertResult
# ================================================================

@dataclass
class UpsertResult:
    """upsert_staging_records の戻り値サマリー"""
    total_submitted:  int = 0
    upserted_count:   int = 0
    skipped_count:    int = 0   # yahoo_lot_id が None で除外した件数
    error_count:      int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_submitted == 0:
            return 0.0
        return round(self.upserted_count / self.total_submitted, 3)

    def __str__(self) -> str:
        return (
            f"UpsertResult(submitted={self.total_submitted}, "
            f"upserted={self.upserted_count}, "
            f"skipped={self.skipped_count}, "
            f"errors={self.error_count})"
        )


# ================================================================
# 公開 API
# ================================================================

def upsert_staging_records(
    client,
    records: list[dict],
    dry_run: bool = False,
) -> UpsertResult:
    """
    yahoo_sold_lots_staging へ batch upsert する。

    Args:
        client:   supabase_client.get_client() の戻り値
        records:  normalizer.normalize_lot_record() で変換済みの dict リスト
        dry_run:  True の場合は DB に書かず、結果を返す

    Returns:
        UpsertResult

    Notes:
        - yahoo_lot_id が None のレコードはスキップ (skipped_count に加算)
        - status が未設定のレコードは PENDING_CEO を強制セット
        - 本DB yahoo_sold_lots には絶対に書かない
    """
    result = UpsertResult(total_submitted=len(records))

    # yahoo_lot_id のないレコードを除外
    valid: list[dict] = []
    for rec in records:
        if not rec.get("yahoo_lot_id"):
            result.skipped_count += 1
            logger.warning("yahoo_lot_id なしのレコードをスキップ: title=%s",
                           rec.get("lot_title", "")[:60])
            continue
        # status を強制 PENDING_CEO
        rec = dict(rec)
        rec["status"] = YahooStagingStatus.PENDING_CEO
        valid.append(rec)

    if not valid:
        logger.info("upsert 対象レコードなし (全件スキップ)")
        return result

    if dry_run:
        logger.info("[DRY-RUN] upsert 予定件数: %d", len(valid))
        result.upserted_count = len(valid)
        return result

    # バッチ分割して upsert
    for i in range(0, len(valid), BATCH_SIZE):
        batch = valid[i:i + BATCH_SIZE]
        try:
            resp = client.table(STAGING_TABLE).upsert(
                batch,
                on_conflict="yahoo_lot_id",
            ).execute()
            upserted = len(resp.data) if resp.data else len(batch)
            result.upserted_count += upserted
            logger.debug("batch[%d-%d] upserted %d rows", i, i + len(batch), upserted)
        except Exception as exc:
            err_msg = f"batch[{i}-{i+len(batch)}] upsert 失敗: {exc}"
            logger.error(err_msg)
            result.error_count += 1
            result.errors.append(err_msg)

    return result


def get_pending_ceo_count(client) -> int:
    """PENDING_CEO 件数を返す。"""
    try:
        resp = client.table(STAGING_TABLE).select(
            "id", count="exact"
        ).eq("status", YahooStagingStatus.PENDING_CEO).execute()
        return resp.count or 0
    except Exception as exc:
        logger.error("PENDING_CEO 件数取得失敗: %s", exc)
        return -1


def get_pending_ceo_records(
    client,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    PENDING_CEO ステータスのレコードを返す。
    dashboard の CEO確認タブで使用する。
    """
    try:
        resp = (
            client.table(STAGING_TABLE)
            .select("*")
            .eq("status", YahooStagingStatus.PENDING_CEO)
            .order("fetched_at", desc=False)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("PENDING_CEO レコード取得失敗: %s", exc)
        return []


def count_by_status(client) -> dict[str, int]:
    """
    staging テーブルのステータス別件数を返す。
    例: {"PENDING_CEO": 120, "APPROVED_TO_MAIN": 5, ...}
    """
    counts: dict[str, int] = {}
    for status in [
        YahooStagingStatus.PENDING_CEO,
        YahooStagingStatus.APPROVED_TO_MAIN,
        YahooStagingStatus.PROMOTED,
        YahooStagingStatus.REJECTED,
        YahooStagingStatus.HELD,
    ]:
        try:
            resp = client.table(STAGING_TABLE).select(
                "id", count="exact"
            ).eq("status", status).execute()
            counts[status] = resp.count or 0
        except Exception:
            counts[status] = -1
    return counts


def get_already_synced_ids(client, yahoo_lot_ids: list[str]) -> set[str]:
    """
    渡した yahoo_lot_id のうち、既に staging に存在するものを返す。
    差分同期の事前チェックに使う。
    """
    if not yahoo_lot_ids:
        return set()
    try:
        resp = (
            client.table(STAGING_TABLE)
            .select("yahoo_lot_id")
            .in_("yahoo_lot_id", yahoo_lot_ids)
            .execute()
        )
        return {row["yahoo_lot_id"] for row in (resp.data or [])}
    except Exception as exc:
        logger.error("既存 ID チェック失敗: %s", exc)
        return set()


def record_job_run(
    client,
    run_date: str,
    status: str,
    fetched_count: int,
    inserted_count: int,
    error_message: Optional[str] = None,
) -> bool:
    """
    job_yahoo_sold_sync_daily にジョブ実行記録を insert する。

    Args:
        run_date:       "YYYY-MM-DD"
        status:         "ok" | "partial" | "error"
        fetched_count:  取得件数
        inserted_count: upsert 件数
        error_message:  エラー時のメッセージ

    Returns:
        True = 成功, False = 失敗
    """
    try:
        record = {
            "run_date":       run_date,
            "status":         status,
            "fetched_count":  fetched_count,
            "inserted_count": inserted_count,
        }
        if error_message:
            record["error_message"] = error_message[:2000]
        client.table(Table.JOB_YAHOO_SOLD_SYNC).insert(record).execute()
        return True
    except Exception as exc:
        logger.error("ジョブ記録 insert 失敗: %s", exc)
        return False
