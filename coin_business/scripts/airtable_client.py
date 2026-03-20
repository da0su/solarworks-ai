"""Airtable接続クライアント

coin_business全体で使う共通Airtableアクセス。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pyairtable import Api, Table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")


def get_table(table_name: str) -> Table:
    """指定テーブルのTableオブジェクトを返す"""
    if not API_KEY or not BASE_ID:
        raise RuntimeError("AIRTABLE_API_KEY / AIRTABLE_BASE_ID が未設定")
    api = Api(API_KEY)
    return api.table(BASE_ID, table_name)


def get_coin_master() -> Table:
    return get_table("coin_master")


def get_yahoo_sales() -> Table:
    return get_table("yahoo_sales_history")


def get_sourcing() -> Table:
    return get_table("sourcing_history")


def get_listing() -> Table:
    return get_table("listing_history")


def get_cost_rules() -> Table:
    return get_table("cost_rules")
