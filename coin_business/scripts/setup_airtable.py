"""Airtable Base・テーブル自動セットアップ

使い方:
  1. Airtable で Personal Access Token (PAT) を発行
     権限: data.records:read, data.records:write, schema.bases:read, schema.bases:write
  2. .env に AIRTABLE_API_KEY を設定
  3. python scripts/setup_airtable.py create_base   → Base作成（初回のみ）
  4. python scripts/setup_airtable.py create_tables  → テーブル作成
  5. python scripts/setup_airtable.py seed           → 初期データ投入
  6. python scripts/setup_airtable.py all            → 全部実行
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# ================================================================
# テーブル定義
# ================================================================

TABLE_SCHEMAS = {
    "coin_master": {
        "description": "コインマスター（全コインの基本情報）",
        "fields": [
            {"name": "coin_id", "type": "singleLineText", "description": "主キー"},
            {"name": "国", "type": "singleLineText"},
            {"name": "年号", "type": "singleLineText"},
            {"name": "額面", "type": "singleLineText"},
            {"name": "素材", "type": "singleLineText", "description": "例：銀99.9%"},
            {"name": "重量", "type": "singleLineText"},
            {"name": "直径", "type": "singleLineText"},
            {"name": "鑑定会社", "type": "singleLineText"},
            {"name": "グレード", "type": "singleLineText"},
            {"name": "特徴タグ", "type": "multipleSelects", "options": {
                "choices": [
                    {"name": "PF70"},
                    {"name": "PF69"},
                    {"name": "First Releases"},
                    {"name": "Advance Releases"},
                    {"name": "Shield Privy"},
                    {"name": "サインラベル"},
                    {"name": "限定発行"},
                ]
            }},
        ],
    },
    "yahoo_sales_history": {
        "description": "ヤフオク販売履歴（売れた価格のみ）",
        "fields": [
            {"name": "coin_id", "type": "singleLineText", "description": "リンク用"},
            {"name": "販売価格", "type": "number", "options": {"precision": 0}},
            {"name": "販売日", "type": "date", "options": {"dateFormat": {"name": "iso"}}},
            {"name": "URL", "type": "url"},
            {"name": "回転日数", "type": "number", "options": {"precision": 0}},
            {"name": "備考", "type": "singleLineText"},
        ],
    },
    "sourcing_history": {
        "description": "仕入履歴",
        "fields": [
            {"name": "coin_id", "type": "singleLineText", "description": "リンク用"},
            {"name": "仕入価格", "type": "number", "options": {"precision": 2}},
            {"name": "通貨", "type": "singleLineText"},
            {"name": "送料_海外", "type": "number", "options": {"precision": 2}},
            {"name": "関税", "type": "number", "options": {"precision": 0}},
            {"name": "合計仕入原価", "type": "number", "options": {"precision": 0}},
            {"name": "サイト", "type": "singleLineText"},
            {"name": "URL", "type": "url"},
        ],
    },
    "listing_history": {
        "description": "出品履歴",
        "fields": [
            {"name": "coin_id", "type": "singleLineText", "description": "リンク用"},
            {"name": "タイトル", "type": "singleLineText"},
            {"name": "商品説明文", "type": "multilineText"},
            {"name": "開始価格", "type": "number", "options": {"precision": 0}},
            {"name": "即決価格", "type": "number", "options": {"precision": 0}},
            {"name": "成約価格", "type": "number", "options": {"precision": 0}},
            {"name": "出品日", "type": "date", "options": {"dateFormat": {"name": "iso"}}},
            {"name": "販売日", "type": "date", "options": {"dateFormat": {"name": "iso"}}},
        ],
    },
    "cost_rules": {
        "description": "コストルール（手数料・送料等）",
        "fields": [
            {"name": "項目名", "type": "singleLineText"},
            {"name": "値", "type": "singleLineText"},
        ],
    },
}

# ================================================================
# 初期データ
# ================================================================

SEED_DATA = {
    "coin_master": [{
        "fields": {
            "coin_id": "UK-2025-SOV-1OZ-AG-PF70",
            "国": "イギリス",
            "年号": "2025",
            "額面": "1ソブリン",
            "素材": "銀99.9%",
            "重量": "31.1g",
            "直径": "38.61mm",
            "鑑定会社": "NGC",
            "グレード": "PF70 Ultra Cameo",
            "特徴タグ": ["PF70", "First Releases"],
        }
    }],
    "yahoo_sales_history": [{
        "fields": {
            "coin_id": "UK-2025-SOV-1OZ-AG-PF70",
            "販売価格": 150000,
            "販売日": "2026-03-10",
            "回転日数": 7,
            "備考": "初回テストデータ",
        }
    }],
    "sourcing_history": [{
        "fields": {
            "coin_id": "UK-2025-SOV-1OZ-AG-PF70",
            "仕入価格": 800,
            "通貨": "USD",
            "送料_海外": 25,
            "関税": 12000,
            "合計仕入原価": 136500,
            "サイト": "eBay",
        }
    }],
    "listing_history": [{
        "fields": {
            "coin_id": "UK-2025-SOV-1OZ-AG-PF70",
            "タイトル": "【NGC PF70】2025 イギリス ソブリン 1オンス銀貨 First Releases",
            "商品説明文": "NGC PF70 Ultra Cameo鑑定済み。2025年発行イギリスソブリン1オンス銀貨。First Releasesラベル付き。",
            "開始価格": 120000,
            "即決価格": 165000,
            "出品日": "2026-03-03",
        }
    }],
    "cost_rules": [
        {"fields": {"項目名": "ヤフオク手数料", "値": "10%"}},
        {"fields": {"項目名": "国内送料", "値": "1000円"}},
        {"fields": {"項目名": "関税率", "値": "5.5%"}},
    ],
}


# ================================================================
# 実行関数
# ================================================================

def create_base():
    """Airtable Baseを新規作成"""
    print("Base作成中: coin_business_db")
    url = "https://api.airtable.com/v0/meta/bases"
    payload = {
        "name": "coin_business_db",
        "tables": [
            {
                "name": name,
                "description": schema["description"],
                "fields": schema["fields"],
            }
            for name, schema in TABLE_SCHEMAS.items()
        ],
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        base_id = data["id"]
        print(f"Base作成完了: {base_id}")
        print(f"\n.envに以下を追加してください:")
        print(f"AIRTABLE_BASE_ID={base_id}")

        # .envファイルを自動更新
        env_path = PROJECT_ROOT / ".env"
        env_content = ""
        if env_path.exists():
            env_content = env_path.read_text(encoding="utf-8")

        if "AIRTABLE_BASE_ID" in env_content:
            lines = env_content.split("\n")
            lines = [f"AIRTABLE_BASE_ID={base_id}" if l.startswith("AIRTABLE_BASE_ID") else l for l in lines]
            env_content = "\n".join(lines)
        else:
            env_content += f"\nAIRTABLE_BASE_ID={base_id}\n"

        env_path.write_text(env_content, encoding="utf-8")
        print(f".env更新完了")

        # テーブルID保存
        table_map = {t["name"]: t["id"] for t in data.get("tables", [])}
        map_path = PROJECT_ROOT / "data" / "table_ids.json"
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(table_map, f, ensure_ascii=False, indent=2)
        print(f"テーブルIDマップ保存: {map_path}")

        return base_id
    else:
        print(f"エラー: {resp.status_code}")
        print(resp.text)
        sys.exit(1)


def get_table_ids():
    """BaseからテーブルID一覧を取得"""
    map_path = PROJECT_ROOT / "data" / "table_ids.json"
    if map_path.exists():
        with open(map_path, "r", encoding="utf-8") as f:
            return json.load(f)

    base_id = os.environ.get("AIRTABLE_BASE_ID", "")
    if not base_id:
        print("AIRTABLE_BASE_IDが設定されていません")
        sys.exit(1)

    url = f"https://api.airtable.com/v0/meta/bases/{base_id}/tables"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = resp.json()["tables"]
    table_map = {t["name"]: t["id"] for t in tables}

    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(table_map, f, ensure_ascii=False, indent=2)

    return table_map


def seed_data():
    """初期データを投入"""
    base_id = os.environ.get("AIRTABLE_BASE_ID", "")
    if not base_id:
        print("AIRTABLE_BASE_IDが設定されていません")
        sys.exit(1)

    print("初期データ投入中...")
    for table_name, records in SEED_DATA.items():
        url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
        payload = {"records": records}
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        if resp.status_code == 200:
            created = resp.json()["records"]
            print(f"  {table_name}: {len(created)}件投入完了")
        else:
            print(f"  {table_name}: エラー {resp.status_code}")
            print(f"  {resp.text[:200]}")

    print("初期データ投入完了")


def verify():
    """接続・データ確認"""
    base_id = os.environ.get("AIRTABLE_BASE_ID", "")
    if not base_id:
        print("AIRTABLE_BASE_IDが設定されていません")
        sys.exit(1)

    print("接続確認中...")
    for table_name in TABLE_SCHEMAS:
        url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
        resp = requests.get(url, headers=HEADERS, params={"maxRecords": 1}, timeout=30)
        if resp.status_code == 200:
            count = len(resp.json().get("records", []))
            print(f"  {table_name}: OK ({count}件)")
        else:
            print(f"  {table_name}: NG ({resp.status_code})")

    print("接続確認完了")


def run_all():
    """Base作成→データ投入→確認を一括実行"""
    base_id = create_base()
    os.environ["AIRTABLE_BASE_ID"] = base_id
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    seed_data()
    verify()


COMMANDS = {
    "create_base": create_base,
    "seed": seed_data,
    "verify": verify,
    "all": run_all,
}

if __name__ == "__main__":
    if not API_KEY:
        print("エラー: AIRTABLE_API_KEY が設定されていません")
        print("1. Airtable で PAT を発行")
        print("2. coin_business/.env に AIRTABLE_API_KEY=pat_xxx を設定")
        sys.exit(1)

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(__doc__)
        print(f"利用可能: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[cmd]()
