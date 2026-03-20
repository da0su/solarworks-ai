"""eBay Completed Listings 自動取得 → Supabase投入

eBay completed/sold listings検索から直近90日分のNGC/PCGSコインを取得し、
market_transactionsへ差分upsertする。

使い方:
    python run.py update-ebay                     # 本番実行（全クエリ）
    python run.py update-ebay --dry-run            # ドライラン
    python run.py update-ebay --queries "NGC coins"  # 特定クエリのみ
    python run.py update-ebay --limit 5            # ページ数制限

技術仕様:
    - URL: https://www.ebay.com/sch/i.html (completed+sold listings)
    - ログイン不要（公開ページ）
    - 240件/ページ、3-5秒間隔
    - 直近90日分のsold履歴
    - 価格はJPY表示（eBayが自動変換、ブラウザロケールに依存）
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client, make_dedup_key

# ============================================================
# 設定
# ============================================================

SOURCE = "ebay"
BATCH_SIZE = 500
BASE_URL = "https://www.ebay.com/sch/i.html"
RESULTS_PER_PAGE = 240
REQUEST_INTERVAL = (3.0, 5.0)
MAX_PAGES_DEFAULT = 50

# USD/JPY概算（eBayはJPY表示だがUSD価格も取れる場合あり）
USD_JPY_RATE = 150.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 検索クエリテンプレート（CEO指示: NGC/PCGS必須、広く取得）
DEFAULT_QUERIES = [
    {"keyword": "NGC coins", "label": "NGC全般"},
    {"keyword": "PCGS coins", "label": "PCGS全般"},
    {"keyword": "NGC sovereign gold", "label": "NGC ソブリン金貨"},
    {"keyword": "NGC britannia silver", "label": "NGC ブリタニア銀貨"},
    {"keyword": "PCGS panda silver", "label": "PCGS パンダ銀貨"},
    {"keyword": "NGC una lion", "label": "NGC ウナとライオン"},
    {"keyword": "PCGS morgan dollar", "label": "PCGS モルガン"},
    {"keyword": "NGC napoleon gold", "label": "NGC ナポレオン金貨"},
]

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
# スクレイパー
# ============================================================

def _random_interval() -> float:
    import random
    return random.uniform(*REQUEST_INTERVAL)


def _parse_ebay_price(text: str) -> dict | None:
    """価格テキスト → {usd, jpy}"""
    text = text.strip()
    # USD: "$105.50"
    m_usd = re.search(r'\$([\d,]+\.?\d*)', text)
    if m_usd:
        usd = float(m_usd.group(1).replace(',', ''))
        return {"usd": usd, "jpy": int(usd * USD_JPY_RATE)}

    # JPY: "16,865 円" or "16,865円"
    m_jpy = re.search(r'([\d,]+)\s*円', text)
    if m_jpy:
        jpy = int(m_jpy.group(1).replace(',', ''))
        return {"usd": round(jpy / USD_JPY_RATE, 2), "jpy": jpy}

    # GBP: "£XX.XX"
    m_gbp = re.search(r'£([\d,]+\.?\d*)', text)
    if m_gbp:
        gbp = float(m_gbp.group(1).replace(',', ''))
        return {"usd": round(gbp * 1.27, 2), "jpy": int(gbp * 1.27 * USD_JPY_RATE)}

    # EUR: "€XX.XX" or "EUR XX.XX"
    m_eur = re.search(r'[€EUR]\s*([\d,]+\.?\d*)', text)
    if m_eur:
        eur = float(m_eur.group(1).replace(',', ''))
        return {"usd": round(eur * 1.09, 2), "jpy": int(eur * 1.09 * USD_JPY_RATE)}

    return None


MONTH_MAP = {
    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
    'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
    'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
    '1月': '01', '2月': '02', '3月': '03', '4月': '04',
    '5月': '05', '6月': '06', '7月': '07', '8月': '08',
    '9月': '09', '10月': '10', '11月': '11', '12月': '12',
}


def _parse_sold_date(text: str) -> str | None:
    """'Sold Mar 18, 2026' or '販売済み 2026年3月18日' → 'YYYY-MM-DD'"""
    # English: "Sold Mar 18, 2026"
    m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d+),?\s+(\d{4})', text)
    if m:
        return f"{m.group(3)}-{MONTH_MAP[m.group(1)]}-{int(m.group(2)):02d}"

    # Japanese: "2026年3月18日"
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return None


def fetch_page(keyword: str, page: int = 1, session: requests.Session = None) -> dict:
    """1ページ分のeBay完了リスティングを取得"""
    params = {
        "_nkw": keyword,
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_ipg": str(RESULTS_PER_PAGE),
        "rt": "nc",
        "_udlo": "10",  # $10以上
    }
    if page > 1:
        params["_pgn"] = str(page)

    s = session or requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        resp = s.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"items": [], "total": 0, "has_next": False, "error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    # 総件数
    total = 0
    count_el = soup.find(class_=re.compile(r'srp-controls__count'))
    if count_el:
        m = re.search(r'([\d,]+)', count_el.get_text())
        if m:
            total = int(m.group(1).replace(',', ''))

    # 商品リスト (li.s-card inside ul.srp-results)
    srp = soup.find(class_='srp-results')
    cards = srp.find_all('li', class_=re.compile(r's-card')) if srp else []

    items = []
    for card in cards:
        texts = [t.strip() for t in card.stripped_strings]
        if not texts:
            continue

        item = {}

        # Sold date
        for t in texts:
            d = _parse_sold_date(t)
            if d:
                item["sold_date"] = d
                break

        # Title + URL from item link
        link = card.find('a', href=re.compile(r'/itm/'))
        if link:
            raw_title = link.get_text(strip=True)
            # Remove "Opens in a new window or tab" suffix
            raw_title = re.sub(r'Opens in a new window or tab$', '', raw_title).strip()
            raw_title = re.sub(r'新しいウィンドウまたはタブに表示されます$', '', raw_title).strip()
            item["title"] = raw_title
            href = link.get('href', '')
            m = re.search(r'/itm/(\d+)', href)
            if m:
                item["item_id"] = m.group(1)
                item["url"] = f"https://www.ebay.com/itm/{m.group(1)}"

        # Price
        for t in texts:
            price = _parse_ebay_price(t)
            if price:
                item["price_usd"] = price["usd"]
                item["price_jpy"] = price["jpy"]
                break

        # Bids / Buy type
        for t in texts:
            m_bids = re.search(r'(\d+)\s*bids?', t)
            if m_bids:
                item["bids"] = int(m_bids.group(1))
                break
            m_bids_jp = re.search(r'(\d+)件の入札', t)
            if m_bids_jp:
                item["bids"] = int(m_bids_jp.group(1))
                break
            if 'Buy It Now' in t or '即決' in t:
                item["buy_type"] = "BuyItNow"
                break
            if 'Best offer' in t or 'ベストオファー' in t:
                item["buy_type"] = "BestOffer"
                break

        # Seller (typically a short username near end of texts)
        for t in texts:
            if re.match(r'^[a-z0-9._*-]{3,30}$', t):
                item["seller"] = t
                break

        if item.get("title") and item.get("price_jpy") and item["title"] != "Shop on eBay":
            items.append(item)

    # Next page
    has_next = len(items) >= RESULTS_PER_PAGE * 0.8 and (page * RESULTS_PER_PAGE) < total

    return {"items": items, "total": total, "has_next": has_next, "error": None}


def fetch_all_pages(keyword: str, max_pages: int = MAX_PAGES_DEFAULT,
                    session: requests.Session = None) -> list[dict]:
    """全ページを取得"""
    all_items = []
    total_available = 0

    for page in range(1, max_pages + 1):
        result = fetch_page(keyword, page=page, session=session)

        if result["error"]:
            print(f"    ERROR page {page}: {result['error']}")
            break

        items = result["items"]
        all_items.extend(items)

        if page == 1:
            total_available = result.get("total", 0)
            total_pages = min(max_pages, (total_available + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
            print(f"    総件数: {total_available:,}件 (最大{total_pages}ページ)")

        if page % 5 == 0 or not result["has_next"]:
            print(f"    page {page}: +{len(items)}件 (累計 {len(all_items):,}件)")

        if not result["has_next"] or len(items) == 0:
            break

        time.sleep(_random_interval())

    return all_items


# ============================================================
# レコード変換
# ============================================================

def convert_to_record(item: dict, keyword: str) -> dict | None:
    """scrape結果 → Supabaseレコード"""
    title = item.get("title", "").strip()
    price_jpy = item.get("price_jpy")

    if not title or not price_jpy:
        return None

    record = {
        "source": SOURCE,
        "currency": "USD",
        "title": title,
        "price": item.get("price_usd", round(price_jpy / USD_JPY_RATE, 2)),
        "price_jpy": price_jpy,
    }

    if item.get("sold_date"):
        record["sold_date"] = item["sold_date"]

    if item.get("url"):
        record["url"] = item["url"]

    if item.get("seller"):
        record["seller_name"] = item["seller"]

    # raw_data
    raw = {"fetch_keyword": keyword, "fetch_source": "ebay_completed"}
    if item.get("bids") is not None:
        raw["bid_count"] = item["bids"]
    if item.get("buy_type"):
        raw["buy_type"] = item["buy_type"]
    if item.get("item_id"):
        raw["item_id"] = item["item_id"]
    record["raw_data"] = raw

    # タイトルパーサー
    parsed = parse_title(title)
    for key in ("grader", "grade", "country", "year", "material",
                "denomination", "series", "tags"):
        if key in parsed:
            record[key] = parsed[key]

    # grader補完
    if "grader" not in record:
        kw_upper = keyword.upper()
        if "NGC" in kw_upper:
            record["grader"] = "NGC"
        elif "PCGS" in kw_upper:
            record["grader"] = "PCGS"

    record["dedup_key"] = make_dedup_key(
        SOURCE,
        url=record.get("url"),
        title=title,
        price=int(price_jpy),
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
            else:
                failed += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: ERROR - {error_str[:300]}")

    return success, failed


# ============================================================
# 実行ログ
# ============================================================

def save_execution_log(log_data: dict):
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

    query_filter = None
    max_pages = MAX_PAGES_DEFAULT
    i = 0
    while i < len(args):
        if args[i] == "--queries" and i + 1 < len(args):
            query_filter = [q.strip() for q in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            max_pages = int(args[i + 1])
            i += 2
        else:
            i += 1

    queries = DEFAULT_QUERIES
    if query_filter:
        queries = [q for q in queries if q["keyword"] in query_filter or q["label"] in query_filter]
        if not queries:
            print(f"ERROR: 指定クエリが見つかりません: {query_filter}")
            sys.exit(1)

    start_time = datetime.now()
    print(f"{'=' * 60}")
    print(f"  eBay Completed Listings 差分更新")
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

        if qi < len(queries):
            time.sleep(_random_interval())

    # 重複除去
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
        "search_template": DEFAULT_QUERIES,
    }
    save_execution_log(log_data)

    return success, failed


if __name__ == "__main__":
    main()
