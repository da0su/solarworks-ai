"""Supabase接続クライアント — coin_business用

airtable_client.pyの置き換え。
全スクリプトはこのモジュール経由でSupabaseにアクセスする。
"""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_client: Client | None = None


def get_client() -> Client:
    """シングルトンでSupabaseクライアントを取得"""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            print("エラー: SUPABASE_URL / SUPABASE_KEY が .env に設定されていません")
            sys.exit(1)
        _client = create_client(url, key)
    return _client


# ============================================================
# 汎用CRUD
# ============================================================

def insert(table: str, records: list[dict], upsert: bool = False) -> list[dict]:
    """バッチinsert/upsert。records = [{"col": val, ...}, ...]"""
    client = get_client()
    if upsert:
        resp = client.table(table).upsert(records).execute()
    else:
        resp = client.table(table).insert(records).execute()
    return resp.data


def select(table: str, columns: str = "*", filters: dict | None = None,
           order_by: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """汎用SELECT。filtersはカラム名→値の辞書。"""
    client = get_client()
    q = client.table(table).select(columns)
    if filters:
        for col, val in filters.items():
            q = q.eq(col, val)
    if order_by:
        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        q = q.order(col, desc=desc)
    q = q.range(offset, offset + limit - 1)
    return q.execute().data


def count(table: str, filters: dict | None = None) -> int:
    """レコード数取得"""
    client = get_client()
    q = client.table(table).select("id", count="exact")
    if filters:
        for col, val in filters.items():
            q = q.eq(col, val)
    resp = q.execute()
    return resp.count or 0


def raw_query(sql: str) -> list[dict]:
    """SQL直接実行（RPC経由）"""
    client = get_client()
    resp = client.rpc("exec_sql", {"query": sql}).execute()
    return resp.data


# ============================================================
# 重複判定キー生成
# ============================================================

def make_dedup_key(source: str, url: str | None = None,
                   title: str = "", price: int = 0, sold_date: str = "") -> str:
    """ソース別の重複判定キーを生成"""
    if url:
        return f"{source}:{url.strip().rstrip('/')}"
    normalized = re.sub(r'\s+', '', title.lower().strip())
    return f"{source}:{normalized}:{price}:{sold_date}"


# ============================================================
# テーブル別ヘルパー
# ============================================================

def get_market_transactions(source: str | None = None, limit: int = 100,
                            offset: int = 0, **filters) -> list[dict]:
    """market_transactions検索"""
    f = dict(filters)
    if source:
        f["source"] = source
    return select("market_transactions", filters=f, order_by="-sold_date",
                  limit=limit, offset=offset)


def upsert_market_transactions(records: list[dict]) -> list[dict]:
    """market_transactionsへupsert（dedup_keyで重複判定）"""
    return insert("market_transactions", records, upsert=True)


def get_coin_master(coin_id: str | None = None) -> list[dict]:
    """coin_master取得"""
    filters = {"coin_id": coin_id} if coin_id else None
    return select("coin_master", filters=filters, limit=1000)


def get_cost_rules(active_only: bool = True) -> list[dict]:
    """コストルール取得"""
    client = get_client()
    q = client.table("cost_rules").select("*")
    if active_only:
        q = q.is_("effective_to", "null")
    return q.execute().data


def get_exchange_rate(date: str, from_cur: str = "USD", to_cur: str = "JPY") -> float | None:
    """指定日の為替レート取得"""
    results = select("exchange_rates", filters={
        "date": date, "from_currency": from_cur, "to_currency": to_cur
    }, limit=1)
    return results[0]["rate"] if results else None
