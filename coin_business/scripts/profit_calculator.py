"""
profit_calculator.py
────────────────────
coin_master / sourcing_history / yahoo_sales_history / cost_rules から
利益率・ランク・投資優先度・AIコメントを算出し profit_analysis へ書き込む。

使い方:
    python scripts/profit_calculator.py          # 全件計算
    python scripts/profit_calculator.py COIN-001  # 指定コインのみ
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

# ── 環境変数 ──────────────────────────────────────────
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

ENV = load_env()
API_KEY = ENV.get("AIRTABLE_API_KEY", "")
BASE_ID = ENV.get("AIRTABLE_BASE_ID", "")

# ── テーブルID ────────────────────────────────────────
TBL_COIN_MASTER       = "tblFLv5Cq9zpOmTc6"
TBL_YAHOO_SALES       = "tblVvoarXhnFTT9BE"
TBL_SOURCING          = "tblrWMUHlBIn9WWce"
TBL_COST_RULES        = "tblbG0yaUNPlnhZ8J"
TBL_PROFIT_ANALYSIS   = "tbl8vTCmzelckdEsM"

# ── profit_analysis フィールドID ──────────────────────
FLD = {
    "coin_id":        "fld3mPeBTzWuPPLJC",
    "想定ヤフオク売価": "fldV1IkQmL1nnWpzE",
    "仕入予想額":      "fldWIEXPnswA9wWXM",
    "海外送料":        "fldO2hkVVG0ferq6v",
    "関税":           "fldrshNcU8FnE1on7",
    "国内送料":        "fldKNc3mZittxj7wS",
    "ヤフオク手数料":   "fldl3gAZF3IBuQr7I",
    "合計コスト":      "fldtnyHufkfzpWD2W",
    "想定利益":        "fldOuiXznY1wLkTH1",
    "利益率":         "fldZksUbeKWy0yxGK",
    "粗利ランク":      "fldbVUncJjQLrO10g",
    "投資優先度":      "fldubs9C47dTwJkUp",
    "AIコメント":      "fldTcC1DfczdDYOHH",
    "判定":           "fldgA1aAkAZ6LsWHS",
}

# ── API helpers ───────────────────────────────────────
def api_get(table_id, params="", use_field_ids=True):
    sep = "&" if params else ""
    fid = f"{sep}returnFieldsByFieldId=true" if use_field_ids else ""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}?{params}{fid}"
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

def api_patch(table_id, records):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}"
    data = json.dumps({"records": records}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }, method="PATCH")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── ランク・優先度・コメント ──────────────────────────
def calc_rank(profit_rate: float) -> str:
    if profit_rate >= 0.30: return "S"
    if profit_rate >= 0.25: return "A"
    if profit_rate >= 0.20: return "B"
    if profit_rate >= 0.15: return "C"
    if profit_rate >= 0.10: return "D"
    return "E"

PRIORITY_MAP = {
    "S": "最優先",
    "A": "優先",
    "B": "通常",
    "C": "条件付き",
    "D": "見送り寄り",
    "E": "見送り",
}

COMMENT_MAP = {
    "S": "利益率30%以上。即入札対象",
    "A": "利益率25%以上。積極的に検討",
    "B": "安定利益ゾーン。競争状況次第",
    "C": "薄利。相場上振れ期待あり",
    "D": "利益低。基本見送り",
    "E": "赤字リスク。見送り推奨",
}

def calc_judgment(profit: float, profit_rate: float) -> str:
    if profit >= 20000 or profit_rate >= 0.20:
        return "仕入OK"
    return "NG"


# ── メイン処理 ────────────────────────────────────────
def fetch_all(table_id):
    """全レコードを取得（ページング対応）"""
    records = []
    offset = ""
    while True:
        params = f"offset={offset}" if offset else ""
        data = api_get(table_id, params)
        records.extend(data.get("records", []))
        offset = data.get("offset", "")
        if not offset:
            break
    return records

def run(target_coin_id=None):
    print("=" * 60)
    print("PROFIT CALCULATOR")
    print("=" * 60)

    # 1. コストルール取得 (field IDs: fldXWYnWE3p1Hhhm4=項目名, fldIpsIM8fTzRGbfe=値)
    cost_rules_raw = fetch_all(TBL_COST_RULES)
    cost_rules = {}
    for r in cost_rules_raw:
        f = r["fields"]
        name = f.get("fldXWYnWE3p1Hhhm4", "")
        val  = f.get("fldIpsIM8fTzRGbfe", "")
        cost_rules[name] = val

    yahoo_fee_rate = float(cost_rules.get("ヤフオク手数料", "10%").replace("%", "")) / 100
    domestic_shipping = int(cost_rules.get("国内送料", "1000円").replace("円", "").replace(",", ""))
    tariff_rate = float(cost_rules.get("関税率", "5%").replace("%", "")) / 100

    print(f"  ヤフオク手数料率: {yahoo_fee_rate:.0%}")
    print(f"  国内送料: ¥{domestic_shipping:,}")
    print(f"  関税率: {tariff_rate:.0%}")

    # 2. 全コインマスター取得
    coins = fetch_all(TBL_COIN_MASTER)
    print(f"  コイン総数: {len(coins)}")

    # 3. ヤフオク相場取得 (fldrZ2xV58vVRdtvn=coin_id, fld3twaf0Cu7WJGpc=販売価格)
    yahoo_records = fetch_all(TBL_YAHOO_SALES)
    yahoo_prices = {}
    for r in yahoo_records:
        f = r["fields"]
        cid = f.get("fldrZ2xV58vVRdtvn", "")
        price = f.get("fld3twaf0Cu7WJGpc", 0)
        if cid and price:
            yahoo_prices[cid] = max(yahoo_prices.get(cid, 0), price)

    # 4. 仕入履歴取得 (fldi5z5wbYCGBY0yK=coin_id, fld5AKwuijhn4eZcY=仕入価格, etc.)
    sourcing_records = fetch_all(TBL_SOURCING)
    sourcing_data = {}
    for r in sourcing_records:
        f = r["fields"]
        cid = f.get("fldi5z5wbYCGBY0yK", "")
        if cid:
            sourcing_data[cid] = {
                "仕入価格": f.get("fld5AKwuijhn4eZcY", 0),
                "送料": f.get("fldGiLcg6FFivVJ7u", 0),
                "関税": f.get("fldNfpkCVOxdZKnKN", 0),
                "合計": f.get("fldQeAmTJNS5oYfNY", 0),
            }

    # 5. 既存 profit_analysis 取得
    existing_pa = fetch_all(TBL_PROFIT_ANALYSIS)
    existing_map = {}
    for r in existing_pa:
        cid = r["fields"].get(FLD["coin_id"], "")
        if cid:
            existing_map[cid] = r["id"]

    # 6. 計算・書き込み
    results = []
    for coin in coins:
        f = coin["fields"]
        cid = f.get("fld0sDWG41KHJ99dT", "")
        if not cid:
            continue
        if target_coin_id and cid != target_coin_id:
            continue

        yahoo_price = yahoo_prices.get(cid, 0)
        src = sourcing_data.get(cid, {})

        if not yahoo_price:
            print(f"  SKIP {cid}: ヤフオク相場なし")
            continue

        # 計算
        purchase_price_usd = src.get("仕入価格", 0)
        # USD→JPY概算（仕入原価がJPYで記録されていればそのまま使う）
        total_sourcing = src.get("合計", 0)
        overseas_shipping_usd = src.get("送料", 0)
        tariff = src.get("関税", 0)

        # 仕入予想額 = 合計仕入原価を使う（既にJPY換算済み想定）
        purchase_est = total_sourcing if total_sourcing > 0 else purchase_price_usd * 150

        yahoo_fee = int(yahoo_price * yahoo_fee_rate)
        total_cost = int(purchase_est + domestic_shipping + yahoo_fee)
        profit = int(yahoo_price - total_cost)
        profit_rate = profit / yahoo_price if yahoo_price > 0 else 0

        rank = calc_rank(profit_rate)
        priority = PRIORITY_MAP[rank]
        comment = COMMENT_MAP[rank]
        judgment = calc_judgment(profit, profit_rate)

        record_fields = {
            FLD["coin_id"]: cid,
            FLD["想定ヤフオク売価"]: yahoo_price,
            FLD["仕入予想額"]: purchase_est,
            FLD["海外送料"]: overseas_shipping_usd,
            FLD["関税"]: tariff,
            FLD["国内送料"]: domestic_shipping,
            FLD["ヤフオク手数料"]: yahoo_fee,
            FLD["合計コスト"]: total_cost,
            FLD["想定利益"]: profit,
            FLD["利益率"]: round(profit_rate, 3),
            FLD["粗利ランク"]: rank,
            FLD["投資優先度"]: priority,
            FLD["AIコメント"]: comment,
            FLD["判定"]: judgment,
        }

        if cid in existing_map:
            api_patch(TBL_PROFIT_ANALYSIS, [{"id": existing_map[cid], "fields": record_fields}])
            action = "UPDATED"
        else:
            api_post(TBL_PROFIT_ANALYSIS, [{"fields": record_fields}])
            action = "CREATED"

        results.append({
            "coin_id": cid,
            "売価": yahoo_price,
            "合計コスト": total_cost,
            "想定利益": profit,
            "利益率": f"{profit_rate:.1%}",
            "ランク": rank,
            "優先度": priority,
            "判定": judgment,
            "action": action,
        })

    # 7. 結果表示
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    for r in sorted(results, key=lambda x: -float(x["利益率"].replace("%", ""))):
        print(f"  {r['coin_id']}: 売価¥{r['売価']:,} / コスト¥{r['合計コスト']:,} / "
              f"利益¥{r['想定利益']:,} / {r['利益率']} / {r['ランク']}{r['優先度']} / {r['判定']} [{r['action']}]")

    print()
    print(f"処理完了: {len(results)}件")
    return results


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
