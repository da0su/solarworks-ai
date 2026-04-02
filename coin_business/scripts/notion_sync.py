"""
coin_business/scripts/notion_sync.py
======================================
候補・watchlist・audit 状態を Notion 台帳へ一方向同期する。

同期対象:
  1. daily_candidates   → Notion DB "候補台帳"
     (audit_status, watch_status, target_max_bid_jpy, comparison_quality_score)
  2. candidate_watchlist → Notion DB "KEEP監視台帳"
     (status, current_price_jpy, max_bid_jpy, auction_end_at, time_left_seconds)

方針:
  - 一方向: DB → Notion のみ。Notion → DB の書き戻しはしない。
  - Upsert: Notion ページが既存なら Update、なければ Create。
    (既存判定: ページプロパティ "supabase_id" で照合)
  - エラー時: 1件失敗しても他は継続。error_count を増やすのみ。

環境変数:
  NOTION_TOKEN      : Integration トークン
  NOTION_CANDIDATE_DB_ID   : 候補台帳 Notion DB ID (32桁)
  NOTION_WATCHLIST_DB_ID   : KEEP監視台帳 Notion DB ID (32桁)
  ※ 未設定の場合は当該同期をスキップ

CLI:
  python notion_sync.py --dry-run
  python notion_sync.py --candidates-only
  python notion_sync.py --watchlist-only
  python notion_sync.py --limit 50
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from constants import AuditStatus, Table, WatchStatus
from db.notification_repo import record_notion_sync_run
from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)

# ================================================================
# 環境変数
# ================================================================

_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

NOTION_TOKEN          = os.environ.get("NOTION_TOKEN", "")
NOTION_CANDIDATE_DB   = os.environ.get("NOTION_CANDIDATE_DB_ID", "")
NOTION_WATCHLIST_DB   = os.environ.get("NOTION_WATCHLIST_DB_ID", "")
NOTION_API_BASE       = "https://api.notion.com/v1"
NOTION_VERSION        = "2022-06-28"


# ================================================================
# Notion API クライアント (軽量ラッパー)
# ================================================================

def _notion_headers() -> dict:
    return {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def _notion_request(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    """
    Notion API へ HTTP リクエストを送る。
    Returns parsed JSON dict or None on error.
    """
    if not NOTION_TOKEN:
        logger.warning("NOTION_TOKEN 未設定 — Notion リクエストをスキップ")
        return None
    url     = f"{NOTION_API_BASE}{path}"
    data    = json.dumps(body).encode("utf-8") if body else None
    req     = urllib.request.Request(url, data=data, headers=_notion_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.warning("Notion %s %s → %s %s", method, path, exc.code, err_body[:200])
        return None
    except Exception as exc:
        logger.warning("Notion request failed: %s", exc)
        return None


def _query_notion_by_supabase_id(db_id: str, supabase_id: str) -> Optional[str]:
    """
    db_id の Notion DB から supabase_id が一致するページを検索する。
    Returns Notion page_id or None.
    """
    body = {
        "filter": {
            "property": "supabase_id",
            "rich_text": {"equals": supabase_id},
        },
        "page_size": 1,
    }
    result = _notion_request("POST", f"/databases/{db_id}/query", body)
    if result and result.get("results"):
        return result["results"][0]["id"]
    return None


# ================================================================
# プロパティビルダー
# ================================================================

def _txt(value: str) -> dict:
    return {"rich_text": [{"text": {"content": str(value)[:2000]}}]}


def _title_prop(value: str) -> dict:
    return {"title": [{"text": {"content": str(value)[:2000]}}]}


def _num(value) -> dict:
    return {"number": float(value) if value is not None else None}


def _select(value: str) -> dict:
    return {"select": {"name": str(value)} if value else None}


def _date_prop(value: str) -> dict:
    """ISO 文字列 → Notion date プロパティ"""
    if not value:
        return {"date": None}
    # Notion は YYYY-MM-DDTHH:MM:SS+09:00 形式
    return {"date": {"start": str(value)[:25]}}


def _build_candidate_properties(cand: dict) -> dict:
    title_str = (
        cand.get("title")
        or cand.get("lot_title")
        or cand.get("coin_name")
        or f"candidate_{str(cand.get('id',''))[:8]}"
    )
    props: dict = {
        "名称":                _title_prop(title_str),
        "supabase_id":         _txt(str(cand.get("id", ""))),
        "audit_status":        _select(cand.get("audit_status") or ""),
        "target_max_bid_jpy":  _num(cand.get("target_max_bid_jpy")),
        "comparison_quality_score": _num(cand.get("comparison_quality_score")),
        "country":             _txt(cand.get("country") or ""),
        "grade":               _txt(cand.get("grade") or ""),
        "year":                _num(cand.get("year")),
        "source":              _txt(cand.get("source") or ""),
        "created_at":          _date_prop(cand.get("created_at") or ""),
    }
    return props


def _build_watchlist_properties(item: dict) -> dict:
    props: dict = {
        "watchlist_id":        _title_prop(str(item.get("id", ""))[:36]),
        "supabase_id":         _txt(str(item.get("id", ""))),
        "status":              _select(item.get("status") or "watching"),
        "current_price_jpy":   _num(item.get("current_price_jpy")),
        "max_bid_jpy":         _num(item.get("max_bid_jpy")),
        "time_left_seconds":   _num(item.get("time_left_seconds")),
        "auction_end_at":      _date_prop(item.get("auction_end_at") or ""),
        "is_bid_ready":        {"checkbox": bool(item.get("is_bid_ready"))},
        "added_at":            _date_prop(item.get("added_at") or ""),
    }
    return props


# ================================================================
# 同期ロジック
# ================================================================

@dataclass
class SyncResult:
    candidates_synced: int = 0
    watchlist_synced:  int = 0
    error_count:       int = 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.candidates_synced + self.watchlist_synced > 0:
            return "partial"
        return "error"


def _upsert_notion_page(
    db_id: str,
    supabase_id: str,
    properties: dict,
    dry_run: bool,
) -> bool:
    """
    supabase_id が既存なら Update、なければ Create。
    Returns True on success (or dry_run), False on error.
    """
    if dry_run:
        logger.info("[DRY-RUN] notion upsert db=%s id=%s", db_id[:8], supabase_id[:8])
        return True

    existing_page_id = _query_notion_by_supabase_id(db_id, supabase_id)
    if existing_page_id:
        result = _notion_request(
            "PATCH",
            f"/pages/{existing_page_id}",
            {"properties": properties},
        )
    else:
        result = _notion_request(
            "POST",
            "/pages",
            {"parent": {"database_id": db_id}, "properties": properties},
        )
    return result is not None


def sync_candidates(
    client,
    *,
    limit: int = 50,
    dry_run: bool = False,
) -> int:
    """
    AUDIT_PASS / AUDIT_HOLD の daily_candidates を Notion 台帳へ同期。
    Returns synced count.
    """
    if not NOTION_CANDIDATE_DB:
        logger.info("NOTION_CANDIDATE_DB_ID 未設定 — 候補同期スキップ")
        return 0

    try:
        res = (
            client.table(Table.DAILY_CANDIDATES)
            .select("*")
            .in_("audit_status", [AuditStatus.AUDIT_PASS, AuditStatus.AUDIT_HOLD])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        candidates = res.data or []
    except Exception as exc:
        logger.error("sync_candidates fetch failed: %s", exc)
        return 0

    synced = 0
    for cand in candidates:
        props = _build_candidate_properties(cand)
        ok = _upsert_notion_page(
            NOTION_CANDIDATE_DB, str(cand["id"]), props, dry_run
        )
        if ok:
            synced += 1
        else:
            logger.warning("notion upsert failed for candidate %s", cand.get("id"))

    return synced


def sync_watchlist(
    client,
    *,
    limit: int = 50,
    dry_run: bool = False,
) -> int:
    """
    ACTIVE な watchlist アイテムを Notion 台帳へ同期。
    Returns synced count.
    """
    if not NOTION_WATCHLIST_DB:
        logger.info("NOTION_WATCHLIST_DB_ID 未設定 — watchlist 同期スキップ")
        return 0

    try:
        res = (
            client.table(Table.CANDIDATE_WATCHLIST)
            .select("*")
            .in_("status", list(WatchStatus.ACTIVE) + [WatchStatus.BID_READY])
            .order("added_at", desc=True)
            .limit(limit)
            .execute()
        )
        items = res.data or []
    except Exception as exc:
        logger.error("sync_watchlist fetch failed: %s", exc)
        return 0

    synced = 0
    for item in items:
        props = _build_watchlist_properties(item)
        ok = _upsert_notion_page(
            NOTION_WATCHLIST_DB, str(item["id"]), props, dry_run
        )
        if ok:
            synced += 1
        else:
            logger.warning("notion upsert failed for watchlist %s", item.get("id"))

    return synced


def run_notion_sync(
    *,
    dry_run:         bool = False,
    candidates_only: bool = False,
    watchlist_only:  bool = False,
    limit:           int  = 50,
) -> SyncResult:
    """
    Notion 台帳への一方向同期を実行する。
    """
    result = SyncResult()
    client = get_client()

    if not watchlist_only:
        try:
            result.candidates_synced = sync_candidates(
                client, limit=limit, dry_run=dry_run
            )
        except Exception as exc:
            logger.error("sync_candidates error: %s", exc)
            result.error_count += 1

    if not candidates_only:
        try:
            result.watchlist_synced = sync_watchlist(
                client, limit=limit, dry_run=dry_run
            )
        except Exception as exc:
            logger.error("sync_watchlist error: %s", exc)
            result.error_count += 1

    if not dry_run:
        record_notion_sync_run(
            client,
            run_date          = date.today().isoformat(),
            status            = result.status_str(),
            candidates_synced = result.candidates_synced,
            watchlist_synced  = result.watchlist_synced,
            error_count       = result.error_count,
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
        description="Notion 台帳への一方向同期"
    )
    parser.add_argument("--dry-run",         action="store_true",
                        help="Notion への書き込みなし")
    parser.add_argument("--candidates-only", action="store_true",
                        help="候補台帳のみ同期")
    parser.add_argument("--watchlist-only",  action="store_true",
                        help="KEEP監視台帳のみ同期")
    parser.add_argument("--limit",           type=int, default=50,
                        help="同期件数上限 (default: 50)")
    args = parser.parse_args()

    result = run_notion_sync(
        dry_run         = args.dry_run,
        candidates_only = args.candidates_only,
        watchlist_only  = args.watchlist_only,
        limit           = args.limit,
    )
    print(
        f"notion_sync done: candidates={result.candidates_synced} "
        f"watchlist={result.watchlist_synced} errors={result.error_count} "
        f"status={result.status_str()}"
    )


if __name__ == "__main__":
    main()
