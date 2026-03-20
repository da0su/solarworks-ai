"""eBay Terapeak (Product Research) 半自動取得 → Supabase投入

Seller Hub > Product Research の検索結果をPlaywright経由で取得し、
market_transactionsへ差分upsertする。

使い方:
    python run.py update-ebay                        # 本番実行（全クエリ）
    python run.py update-ebay --dry-run              # ドライラン
    python run.py update-ebay --queries "NGC sovereign"  # 特定クエリのみ
    python run.py update-ebay --csv data/ebay/manual_export.csv  # CSV手動投入

運用フロー:
    1. CEOがeBay seller accountにログイン済みのブラウザを用意
    2. COOがこのスクリプトを実行
    3. Terapeak Product Researchを自動操作して検索結果を取得
    4. 取得データをSupabaseへ投入

技術仕様:
    - Terapeak: eBay Seller Hub内のProduct Research
    - 3年分のsold履歴（Best Offer実成約価格含む）
    - 1日250クエリ上限
    - CSV出力機能なし → DOM抽出 or 手動CSV
    - 通貨: USD（取得時にJPY換算）
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client, make_dedup_key

# ============================================================
# 設定
# ============================================================

SOURCE = "ebay"
BATCH_SIZE = 500

# 為替レート（仮。将来的にはAPIで取得）
DEFAULT_USD_JPY_RATE = 150.0

# 検索クエリテンプレート
DEFAULT_QUERIES = [
    {"keyword": "NGC coins", "category": "Coins & Paper Money", "label": "NGC全般"},
    {"keyword": "PCGS coins", "category": "Coins & Paper Money", "label": "PCGS全般"},
    {"keyword": "NGC sovereign gold", "category": "Coins:World", "label": "NGC ソブリン"},
    {"keyword": "NGC britannia silver", "category": "Coins:World", "label": "NGC ブリタニア"},
    {"keyword": "PCGS panda silver", "category": "Coins:World", "label": "PCGS パンダ"},
    {"keyword": "NGC una lion", "category": "Coins:World", "label": "NGC ウナとライオン"},
    {"keyword": "PCGS morgan dollar", "category": "Coins:US", "label": "PCGS モルガン"},
    {"keyword": "NGC napoleon gold", "category": "Coins:World", "label": "NGC ナポレオン"},
]

# ログ保存先
LOG_DIR = PROJECT_ROOT / "data" / "ebay"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# タイトルパーサー
# ============================================================

def _load_parser():
    from scripts.import_yahoo_history import parse_title
    return parse_title

parse_title = _load_parser()


# ============================================================
# CSV手動投入モード
# ============================================================

def import_from_csv(csv_path: str, dry_run: bool = False) -> tuple[int, int]:
    """手動エクスポートCSVからSupabaseへ投入

    想定CSVフォーマット:
        title, price_usd, sold_date, url, seller, category
    """
    import csv

    records = []
    for enc in ("utf-8", "utf-8-sig", "cp932"):
        try:
            with open(csv_path, "r", encoding=enc) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = convert_csv_row(row)
                    if rec:
                        records.append(rec)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if not records:
        print(f"ERROR: {csv_path} からレコードを読み込めません")
        return 0, 0

    # 重複除去
    seen = set()
    unique = []
    for r in records:
        dk = r["dedup_key"]
        if dk not in seen:
            seen.add(dk)
            unique.append(r)

    print(f"CSV読込: {len(records)}件 → 重複除去後: {len(unique)}件")
    return upload_records(unique, dry_run=dry_run)


def convert_csv_row(row: dict) -> dict | None:
    """CSV行 → Supabaseレコード"""
    title = (row.get("title") or "").strip()
    if not title:
        return None

    price_str = row.get("price_usd") or row.get("price") or ""
    price_str = re.sub(r'[^\d.]', '', str(price_str))
    if not price_str:
        return None

    try:
        price_usd = float(price_str)
    except ValueError:
        return None

    record = {
        "source": SOURCE,
        "currency": "USD",
        "title": title,
        "price": int(price_usd * 100) / 100,  # 小数2桁
        "price_jpy": int(price_usd * DEFAULT_USD_JPY_RATE),
    }

    sold_date = (row.get("sold_date") or "").strip()
    if sold_date:
        record["sold_date"] = sold_date[:10]

    url = (row.get("url") or "").strip()
    if url:
        record["url"] = url

    seller = (row.get("seller") or "").strip()
    if seller:
        record["seller_name"] = seller

    # raw_data
    raw = {"fetch_source": "csv_manual"}
    category = (row.get("category") or "").strip()
    if category:
        raw["category"] = category
    record["raw_data"] = raw

    # タイトルパーサー
    parsed = parse_title(title)
    for key in ("grader", "grade", "country", "year", "material",
                "denomination", "series", "tags"):
        if key in parsed:
            record[key] = parsed[key]

    record["dedup_key"] = make_dedup_key(
        SOURCE,
        url=record.get("url"),
        title=title,
        price=int(price_usd),
        sold_date=record.get("sold_date", ""),
    )

    return record


# ============================================================
# Supabase投入
# ============================================================

def upload_records(records: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """Supabaseへバッチupsert"""
    total = len(records)
    success = 0
    failed = 0

    if dry_run:
        print(f"  [DRY-RUN] {total}件（実際の投入はスキップ）")
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
            print(f"  Batch {batch_num}/{total_batches}: {inserted}件OK")
        except Exception as e:
            error_str = str(e)
            if "duplicate" in error_str.lower() or "conflict" in error_str.lower():
                success += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: {len(batch)}件（重複更新）")
            else:
                failed += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: ERROR - {error_str[:300]}")

    return success, failed


# ============================================================
# 実行ログ
# ============================================================

def save_execution_log(log_data: dict):
    """実行ログをJSONで保存"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"fetch_log_{timestamp}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    print(f"  実行ログ保存: {log_path}")
    return log_path


# ============================================================
# メイン
# ============================================================

def main():
    args = sys.argv[1:]
    args = [a for a in args if a not in ("update-ebay",)]

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    # --csv モード（手動CSV投入）
    csv_path = None
    i = 0
    while i < len(args):
        if args[i] == "--csv" and i + 1 < len(args):
            csv_path = args[i + 1]
            i += 2
        else:
            i += 1

    start_time = datetime.now()

    if csv_path:
        # CSV手動投入モード
        print(f"{'=' * 60}")
        print(f"  eBay手動CSV投入モード")
        print(f"{'=' * 60}")
        print(f"  ファイル: {csv_path}")
        print(f"  モード:   {'DRY-RUN' if dry_run else '本番投入'}")
        print()

        success, failed = import_from_csv(csv_path, dry_run=dry_run)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print()
        print(f"{'=' * 60}")
        print(f"  実行結果: 成功{success}件 / 失敗{failed}件")
        print(f"  所要時間: {duration:.0f}秒")
        print(f"{'=' * 60}")

        log_data = {
            "execution_time": start_time.isoformat(),
            "duration_seconds": duration,
            "mode": "csv_manual",
            "dry_run": dry_run,
            "executor": "COO",
            "csv_path": csv_path,
            "upload_success": success,
            "upload_failed": failed,
        }
        save_execution_log(log_data)

    else:
        # Terapeak半自動モード（将来実装）
        print(f"{'=' * 60}")
        print(f"  eBay Terapeak 更新")
        print(f"{'=' * 60}")
        print()
        print("  現在利用可能なモード:")
        print("    python run.py update-ebay --csv <path>  # CSV手動投入")
        print()
        print("  Terapeak自動取得モードは準備中です。")
        print("  現在の推奨フロー:")
        print("    1. eBay Seller Hub > Product Research にアクセス")
        print("    2. 検索実行 → 結果をCSVに転記")
        print("    3. python run.py update-ebay --csv <path> で投入")
        print()
        print("  検索クエリテンプレート:")
        for q in DEFAULT_QUERIES:
            print(f"    [{q['label']}] {q['keyword']} (in {q['category']})")
        print()

        log_data = {
            "execution_time": start_time.isoformat(),
            "mode": "template_display",
            "executor": "COO",
            "search_template": DEFAULT_QUERIES,
        }
        save_execution_log(log_data)


if __name__ == "__main__":
    main()
