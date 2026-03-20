"""
inventory_manager.py
────────────────────
在庫管理・棚卸スクリプト。

使い方:
    python scripts/inventory_manager.py status              # 現在在庫一覧
    python scripts/inventory_manager.py add <coin_id> <種別> # 在庫取引追加
    python scripts/inventory_manager.py snapshot             # 月末棚卸スナップショット生成
    python scripts/inventory_manager.py report               # 在庫サマリーレポート
"""
import json, os, sys, urllib.request
from pathlib import Path
from datetime import datetime

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

ENV = load_env()
API_KEY = ENV["AIRTABLE_API_KEY"]
BASE_ID = ENV["AIRTABLE_BASE_ID"]

# テーブルID
TBL_LEDGER   = "tbl1fCIjtjeU7qDuu"
TBL_SNAPSHOT = "tblThZB12lUyugBvy"
TBL_CM       = "tblFLv5Cq9zpOmTc6"
TBL_PA       = "tbl8vTCmzelckdEsM"

# フィールドID (inventory_ledger)
IL_F = {
    "txn_id":     "fld01dYfC0Z4N3RpX",
    "coin_id":    "fld7GhOAB1bGlgIV4",
    "txn_type":   "flddvUTUHBYyQd8t8",
    "date":       "fldgXoYvNYfhGqGWF",
    "qty":        "fldeungvqBrOij0Np",
    "unit_cost":  "fldFjTzF1kdZSjjti",
    "acq_cost":   "fld91FeR9RLlOtCXY",
    "total_cost": "fldNY0Q3iyVAXY0Fb",
    "status":     "fld8g3LSCcNHqgbko",
    "location":   "flde9A1mNiTAkIXLP",
    "memo":       "fldD7Yip6uNg2y7LR",
}

# フィールドID (inventory_snapshot)
IS_F = {
    "snapshot_id":   "flduO9Ryx7BUUn1ul",
    "base_date":     "fldGpoN8mvaJcHVy3",
    "coin_id":       "fldRe8Us0ubau2I9G",
    "qty":           "flda8iyyzLLBwvIKG",
    "unit_cost":     "fldYsMI6DohLI8BKg",
    "book_value":    "fldQrAVQ6R5QUJaTo",
    "est_sell":      "fldxyzySxlBvQ4WPd",
    "eval_memo":     "fldMpLjUE2ffxgxO4",
    "verifier":      "fldrlke0OCPBf4fQQ",
    "verify_date":   "fldMybKjAUwsNO8BB",
}

CM_F_CID = "fld0sDWG41KHJ99dT"
CM_F_COUNTRY = "fldUTtgUn5f1xvQ3q"
CM_F_YEAR = "fldiRk8ituRjLe823"
CM_F_DENOM = "fldOsGYYzfvC6TLhw"

PA_F_CID = "fld3mPeBTzWuPPLJC"
PA_F_YAHOO = "fldV1IkQmL1nnWpzE"


def api_get(table_id, params=""):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}?returnFieldsByFieldId=true&{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def api_post(table_id, records):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}"
    data = json.dumps({"records": records}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def fetch_all(table_id):
    records, offset = [], ""
    while True:
        params = f"offset={offset}" if offset else ""
        data = api_get(table_id, params)
        records.extend(data.get("records", []))
        offset = data.get("offset", "")
        if not offset:
            break
    return records


def get_current_inventory():
    """各coin_idの最新ステータスを取得"""
    ledger = fetch_all(TBL_LEDGER)

    # coin_idごとに最新の取引を取得
    latest = {}
    for r in ledger:
        f = r["fields"]
        cid = f.get(IL_F["coin_id"], "")
        if not cid:
            continue
        date = f.get(IL_F["date"], "")
        txn_id = f.get(IL_F["txn_id"], "")
        if cid not in latest or date > latest[cid].get("date", ""):
            latest[cid] = {
                "coin_id": cid,
                "status": f.get(IL_F["status"], ""),
                "date": date,
                "unit_cost": f.get(IL_F["unit_cost"], 0),
                "acq_cost": f.get(IL_F["acq_cost"], 0),
                "total_cost": f.get(IL_F["total_cost"], 0),
                "qty": f.get(IL_F["qty"], 0),
                "location": f.get(IL_F["location"], ""),
                "txn_type": f.get(IL_F["txn_type"], ""),
            }
    return latest


def cmd_status():
    """現在在庫一覧"""
    inv = get_current_inventory()

    # coin_master情報
    cm_records = fetch_all(TBL_CM)
    cm_map = {}
    for r in cm_records:
        f = r["fields"]
        cid = f.get(CM_F_CID, "")
        if cid:
            cm_map[cid] = f"{f.get(CM_F_YEAR, '')} {f.get(CM_F_COUNTRY, '')} {f.get(CM_F_DENOM, '')}"

    holding = []
    listing = []
    sold = []
    total_cost = 0

    for cid, data in sorted(inv.items()):
        name = cm_map.get(cid, cid)
        status = data["status"]
        cost = data["total_cost"] or data["acq_cost"] or 0

        row = {"coin_id": cid, "name": name, "status": status, "cost": cost, "location": data["location"]}
        if status == "保有中":
            holding.append(row)
            total_cost += cost
        elif status == "出品中":
            listing.append(row)
            total_cost += cost
        elif status == "売却済":
            sold.append(row)

    print("=" * 80)
    print("在庫ステータス一覧")
    print("=" * 80)

    if holding:
        print(f"\n■ 保有中: {len(holding)}件")
        for r in holding:
            print(f"  {r['coin_id']}: {r['name'][:30]} / 原価: {r['cost']:,}円 / {r['location']}")

    if listing:
        print(f"\n■ 出品中: {len(listing)}件")
        for r in listing:
            print(f"  {r['coin_id']}: {r['name'][:30]} / 原価: {r['cost']:,}円 / {r['location']}")

    if sold:
        print(f"\n■ 売却済: {len(sold)}件")
        for r in sold:
            print(f"  {r['coin_id']}: {r['name'][:30]}")

    print(f"\n{'─' * 40}")
    print(f"在庫原価総額（保有中+出品中）: {total_cost:,}円")
    print(f"在庫コイン数: {len(holding) + len(listing)}件")
    print("=" * 80)


def cmd_snapshot():
    """月末棚卸スナップショット生成"""
    inv = get_current_inventory()
    today = datetime.now().strftime("%Y-%m-%d")

    # profit_analysis から想定売価取得
    pa_records = fetch_all(TBL_PA)
    pa_map = {}
    for r in pa_records:
        f = r["fields"]
        cid = f.get(PA_F_CID, "")
        if cid:
            pa_map[cid] = f.get(PA_F_YAHOO, 0)

    records = []
    seq = 1
    for cid, data in sorted(inv.items()):
        if data["status"] not in ("保有中", "出品中"):
            continue
        cost = data["total_cost"] or data["acq_cost"] or 0
        est_sell = pa_map.get(cid, 0)

        records.append({"fields": {
            IS_F["snapshot_id"]: f"SS-{today}-{seq:03d}",
            IS_F["base_date"]: today,
            IS_F["coin_id"]: cid,
            IS_F["qty"]: data.get("qty", 1),
            IS_F["unit_cost"]: cost,
            IS_F["book_value"]: cost,
            IS_F["est_sell"]: est_sell,
            IS_F["eval_memo"]: f"在庫ステータス: {data['status']}",
            IS_F["verifier"]: "COO-AI",
            IS_F["verify_date"]: today,
        }})
        seq += 1

    if records:
        # 10件ずつバッチ投入
        for i in range(0, len(records), 10):
            batch = records[i:i+10]
            api_post(TBL_SNAPSHOT, batch)

    print(f"棚卸スナップショット: {len(records)}件を {today} で保存")
    return records


def cmd_report():
    """在庫サマリーレポート"""
    inv = get_current_inventory()
    today = datetime.now().strftime("%Y-%m-%d")

    cm_records = fetch_all(TBL_CM)
    cm_map = {}
    for r in cm_records:
        f = r["fields"]
        cid = f.get(CM_F_CID, "")
        if cid:
            cm_map[cid] = f"{f.get(CM_F_YEAR, '')} {f.get(CM_F_COUNTRY, '')} {f.get(CM_F_DENOM, '')}"

    pa_records = fetch_all(TBL_PA)
    pa_map = {}
    for r in pa_records:
        f = r["fields"]
        cid = f.get(PA_F_CID, "")
        if cid:
            pa_map[cid] = f.get(PA_F_YAHOO, 0)

    lines = []
    lines.append(f"# 在庫・棚卸サマリーレポート")
    lines.append(f"**日付**: {today}")
    lines.append("")

    holding, listing, sold = [], [], []
    total_cost = 0
    total_est = 0

    for cid, data in sorted(inv.items()):
        name = cm_map.get(cid, cid)
        cost = data["total_cost"] or data["acq_cost"] or 0
        est = pa_map.get(cid, 0)

        row = {"coin_id": cid, "name": name, "cost": cost, "est": est, "loc": data["location"]}
        if data["status"] == "保有中":
            holding.append(row)
            total_cost += cost
            total_est += est
        elif data["status"] == "出品中":
            listing.append(row)
            total_cost += cost
            total_est += est
        elif data["status"] == "売却済":
            sold.append(row)

    lines.append(f"## サマリー")
    lines.append(f"- 保有中: {len(holding)}件")
    lines.append(f"- 出品中: {len(listing)}件")
    lines.append(f"- 売却済: {len(sold)}件")
    lines.append(f"- **在庫原価総額: {total_cost:,}円**")
    lines.append(f"- 参考想定売価総額: {total_est:,}円")
    unrealized = total_est - total_cost
    lines.append(f"- 含み益: {unrealized:,}円")
    lines.append("")

    if holding:
        lines.append("## 保有中")
        lines.append("| coin_id | コイン名 | 原価 | 想定売価 | 保管場所 |")
        lines.append("|---------|---------|------|---------|---------|")
        for r in holding:
            lines.append(f"| {r['coin_id']} | {r['name'][:25]} | {r['cost']:,} | {r['est']:,} | {r['loc']} |")
        lines.append("")

    if listing:
        lines.append("## 出品中")
        lines.append("| coin_id | コイン名 | 原価 | 想定売価 | 保管場所 |")
        lines.append("|---------|---------|------|---------|---------|")
        for r in listing:
            lines.append(f"| {r['coin_id']} | {r['name'][:25]} | {r['cost']:,} | {r['est']:,} | {r['loc']} |")
        lines.append("")

    path = OUTPUT_DIR / f"{today}_inventory_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n在庫レポート出力: {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inventory_manager.py [status|snapshot|report]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "report":
        cmd_report()
    else:
        print(f"Unknown command: {cmd}")
