"""
coin_business/db/notification_repo.py
=======================================
notification_log の CRUD。

責務:
  - log_notification()        : 送信履歴を記録
  - was_recently_notified()   : 重複送信防止チェック
  - load_unsent_for_type()    : 未送信通知の取得 (retry用)
  - record_morning_brief_run(): job_morning_brief_daily に記録
  - record_notion_sync_run()  : job_notion_sync_daily に記録

設計原則:
  - 全テーブル名は constants.Table から参照
  - API エラーは例外を外に出さず None / False / [] を返す
  - dedup は sent_at の recency で判断 (DB側 UNIQUE 制約なし)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from constants import NotificationChannel, Table

logger = logging.getLogger(__name__)


# ================================================================
# 送信ログ記録
# ================================================================

def log_notification(
    client,
    *,
    notification_type: str,
    channel: str = NotificationChannel.SLACK,
    message_summary: Optional[str] = None,
    payload: Optional[dict] = None,
    status: str = "sent",
    error_message: Optional[str] = None,
    candidate_id: Optional[str] = None,
    watchlist_id: Optional[str] = None,
    bid_record_id: Optional[str] = None,
    event_id: Optional[str] = None,
    lot_id: Optional[str] = None,
) -> Optional[str]:
    """
    notification_log に 1行追加する。

    Returns: 挿入された id (UUID string) or None on error
    """
    try:
        rec: dict = {
            "notification_type": notification_type,
            "channel":           channel,
            "status":            status,
            "sent_at":           datetime.now(timezone.utc).isoformat(),
        }
        if message_summary is not None:
            rec["message_summary"] = message_summary
        if payload is not None:
            rec["payload"] = payload
        if error_message is not None:
            rec["error_message"] = error_message
        if candidate_id is not None:
            rec["candidate_id"] = candidate_id
        if watchlist_id is not None:
            rec["watchlist_id"] = watchlist_id
        if bid_record_id is not None:
            rec["bid_record_id"] = bid_record_id
        if event_id is not None:
            rec["event_id"] = event_id
        if lot_id is not None:
            rec["lot_id"] = lot_id

        res = client.table(Table.NOTIFICATION_LOG).insert(rec).execute()
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as exc:
        logger.error("log_notification failed: %s", exc)
        return None


# ================================================================
# 重複送信防止
# ================================================================

def was_recently_notified(
    client,
    notification_type: str,
    *,
    candidate_id: Optional[str] = None,
    watchlist_id: Optional[str] = None,
    within_hours: float = 6.0,
) -> bool:
    """
    指定した notification_type + 参照IDの組み合わせが
    within_hours 時間以内に送信済みかを確認する。

    重複送信防止のために呼び出す。
    Returns True if already sent recently, False otherwise.
    """
    try:
        since = (
            datetime.now(timezone.utc) - timedelta(hours=within_hours)
        ).isoformat()

        query = (
            client
            .table(Table.NOTIFICATION_LOG)
            .select("id")
            .eq("notification_type", notification_type)
            .eq("status", "sent")
            .gte("sent_at", since)
        )
        if candidate_id is not None:
            query = query.eq("candidate_id", candidate_id)
        if watchlist_id is not None:
            query = query.eq("watchlist_id", watchlist_id)

        res = query.limit(1).execute()
        return bool(res.data)
    except Exception as exc:
        logger.error("was_recently_notified failed: %s", exc)
        return False  # fail open → allow send (better than silently suppressing)


# ================================================================
# job 実行履歴
# ================================================================

def record_morning_brief_run(
    client,
    *,
    run_date: str,
    status: str,
    yahoo_pending_count: int = 0,
    audit_pass_count: int = 0,
    keep_count: int = 0,
    bid_ready_count: int = 0,
    slack_message_ts: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """job_morning_brief_daily に実行履歴を記録する。"""
    try:
        rec = {
            "run_date":           run_date,
            "status":             status,
            "yahoo_pending_count": yahoo_pending_count,
            "audit_pass_count":   audit_pass_count,
            "keep_count":         keep_count,
            "bid_ready_count":    bid_ready_count,
            "slack_message_ts":   slack_message_ts,
            "error_message":      error_message,
        }
        client.table(Table.JOB_MORNING_BRIEF).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("record_morning_brief_run failed: %s", exc)
        return False


def record_notion_sync_run(
    client,
    *,
    run_date: str,
    status: str,
    candidates_synced: int = 0,
    watchlist_synced: int = 0,
    error_count: int = 0,
    error_message: Optional[str] = None,
) -> bool:
    """job_notion_sync_daily に実行履歴を記録する。"""
    try:
        rec = {
            "run_date":          run_date,
            "status":            status,
            "candidates_synced": candidates_synced,
            "watchlist_synced":  watchlist_synced,
            "error_count":       error_count,
            "error_message":     error_message,
        }
        client.table(Table.JOB_NOTION_SYNC).insert(rec).execute()
        return True
    except Exception as exc:
        logger.error("record_notion_sync_run failed: %s", exc)
        return False
