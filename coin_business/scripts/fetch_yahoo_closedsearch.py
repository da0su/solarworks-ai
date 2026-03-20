"""ヤフオク closedsearch 自動取得 → Supabase投入

Yahoo closedsearch（落札相場）から過去120日分のNGC/PCGS鑑定コインを取得し、
market_transactionsへ差分upsertする。

使い方:
    python run.py update-yahoo                    # 本番実行
    python run.py update-yahoo --dry-run          # ドライラン
    python run.py update-yahoo --queries NGC      # 特定クエリのみ
    python run.py update-yahoo --limit 100        # ページ数制限

技術仕様:
    - URL: https://auctions.yahoo.co.jp/closedsearch/closedsearch
    - ログイン不要（公開ページ）
    - robots.txt で /closedsearch/closedsearch は許可
    - 100件/ページ、3-5秒間隔
    - 過去120日分のsold履歴
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client, make_dedup_key

# ============================================================
# 設定
# ============================================================

SOURCE = "yahoo"
BATCH_SIZE = 500
BASE_URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"
RESULTS_PER_PAGE = 100
REQUEST_INTERVAL = (3.0, 5.0)  # 秒（ランダム範囲）
MAX_PAGES_DEFAULT = 200  # 安全上限（100件×200ページ=20,000件）

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 検索クエリテンプレート（カテゴリ起点 + NGC/PCGS）
DEFAULT_QUERIES = [
    {"keyword": "NGC", "label": "NGC全般"},
    {"keyword": "PCGS", "label": "PCGS全般"},
]

# ログ保存先
LOG_DIR = PROJECT_ROOT / "data" / "yahoo_closedsearch"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# タイトルパーサー（import_yahoo_history.py と共通ロジック）
# ============================================================

def _load_parser():
    """import_yahoo_history.pyのparse_titleを再利用"""
    from scripts.import_yahoo_history import parse_title
    return parse_title

parse_title = _load_parser()


# ============================================================
# closedsearch スクレイパー
# ============================================================

def _random_interval() -> float:
    """人間的なランダム間隔"""
    import random
    return random.uniform(*REQUEST_INTERVAL)


def fetch_page(keyword: str, offset: int = 1, session: requests.Session = None) -> dict:
    """1ページ分のclosedsearch結果を取得（__NEXT_DATA__ JSON抽出方式）

    Yahoo closedsearch はNext.jsベースのSSRで、商品データは
    <script>タグ内のJSON（props.pageProps.initialState.search.items.listing）に格納されている。
    """
    params = {
        "p": keyword,
        "n": str(RESULTS_PER_PAGE),
        "b": str(offset),
    }

    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    try:
        resp = s.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"items": [], "has_next": False, "total": 0, "error": str(e)}

    html = resp.text

    # __NEXT_DATA__ JSON を抽出
    match = re.search(
        r'<script[^>]*>\s*(\{"props":\{"pageProps".*?\})\s*</script>',
        html, re.DOTALL
    )
    if not match:
        return {"items": [], "has_next": False, "total": 0,
                "error": "JSON data not found in page"}

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        return {"items": [], "has_next": False, "total": 0,
                "error": f"JSON parse error: {e}"}

    listing = (data.get("props", {}).get("pageProps", {})
               .get("initialState", {}).get("search", {})
               .get("items", {}).get("listing", {}))

    raw_items = listing.get("items", [])
    total = listing.get("totalResultsAvailable", 0)

    # JSON項目 → 中間辞書に変換
    items = []
    for raw in raw_items:
        item = {}

        item["title"] = raw.get("title", "").strip()
        item["price"] = raw.get("price")
        item["auctionId"] = raw.get("auctionId", "")

        # URL構築
        if item["auctionId"]:
            item["url"] = f"https://page.auctions.yahoo.co.jp/jp/auction/{item['auctionId']}"

        # 入札数
        item["bid_count"] = raw.get("bidCount")

        # 終了日時（ISO 8601: 2026-03-19T13:09:02+09:00）
        end_time = raw.get("endTime", "")
        if end_time:
            item["end_date_raw"] = end_time
            item["sold_date"] = end_time[:10]  # YYYY-MM-DD

        # 画像URL
        item["thumbnail"] = raw.get("imageUrl", "")

        # seller情報
        seller = raw.get("seller", {})
        if seller:
            item["seller_id"] = seller.get("userId", "")
            item["seller_name"] = seller.get("displayName", "")

        # カテゴリ
        cat = raw.get("category", {})
        if cat:
            item["category_id"] = cat.get("id")
            item["category_name"] = cat.get("name", "")

        if item.get("title") and item.get("price"):
            items.append(item)

    # 次ページ判定（offset + 件数 < total）
    has_next = (offset + len(items)) < total

    return {"items": items, "has_next": has_next, "total": total, "error": None}


def fetch_all_pages(keyword: str, max_pages: int = MAX_PAGES_DEFAULT,
                    session: requests.Session = None) -> list[dict]:
    """全ページを取得"""
    all_items = []
    page = 1
    offset = 1
    total_available = 0

    while page <= max_pages:
        result = fetch_page(keyword, offset=offset, session=session)

        if result["error"]:
            print(f"    ERROR page {page}: {result['error']}")
            break

        items = result["items"]
        all_items.extend(items)

        if page == 1:
            total_available = result.get("total", 0)
            total_pages = (total_available + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE
            print(f"    総件数: {total_available:,}件 (全{total_pages}ページ)")

        if page % 10 == 0 or not result["has_next"]:
            print(f"    page {page}: +{len(items)}件 (累計 {len(all_items):,}件)")

        if not result["has_next"] or len(items) == 0:
            break

        page += 1
        offset += RESULTS_PER_PAGE
        time.sleep(_random_interval())

    return all_items


# ============================================================
# レコード変換
# ============================================================

def convert_to_record(item: dict, keyword: str) -> dict | None:
    """scrape結果 → Supabaseレコード"""
    title = item.get("title", "").strip()
    price = item.get("price")
    sold_date = item.get("sold_date")

    if not title or not price:
        return None

    record = {
        "source": SOURCE,
        "currency": "JPY",
        "title": title,
        "price": price,
        "price_jpy": price,
    }

    if sold_date:
        record["sold_date"] = sold_date

    url = item.get("url")
    if url:
        record["url"] = url

    # 出品者
    seller_name = item.get("seller_id") or item.get("seller_name")
    if seller_name:
        record["seller_name"] = seller_name

    # raw_data
    raw = {}
    if item.get("bid_count") is not None:
        raw["bid_count"] = item["bid_count"]
    if item.get("thumbnail"):
        raw["thumbnail_url"] = item["thumbnail"]
    if item.get("end_date_raw"):
        raw["end_date_raw"] = item["end_date_raw"]
    if item.get("auctionId"):
        raw["item_id"] = item["auctionId"]
    if item.get("category_name"):
        raw["category"] = item["category_name"]
    raw["fetch_keyword"] = keyword
    raw["fetch_source"] = "closedsearch"
    if raw:
        record["raw_data"] = raw

    # タイトルパーサーでメタデータ抽出
    parsed = parse_title(title)
    for key in ("grader", "grade", "country", "year", "material",
                "denomination", "series", "tags"):
        if key in parsed:
            record[key] = parsed[key]

    # grader補完（検索キーワードから）
    if "grader" not in record:
        kw_upper = keyword.upper()
        if "NGC" in kw_upper:
            record["grader"] = "NGC"
        elif "PCGS" in kw_upper:
            record["grader"] = "PCGS"

    # dedup_key
    record["dedup_key"] = make_dedup_key(
        SOURCE,
        url=record.get("url"),
        title=title,
        price=price,
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
    args = [a for a in args if a not in ("update-yahoo",)]

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    # --queries フィルタ
    query_filter = None
    max_pages = MAX_PAGES_DEFAULT
    i = 0
    while i < len(args):
        if args[i] == "--queries" and i + 1 < len(args):
            query_filter = args[i + 1].split(",")
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            max_pages = int(args[i + 1])
            i += 2
        else:
            i += 1

    # 検索クエリ選定
    queries = DEFAULT_QUERIES
    if query_filter:
        queries = [q for q in queries if q["keyword"] in query_filter]
        if not queries:
            print(f"ERROR: 指定クエリが見つかりません: {query_filter}")
            sys.exit(1)

    start_time = datetime.now()
    print(f"{'=' * 60}")
    print(f"  ヤフオク closedsearch 差分更新")
    print(f"{'=' * 60}")
    print(f"  実行日時: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  モード:   {'DRY-RUN' if dry_run else '本番投入'}")
    print(f"  クエリ数: {len(queries)}本")
    print(f"  最大ページ: {max_pages}/クエリ")
    print()

    session = requests.Session()
    all_records = []
    query_results = []

    for qi, query in enumerate(queries, 1):
        keyword = query["keyword"]
        label = query["label"]
        print(f"[{qi}/{len(queries)}] '{keyword}' ({label})")

        raw_items = fetch_all_pages(keyword, max_pages=max_pages, session=session)
        print(f"    取得完了: {len(raw_items)}件")

        # レコード変換
        records = []
        skip_count = 0
        for item in raw_items:
            rec = convert_to_record(item, keyword)
            if rec:
                records.append(rec)
            else:
                skip_count += 1

        print(f"    変換成功: {len(records)}件 / スキップ: {skip_count}件")

        query_results.append({
            "keyword": keyword,
            "label": label,
            "raw_count": len(raw_items),
            "converted_count": len(records),
            "skipped": skip_count,
        })

        all_records.extend(records)
        print()

        # クエリ間のインターバル
        if qi < len(queries):
            time.sleep(_random_interval())

    # ファイル内重複除去
    seen = set()
    unique_records = []
    dup_count = 0
    for r in all_records:
        dk = r["dedup_key"]
        if dk in seen:
            dup_count += 1
        else:
            seen.add(dk)
            unique_records.append(r)

    print(f"全クエリ合計: {len(all_records)}件 → 重複除去後: {len(unique_records)}件 (重複{dup_count}件)")
    print()

    # Supabase投入
    print(f"Supabaseへ投入中... ({len(unique_records):,}件)")
    success, failed = upload_records(unique_records, dry_run=dry_run)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    # サマリー
    print()
    print(f"{'=' * 60}")
    print(f"  実行結果サマリー")
    print(f"{'=' * 60}")
    print(f"  実行日時:   {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  所要時間:   {duration:.0f}秒")
    print(f"  クエリ数:   {len(queries)}本")
    print(f"  取得合計:   {sum(q['raw_count'] for q in query_results):,}件")
    print(f"  変換成功:   {len(unique_records):,}件")
    print(f"  ファイル内重複: {dup_count}件")
    print(f"  投入成功:   {success:,}件")
    print(f"  投入失敗:   {failed}件")
    print(f"{'=' * 60}")

    # 実行ログ保存
    log_data = {
        "execution_time": start_time.isoformat(),
        "duration_seconds": duration,
        "mode": "dry-run" if dry_run else "production",
        "executor": "COO-auto",
        "queries": query_results,
        "total_raw": sum(q["raw_count"] for q in query_results),
        "total_converted": len(unique_records),
        "duplicates_removed": dup_count,
        "upload_success": success,
        "upload_failed": failed,
        "search_template": [
            {"keyword": q["keyword"], "label": q["label"]}
            for q in DEFAULT_QUERIES
        ],
    }
    save_execution_log(log_data)

    return success, failed


if __name__ == "__main__":
    main()
