"""
ranking_report.py
─────────────────
profit_analysis + coin_master + sourcing_history を結合し、
ランキング・フィルタ付きレポートを生成する。

使い方:
    python scripts/ranking_report.py                    # 全件・利益率順
    python scripts/ranking_report.py --sort profit      # 想定利益額順
    python scripts/ranking_report.py --rank S,A,B       # S/A/Bランクのみ
    python scripts/ranking_report.py --site eBay        # 仕入元フィルタ
    python scripts/ranking_report.py --tag PF70         # 特徴タグフィルタ
    python scripts/ranking_report.py --csv              # CSV出力
    python scripts/ranking_report.py --md               # Markdownレポート出力
    python scripts/ranking_report.py --ceo              # CEO向け優先度別レポート
"""
import json, os, sys, csv, urllib.request
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

# テーブル/フィールドID
TBL_PA = "tbl8vTCmzelckdEsM"
TBL_CM = "tblFLv5Cq9zpOmTc6"
TBL_SR = "tblrWMUHlBIn9WWce"

PA_F = {
    "coin_id": "fld3mPeBTzWuPPLJC",
    "yahoo_price": "fldV1IkQmL1nnWpzE",
    "purchase_est": "fldWIEXPnswA9wWXM",
    "overseas_ship": "fldO2hkVVG0ferq6v",
    "tariff": "fldrshNcU8FnE1on7",
    "domestic_ship": "fldKNc3mZittxj7wS",
    "yahoo_fee": "fldl3gAZF3IBuQr7I",
    "total_cost": "fldtnyHufkfzpWD2W",
    "profit": "fldOuiXznY1wLkTH1",
    "profit_rate": "fldZksUbeKWy0yxGK",
    "rank": "fldbVUncJjQLrO10g",
    "priority": "fldubs9C47dTwJkUp",
    "comment": "fldTcC1DfczdDYOHH",
    "judgment": "fldgA1aAkAZ6LsWHS",
}
CM_F = {
    "coin_id": "fld0sDWG41KHJ99dT",
    "country": "fldUTtgUn5f1xvQ3q",
    "year": "fldiRk8ituRjLe823",
    "denom": "fldOsGYYzfvC6TLhw",
    "grader": "fldtwinYCT6PKXSCV",
    "grade": "fldYbOvd5gy0esKf6",
    "tags": "fldxgehQqeTi4KOmd",
}
SR_F = {
    "coin_id": "fldi5z5wbYCGBY0yK",
    "site": "fldgQNcdtlbEr7z8l",
}


def api_get(table_id, params=""):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}?returnFieldsByFieldId=true&{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
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


def build_ranking():
    """全データを結合してランキング用リストを構築"""
    # profit_analysis
    pa_records = fetch_all(TBL_PA)
    # coin_master
    cm_records = fetch_all(TBL_CM)
    cm_map = {}
    for r in cm_records:
        f = r["fields"]
        cid = f.get(CM_F["coin_id"], "")
        if cid:
            cm_map[cid] = {
                "country": f.get(CM_F["country"], ""),
                "year": f.get(CM_F["year"], ""),
                "denom": f.get(CM_F["denom"], ""),
                "grader": f.get(CM_F["grader"], ""),
                "grade": f.get(CM_F["grade"], ""),
                "tags": f.get(CM_F["tags"], []),
            }
    # sourcing_history
    sr_records = fetch_all(TBL_SR)
    sr_map = {}
    for r in sr_records:
        f = r["fields"]
        cid = f.get(SR_F["coin_id"], "")
        if cid:
            sr_map[cid] = f.get(SR_F["site"], "不明")

    # 結合
    items = []
    seen = set()
    for r in pa_records:
        f = r["fields"]
        cid = f.get(PA_F["coin_id"], "")
        if not cid or cid in seen:
            continue
        seen.add(cid)

        cm = cm_map.get(cid, {})
        coin_name = f"{cm.get('year','')} {cm.get('country','')} {cm.get('denom','')} {cm.get('grader','')} {cm.get('grade','')}"

        profit_rate_raw = f.get(PA_F["profit_rate"], 0)
        profit_rate = profit_rate_raw if isinstance(profit_rate_raw, (int, float)) else 0

        items.append({
            "coin_id": cid,
            "coin_name": coin_name.strip(),
            "site": sr_map.get(cid, "不明"),
            "yahoo_price": f.get(PA_F["yahoo_price"], 0),
            "total_cost": f.get(PA_F["total_cost"], 0),
            "profit": f.get(PA_F["profit"], 0),
            "profit_rate": profit_rate,
            "rank": f.get(PA_F["rank"], ""),
            "priority": f.get(PA_F["priority"], ""),
            "comment": f.get(PA_F["comment"], ""),
            "judgment": f.get(PA_F["judgment"], ""),
            "tags": cm.get("tags", []),
        })
    return items


def apply_filters(items, args):
    """フィルタ適用"""
    # ランクフィルタ
    if args.get("rank"):
        allowed = [r.strip().upper() for r in args["rank"].split(",")]
        items = [i for i in items if i["rank"] in allowed]

    # 仕入元フィルタ
    if args.get("site"):
        site = args["site"].lower()
        items = [i for i in items if site in i["site"].lower()]

    # タグフィルタ
    if args.get("tag"):
        tag = args["tag"]
        items = [i for i in items if tag in (i.get("tags") or [])]

    return items


def sort_items(items, sort_key="rate"):
    """ソート"""
    if sort_key == "profit":
        return sorted(items, key=lambda x: -x["profit"])
    return sorted(items, key=lambda x: -x["profit_rate"])


def print_table(items):
    """コンソール表示"""
    rank_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
    print()
    print("=" * 120)
    print(f"{'#':>2} {'coin_id':<10} {'コイン名':<40} {'仕入元':<15} {'売価':>10} {'原価':>10} {'利益':>10} {'利益率':>7} {'ランク':>4} {'優先度':<8} {'コメント'}")
    print("-" * 120)
    for i, item in enumerate(items, 1):
        rate_str = f"{item['profit_rate']:.1%}" if isinstance(item['profit_rate'], float) else str(item['profit_rate'])
        name = item['coin_name'][:38]
        print(f"{i:>2} {item['coin_id']:<10} {name:<40} {item['site']:<15} {item['yahoo_price']:>10,} {item['total_cost']:>10,} {item['profit']:>10,} {rate_str:>7} {item['rank']:>4} {item['priority']:<8} {item['comment']}")
    print("=" * 120)
    print(f"合計: {len(items)}件")


def export_csv(items):
    """CSV出力"""
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"{today}_ranking.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "coin_id", "コイン名", "仕入元", "想定売価", "総原価", "想定利益", "利益率", "ランク", "優先度", "AIコメント", "判定"])
        for i, item in enumerate(items, 1):
            rate_str = f"{item['profit_rate']:.1%}" if isinstance(item['profit_rate'], float) else str(item['profit_rate'])
            writer.writerow([i, item["coin_id"], item["coin_name"], item["site"], item["yahoo_price"], item["total_cost"], item["profit"], rate_str, item["rank"], item["priority"], item["comment"], item["judgment"]])
    print(f"\nCSV出力: {path}")
    return path


def export_markdown(items):
    """Markdownレポート出力"""
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"{today}_ranking.md"
    lines = []
    lines.append(f"# コインリサーチ 差益ランキングレポート")
    lines.append(f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**対象件数**: {len(items)}件")
    lines.append("")
    lines.append("| # | coin_id | コイン名 | 仕入元 | 売価 | 原価 | 利益 | 利益率 | ランク | 優先度 | コメント |")
    lines.append("|---|---------|---------|--------|------|------|------|--------|--------|--------|---------|")
    for i, item in enumerate(items, 1):
        rate_str = f"{item['profit_rate']:.1%}" if isinstance(item['profit_rate'], float) else str(item['profit_rate'])
        lines.append(f"| {i} | {item['coin_id']} | {item['coin_name'][:30]} | {item['site']} | {item['yahoo_price']:,} | {item['total_cost']:,} | {item['profit']:,} | {rate_str} | {item['rank']} | {item['priority']} | {item['comment']} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nMarkdown出力: {path}")
    return path


def export_ceo_report(items):
    """CEO向け優先度別レポート"""
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"{today}_ceo_report.md"

    priority_groups = {
        "S": {"label": "最優先候補", "items": []},
        "A": {"label": "優先候補", "items": []},
        "B": {"label": "通常候補", "items": []},
        "C": {"label": "条件付き候補", "items": []},
        "D": {"label": "見送り寄り", "items": []},
        "E": {"label": "見送り候補", "items": []},
    }
    for item in items:
        rank = item.get("rank", "E")
        if rank in priority_groups:
            priority_groups[rank]["items"].append(item)

    lines = []
    lines.append(f"# CEO向け 仕入候補レポート")
    lines.append(f"**日付**: {today}")
    lines.append(f"**総候補数**: {len(items)}件")
    lines.append("")

    # サマリー
    lines.append("## サマリー")
    for rank in ["S", "A", "B", "C", "D", "E"]:
        g = priority_groups[rank]
        count = len(g["items"])
        if count > 0:
            lines.append(f"- **{rank}ランク（{g['label']}）**: {count}件")
    lines.append("")

    # 各ランク詳細
    for rank in ["S", "A", "B", "C", "D", "E"]:
        g = priority_groups[rank]
        if not g["items"]:
            continue
        lines.append(f"## {rank}ランク: {g['label']}")
        lines.append("")
        for item in sorted(g["items"], key=lambda x: -x["profit_rate"]):
            rate_str = f"{item['profit_rate']:.1%}"
            lines.append(f"### {item['coin_id']}: {item['coin_name'][:40]}")
            lines.append(f"- 仕入元: {item['site']}")
            lines.append(f"- 想定売価: {item['yahoo_price']:,}円 / 総原価: {item['total_cost']:,}円")
            lines.append(f"- **想定利益: {item['profit']:,}円（利益率 {rate_str}）**")
            lines.append(f"- {item['comment']}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nCEOレポート出力: {path}")
    return path


def parse_args():
    args = {}
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--sort" and i + 1 < len(sys.argv):
            args["sort"] = sys.argv[i + 1]
            i += 2
        elif a == "--rank" and i + 1 < len(sys.argv):
            args["rank"] = sys.argv[i + 1]
            i += 2
        elif a == "--site" and i + 1 < len(sys.argv):
            args["site"] = sys.argv[i + 1]
            i += 2
        elif a == "--tag" and i + 1 < len(sys.argv):
            args["tag"] = sys.argv[i + 1]
            i += 2
        elif a == "--csv":
            args["csv"] = True
            i += 1
        elif a == "--md":
            args["md"] = True
            i += 1
        elif a == "--ceo":
            args["ceo"] = True
            i += 1
        else:
            i += 1
    return args


if __name__ == "__main__":
    args = parse_args()

    print("データ取得中...")
    items = build_ranking()

    # フィルタ
    items = apply_filters(items, args)

    # ソート
    sort_key = args.get("sort", "rate")
    items = sort_items(items, sort_key)

    # 表示
    print_table(items)

    # 出力
    if args.get("csv"):
        export_csv(items)
    if args.get("md"):
        export_markdown(items)
    if args.get("ceo"):
        export_ceo_report(items)

    # デフォルトでceo+csv+md全部出力
    if not any(args.get(k) for k in ["csv", "md", "ceo"]):
        export_csv(items)
        export_markdown(items)
        export_ceo_report(items)
