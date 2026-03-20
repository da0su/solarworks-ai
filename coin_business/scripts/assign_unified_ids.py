"""
assign_unified_ids.py
─────────────────────
全テーブルの既存レコードに統一管理IDを付与する。
"""
import json, os, urllib.request, time
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
env = {}
for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
    if line.strip() and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

API_KEY = env["AIRTABLE_API_KEY"]
BASE_ID = env["AIRTABLE_BASE_ID"]

def fetch_all(table_id):
    records, offset = [], ""
    while True:
        params = f"offset={offset}&returnFieldsByFieldId=true" if offset else "returnFieldsByFieldId=true"
        url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}?{params}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        records.extend(data.get("records", []))
        offset = data.get("offset", "")
        if not offset:
            break
    return records

def patch_batch(table_id, records):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}"
    data = json.dumps({"records": records}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }, method="PATCH")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ERROR: {body[:300]}")
        return None

# テーブルごとの設定
# (table_id, unified_id_field_id, prefix, old_id_field_id, old_number_field_id_for_sales)
tables = [
    {
        "name": "コインマスター",
        "table_id": "tblFLv5Cq9zpOmTc6",
        "uid_fld": "fldy955dTozZXB4rX",
        "prefix": "COIN",
        "sort_fld": "fld0sDWG41KHJ99dT",  # coin_id
    },
    {
        "name": "ヤフオク相場履歴",
        "table_id": "tblVvoarXhnFTT9BE",
        "uid_fld": "fldG9ITFxYuXf4KAk",
        "prefix": "MKT",
        "sort_fld": "fldYdrJ8s21xJJB28",  # record_id
    },
    {
        "name": "仕入履歴",
        "table_id": "tblrWMUHlBIn9WWce",
        "uid_fld": "fldqVKp9zwqDtdQZb",
        "prefix": "SRC",
        "sort_fld": "fldjuOpEIlrOpz3ZD",  # record_id
    },
    {
        "name": "出品履歴",
        "table_id": "tbl8Ajg41oN2RilAb",
        "uid_fld": "fldVEby8LYn2PTsBJ",
        "prefix": "LST",
        "sort_fld": "fldWcJTQkErG5QrQZ",  # record_id
    },
    {
        "name": "利益分析",
        "table_id": "tbl8vTCmzelckdEsM",
        "uid_fld": "fld6UDtA2pap6jJWA",
        "prefix": "PFT",
        "sort_fld": "fld3mPeBTzWuPPLJC",  # coin_id
    },
    {
        "name": "在庫台帳",
        "table_id": "tbl1fCIjtjeU7qDuu",
        "uid_fld": "fld62vFQOk81YBmGK",
        "prefix": "INV",
        "sort_fld": "fld01dYfC0Z4N3RpX",  # txn_id
    },
    {
        "name": "棚卸スナップショット",
        "table_id": "tblThZB12lUyugBvy",
        "uid_fld": "fld5CcdAwJVZVkiAv",
        "prefix": "SNP",
        "sort_fld": "flduO9Ryx7BUUn1ul",  # snapshot_id
    },
]

# 販売管理は特殊処理（旧番号保持）
SM_CONFIG = {
    "name": "販売管理",
    "table_id": "tbllEIckahvgklrZ7",
    "uid_fld": "fldyQpRN0fIgw5KNs",
    "old_number_fld": "fldENlGtqZFlzHzGg",  # 旧番号
    "coin_id_fld": "fldqly5OojXSB4g9d",  # coin_id
    "no_fld": "fldPnBH0XfzTe51UJ",  # no
}


def process_table(cfg):
    records = fetch_all(cfg["table_id"])
    print(f"\n{cfg['name']}: {len(records)}件")

    # ソート用フィールドで並べる
    def sort_key(r):
        val = r["fields"].get(cfg["sort_fld"], "")
        return str(val) if val else "zzz"

    records.sort(key=sort_key)

    patches = []
    for i, rec in enumerate(records, 1):
        uid = f"{cfg['prefix']}-{i:04d}"
        patches.append({
            "id": rec["id"],
            "fields": {cfg["uid_fld"]: uid}
        })

    # 10件ずつPATCH
    done = 0
    for i in range(0, len(patches), 10):
        batch = patches[i:i+10]
        r = patch_batch(cfg["table_id"], batch)
        if r:
            done += len(r.get("records", []))
        time.sleep(0.3)

    print(f"  {done}件にID付与完了 ({cfg['prefix']}-0001 〜 {cfg['prefix']}-{len(patches):04d})")


def process_sales():
    cfg = SM_CONFIG
    records = fetch_all(cfg["table_id"])
    print(f"\n{cfg['name']}: {len(records)}件")

    # no順でソート
    def sort_key(r):
        val = r["fields"].get(cfg["no_fld"], 9999)
        return val if isinstance(val, (int, float)) else 9999

    records.sort(key=sort_key)

    patches = []
    for i, rec in enumerate(records, 1):
        uid = f"SAL-{i:04d}"
        old_cid = rec["fields"].get(cfg["coin_id_fld"], "")
        patches.append({
            "id": rec["id"],
            "fields": {
                cfg["uid_fld"]: uid,
                cfg["old_number_fld"]: old_cid,  # 旧coin_id(EXCEL-xxx/SOLD-xxx)を保持
            }
        })

    done = 0
    for i in range(0, len(patches), 10):
        batch = patches[i:i+10]
        r = patch_batch(cfg["table_id"], batch)
        if r:
            done += len(r.get("records", []))
        time.sleep(0.3)

    print(f"  {done}件にID付与完了 (SAL-0001 〜 SAL-{len(patches):04d})")


if __name__ == "__main__":
    print("=== 統一管理ID一括付与 ===")
    for cfg in tables:
        process_table(cfg)
    process_sales()
    print("\n=== 完了 ===")
