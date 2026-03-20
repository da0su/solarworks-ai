"""eBay JSON data → Supabase投入

ブラウザで収集したeBay soldデータ（JSON）をパースしてSupabaseへupsert。

使い方:
    python scripts/import_ebay_json.py data/ebay/ebay_sold_ngc_pcgs_20260319.json
    python scripts/import_ebay_json.py data/ebay/ebay_sold_ngc_pcgs_20260319.json --dry-run
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client, make_dedup_key

SOURCE = "ebay"
BATCH_SIZE = 500
USD_JPY_RATE = 150.0

MONTH_MAP = {
    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
    'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
    'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
}


def _load_parser():
    from scripts.import_yahoo_history import parse_title
    return parse_title

parse_title = _load_parser()


def parse_date(date_str):
    """'Mar 18, 2026' → '2026-03-18'"""
    if not date_str:
        return None
    m = re.match(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d+),?\s+(\d{4})', date_str)
    if m:
        return f"{m.group(3)}-{MONTH_MAP[m.group(1)]}-{int(m.group(2)):02d}"
    return None


def convert_item(item):
    """ブラウザ抽出JSON → Supabaseレコード"""
    title = (item.get("t") or "").strip()
    price_usd = item.get("p", 0)
    if not title or not price_usd or price_usd < 5:
        return None

    price_jpy = int(price_usd * USD_JPY_RATE)

    record = {
        "source": SOURCE,
        "currency": "USD",
        "title": title,
        "price": int(price_usd),
        "price_jpy": price_jpy,
    }

    sold_date = parse_date(item.get("d"))
    if sold_date:
        record["sold_date"] = sold_date

    item_id = item.get("id")
    if item_id:
        record["url"] = f"https://www.ebay.com/itm/{item_id}"

    # raw_data
    raw = {"fetch_source": "ebay_completed"}
    if item.get("b"):
        raw["bid_count"] = item["b"]
    if item.get("bt"):
        raw["buy_type"] = item["bt"]
    if item.get("id"):
        raw["item_id"] = item["id"]
    if item.get("src"):
        raw["search_keyword"] = item["src"] + " coins"
    record["raw_data"] = raw

    # タイトルパーサー
    parsed = parse_title(title)
    for key in ("grader", "grade", "country", "year", "material",
                "denomination", "series", "tags"):
        if key in parsed:
            record[key] = parsed[key]

    # grader補完
    if "grader" not in record:
        src = item.get("src", "")
        if "NGC" in src:
            record["grader"] = "NGC"
        elif "PCGS" in src:
            record["grader"] = "PCGS"

    record["dedup_key"] = make_dedup_key(
        SOURCE,
        url=record.get("url"),
        title=title,
        price=price_jpy,
        sold_date=record.get("sold_date", ""),
    )

    return record


def upload_records(records, dry_run=False):
    total = len(records)
    success = 0
    failed = 0

    if dry_run:
        print(f"  [DRY-RUN] {total}件")
        return total, 0

    client = get_client()

    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        try:
            resp = client.table("market_transactions").upsert(
                batch, on_conflict="dedup_key"
            ).execute()
            inserted = len(resp.data)
            success += inserted
            if batch_num % 10 == 0 or batch_num == total_batches:
                print(f"  Batch {batch_num}/{total_batches}: {success:,}件完了")
        except Exception as e:
            error_str = str(e)
            if "duplicate" in error_str.lower() or "conflict" in error_str.lower():
                success += len(batch)
            else:
                failed += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: ERROR - {error_str[:200]}")

    return success, failed


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if not args:
        print("Usage: python scripts/import_ebay_json.py <json_file> [--dry-run]")
        sys.exit(1)

    json_path = args[0]
    print(f"eBay JSON import: {json_path}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'PRODUCTION'}")
    print()

    with open(json_path, "r", encoding="utf-8") as f:
        raw_items = json.load(f)

    print(f"JSON読込: {len(raw_items):,}件")

    # Convert
    records = []
    skip = 0
    for item in raw_items:
        rec = convert_item(item)
        if rec:
            records.append(rec)
        else:
            skip += 1

    print(f"変換成功: {len(records):,}件 / スキップ: {skip}件")

    # Dedup
    seen = set()
    unique = []
    dup = 0
    for r in records:
        dk = r["dedup_key"]
        if dk in seen:
            dup += 1
        else:
            seen.add(dk)
            unique.append(r)

    print(f"重複除去: {dup}件 → ユニーク: {len(unique):,}件")
    print()

    # Date range
    dates = [r["sold_date"] for r in unique if r.get("sold_date")]
    dates.sort()
    if dates:
        print(f"日付範囲: {dates[0]} ~ {dates[-1]}")

    # Grader split
    ngc = sum(1 for r in unique if r.get("grader") == "NGC")
    pcgs = sum(1 for r in unique if r.get("grader") == "PCGS")
    print(f"NGC: {ngc:,}件 / PCGS: {pcgs:,}件")
    print()

    # Upload
    print(f"Supabaseへ投入中... ({len(unique):,}件)")
    start = datetime.now()
    success, failed = upload_records(unique, dry_run=dry_run)
    duration = (datetime.now() - start).total_seconds()

    print()
    print(f"{'=' * 50}")
    print(f"  投入完了: {success:,}件成功 / {failed}件失敗")
    print(f"  所要時間: {duration:.0f}秒")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
