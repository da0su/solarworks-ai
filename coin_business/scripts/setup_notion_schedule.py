"""
Notion オークションスケジュール DB セットアップスクリプト

事前準備:
  1. Notion で Integration を作成 → Token を取得
     https://www.notion.so/my-integrations
  2. スケジュールを貼り付けたいページを開き、IntegrationをConnectする
  3. ページURLからページIDを取得（32桁の英数字）

使い方:
  python scripts/setup_notion_schedule.py --token <token> --parent-id <page_id>

実行すると:
  - Notion にデータベースを作成
  - 14件のオークションを一括登録
  - タイムライン表示 / テーブル表示の両方でアクセス可能
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

import requests

# auction_schedule.json のパス
SCHEDULE_FILE = Path(__file__).parent.parent / "data" / "auction_schedule.json"

# Notion API
NOTION_VERSION = "2022-06-28"
NOTION_BASE    = "https://api.notion.com/v1"

# 優先度ラベル
PRIORITY_LABEL = {3: "⭐⭐⭐ 最重要", 2: "⭐⭐ 重要", 1: "⭐ 参考"}
# 重点対象ラベル
APRIL_LABEL = {True: "4月重点", False: "-"}


def notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_database(token: str, parent_id: str) -> str:
    """Notion にオークションスケジュール DB を作成。DB の ID を返す。"""
    url = f"{NOTION_BASE}/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "🌍 海外コインオークション 年間スケジュール"}}],
        "properties": {
            "オークション名": {"title": {}},
            "会社": {
                "select": {
                    "options": [
                        {"name": "Heritage Auctions",     "color": "red"},
                        {"name": "Stack's Bowers",        "color": "blue"},
                        {"name": "Noonans Mayfair",       "color": "green"},
                        {"name": "Noble Numismatics",     "color": "orange"},
                        {"name": "Spink",                 "color": "yellow"},
                        {"name": "SINCONA",               "color": "purple"},
                    ]
                }
            },
            "開催開始日": {"date": {}},
            "開催終了日": {"date": {}},
            "重要度": {
                "select": {
                    "options": [
                        {"name": "⭐⭐⭐ 最重要", "color": "red"},
                        {"name": "⭐⭐ 重要",     "color": "yellow"},
                        {"name": "⭐ 参考",       "color": "gray"},
                    ]
                }
            },
            "4月重点": {
                "select": {
                    "options": [
                        {"name": "4月重点", "color": "red"},
                        {"name": "-",       "color": "gray"},
                    ]
                }
            },
            "種別": {
                "multi_select": {
                    "options": [
                        {"name": "world_coins",    "color": "blue"},
                        {"name": "asian_coins",    "color": "orange"},
                        {"name": "us_coins",       "color": "green"},
                        {"name": "ancient_coins",  "color": "purple"},
                        {"name": "gold_coins",     "color": "yellow"},
                        {"name": "british_coins",  "color": "gray"},
                        {"name": "australian_coins","color": "pink"},
                        {"name": "chinese_coins",  "color": "red"},
                    ]
                }
            },
            "自動監視候補": {"checkbox": {}},
            "備考": {"rich_text": {}},
            "URL": {"url": {}},
        },
    }
    resp = requests.post(url, headers=notion_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    db_id = resp.json()["id"]
    print(f"✅ データベース作成: {db_id}")
    return db_id


COMPANY_LABEL = {
    "heritage":      "Heritage Auctions",
    "stacks_bowers": "Stack's Bowers",
    "noonans":       "Noonans Mayfair",
    "noble":         "Noble Numismatics",
    "spink":         "Spink",
    "sincona":       "SINCONA",
}


def add_auction(token: str, db_id: str, auction: dict) -> None:
    """1件のオークションを DB に追加"""
    url = f"{NOTION_BASE}/pages"

    # 自動監視候補: priority=3 かつ april_focus=True のもの
    auto_monitor = auction.get("priority", 0) >= 3 and auction.get("april_focus", False)

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "オークション名": {
                "title": [{"text": {"content": auction.get("name", "")}}]
            },
            "会社": {
                "select": {"name": COMPANY_LABEL.get(auction.get("company", ""), auction.get("company", ""))}
            },
            "開催開始日": {
                "date": {"start": auction["start_date"]}
            },
            "開催終了日": {
                "date": {"start": auction["end_date"]}
            },
            "重要度": {
                "select": {"name": PRIORITY_LABEL.get(auction.get("priority", 1), "⭐ 参考")}
            },
            "4月重点": {
                "select": {"name": APRIL_LABEL.get(auction.get("april_focus", False), "-")}
            },
            "種別": {
                "multi_select": [{"name": t} for t in (auction.get("type") or [])]
            },
            "自動監視候補": {"checkbox": auto_monitor},
            "備考": {
                "rich_text": [{"text": {"content": auction.get("notes", "")}}]
            },
            "URL": {"url": auction.get("url", "") or None},
        },
    }
    resp = requests.post(url, headers=notion_headers(token), json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"  ⚠️ 登録失敗: {auction.get('name')} → {resp.status_code} {resp.text[:100]}")
    else:
        print(f"  ✅ {auction.get('name')}")


def main():
    parser = argparse.ArgumentParser(description="Notion オークションスケジュール DB セットアップ")
    parser.add_argument("--token", required=True, help="Notion Integration Token (secret_xxx)")
    parser.add_argument("--parent-id", required=True, help="貼り付け先ページID（URLから取得）")
    args = parser.parse_args()

    # スケジュール読み込み
    if not SCHEDULE_FILE.exists():
        print(f"❌ auction_schedule.json が見つかりません: {SCHEDULE_FILE}")
        sys.exit(1)
    with open(SCHEDULE_FILE, encoding="utf-8") as f:
        schedule = json.load(f)
    auctions = schedule.get("auctions", [])
    print(f"登録対象: {len(auctions)}件")

    # DB 作成
    db_id = create_database(args.token, args.parent_id)

    # 全オークション登録
    print("\nオークション登録中...")
    for a in sorted(auctions, key=lambda x: x.get("start_date", "")):
        add_auction(args.token, db_id, a)

    print(f"\n🎉 完了: {len(auctions)}件を Notion に登録しました")
    print(f"DB URL: https://www.notion.so/{db_id.replace('-', '')}")
    print("\nタイムライン表示の設定方法:")
    print("  1. Notion でDBを開く")
    print("  2. 右上「+ ビューを追加」→「タイムライン」")
    print("  3. 日付プロパティ: 開催開始日 / 終了日: 開催終了日")
    print("  4. グループ化: 会社 または 重要度")


if __name__ == "__main__":
    main()
