"""eBay リアルタイム監視 + オークション監視 + 交渉候補抽出

3本柱を1スクリプトで統合管理する。

使い方:
    python run.py ebay-watch                          # リアルタイム買い候補表示
    python run.py ebay-watch --auctions               # オークション監視（終了間近優先）
    python run.py ebay-watch --offers                  # 交渉候補抽出
    python run.py ebay-watch --all                     # 全モード実行
    python run.py ebay-watch --limit 5                 # ページ制限
    python run.py ebay-watch --queries "NGC sovereign" # 特定クエリのみ

柱1: リアルタイム監視 - BuyItNow/固定価格から即買い候補を抽出
柱2: オークション監視 - 入札案件の終了間近を追跡、最大入札額を提案
柱3: 価格交渉 - MakeOffer可能商品から交渉候補を抽出
"""

import json
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client

# ============================================================
# 設定
# ============================================================

BASE_URL = "https://www.ebay.com/sch/i.html"
RESULTS_PER_PAGE = 240
REQUEST_INTERVAL = (3.0, 5.0)
MAX_PAGES_DEFAULT = 10
USD_JPY_RATE = 150.0

# コスト定数
SHIPPING_ESTIMATE = 3000   # 送料概算（円）
YAHOO_FEE_RATE = 0.088     # ヤフオク手数料率 8.8%
EBAY_BUYER_FEE = 0.0       # eBay購入手数料なし

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 検索クエリ（広く取得、NGC/PCGS中心）
DEFAULT_QUERIES = [
    {"keyword": "NGC coins", "label": "NGC全般"},
    {"keyword": "PCGS coins", "label": "PCGS全般"},
]

LOG_DIR = PROJECT_ROOT / "data" / "ebay_monitor"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _random_interval() -> float:
    """REQUEST_INTERVAL の範囲でランダム待機秒数を返す。"""
    return random.uniform(REQUEST_INTERVAL[0], REQUEST_INTERVAL[1])


# ============================================================
# タイトルパーサー
# ============================================================

def _load_parser():
    from scripts.import_yahoo_history import parse_title
    return parse_title

parse_title = _load_parser()


# ============================================================
# 利益条件DB（Supabaseの直近3か月ヤフオクデータから構築）
# ============================================================

def build_profit_lookup(months=3, min_records=2) -> dict:
    """ヤフオク直近N月のgrader+grade+yearキー別統計をロード"""
    client = get_client()
    cutoff = (datetime.now() - timedelta(days=months * 31)).strftime("%Y-%m-%d")

    all_records = []
    page_size = 1000
    offset = 0
    while True:
        q = (client.table("market_transactions")
             .select("grader,grade,year,price_jpy,tags")
             .eq("source", "yahoo")
             .gte("sold_date", cutoff)
             .not_.contains("tags", '{"_noise:set"}')
             .not_.contains("tags", '{"_noise:non_coin"}')
             .order("sold_date", desc=True)
             .range(offset, offset + page_size - 1))
        batch = q.execute().data
        all_records.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    # grader|grade|year でグルーピング
    groups = defaultdict(list)
    for r in all_records:
        g = r.get("grader", "")
        gr = r.get("grade", "")
        y = r.get("year")
        if g and gr:
            key = f"{g}|{gr}|{y}" if y else f"{g}|{gr}"
            groups[key].append(r.get("price_jpy", 0))

    # 統計算出
    lookup = {}
    for key, prices in groups.items():
        if len(prices) < min_records:
            continue
        prices.sort()
        lookup[key] = {
            "count": len(prices),
            "median": prices[len(prices) // 2],
            "avg": int(sum(prices) / len(prices)),
            "sell_after_fee": int(prices[len(prices) // 2] * (1 - YAHOO_FEE_RATE)),
        }

    return lookup


def calc_profit(ebay_price_jpy: int, shipping: int, lookup_key: str,
                profit_lookup: dict) -> dict | None:
    """利益計算"""
    if lookup_key not in profit_lookup:
        return None
    info = profit_lookup[lookup_key]
    cost = ebay_price_jpy + shipping
    sell = info["sell_after_fee"]
    profit = sell - cost
    pct = profit / cost * 100 if cost > 0 else 0
    return {
        "cost": cost,
        "sell": sell,
        "profit": profit,
        "pct": pct,
        "yahoo_median": info["median"],
        "yahoo_count": info["count"],
    }


# ============================================================
# eBay アクティブリスティング取得（Browse API経由）
# ============================================================

def _get_api():
    """eBay Browse API クライアント取得"""
    from scripts.ebay_api_client import EbayBrowseAPI
    return EbayBrowseAPI()


def fetch_active_listings(keyword, page=1, listing_type="all", session=None):
    """eBayアクティブリスティングを取得（Browse API経由）

    listing_type:
        "all" - 全て
        "auction" - オークションのみ
        "buy_it_now" - 即決のみ
    """
    api = _get_api()
    if not api.is_configured:
        return {"items": [], "total": 0, "has_next": False,
                "error": "eBay API keys not configured. Run: python scripts/ebay_api_client.py"}

    # listing_type → buying_options
    buying_options = None
    sort = None
    if listing_type == "auction":
        buying_options = "AUCTION"
        sort = "endingSoonest"
    elif listing_type == "buy_it_now":
        buying_options = "FIXED_PRICE"
        sort = "price"

    offset = (page - 1) * 200
    result = api.search(
        query=keyword,
        limit=200,
        offset=offset,
        buying_options=buying_options,
        sort=sort,
        min_price=10,
    )

    if result.get("error"):
        return {"items": [], "total": 0, "has_next": False, "error": result["error"]}

    # タイトルパーサー適用
    for item in result["items"]:
        parsed = parse_title(item.get("title", ""))
        item.update(parsed)

    return {
        "items": result["items"],
        "total": result["total"],
        "has_next": result.get("has_next", False),
        "error": None,
    }


# ============================================================
# 柱1: リアルタイム監視（BuyItNow即買い候補）
# ============================================================

def realtime_monitor(queries, max_pages, profit_lookup, session):
    """固定価格商品から即買い候補を抽出"""
    all_candidates = []

    for qi, query in enumerate(queries, 1):
        keyword = query["keyword"]
        print(f"[{qi}/{len(queries)}] '{keyword}' (BuyItNow)")

        for page in range(1, max_pages + 1):
            result = fetch_active_listings(keyword, page=page,
                                            listing_type="buy_it_now", session=session)
            if result.get("error"):
                print(f"  ERROR: {result['error']}")
                break

            if page == 1:
                print(f"  Total: {result['total']:,} listings")

            for item in result["items"]:
                grader = item.get("grader", "")
                grade = item.get("grade", "")
                year = item.get("year")

                if not grader or not grade:
                    continue

                key = f"{grader}|{grade}|{year}" if year else f"{grader}|{grade}"
                shipping = item.get("shipping_jpy", SHIPPING_ESTIMATE)
                if item.get("shipping") == 0:
                    shipping = 0

                profit_info = calc_profit(item["price_jpy"], shipping, key, profit_lookup)
                if profit_info and profit_info["profit"] > 0 and profit_info["pct"] > 50:
                    item["profit_info"] = profit_info
                    item["lookup_key"] = key
                    all_candidates.append(item)

            if not result["has_next"]:
                break
            time.sleep(_random_interval())

        if qi < len(queries):
            time.sleep(_random_interval())

    # 利益率でソート
    all_candidates.sort(key=lambda x: -x["profit_info"]["profit"])
    return all_candidates


# ============================================================
# 柱2: オークション監視（終了間近優先）
# ============================================================

def auction_monitor(queries, max_pages, profit_lookup, session):
    """入札案件の終了間近を追跡、最大入札額を提案"""
    all_auctions = []

    for qi, query in enumerate(queries, 1):
        keyword = query["keyword"]
        print(f"[{qi}/{len(queries)}] '{keyword}' (Auction)")

        for page in range(1, max_pages + 1):
            result = fetch_active_listings(keyword, page=page,
                                            listing_type="auction", session=session)
            if result.get("error"):
                print(f"  ERROR: {result['error']}")
                break

            if page == 1:
                print(f"  Total: {result['total']:,} auctions")

            for item in result["items"]:
                grader = item.get("grader", "")
                grade = item.get("grade", "")
                year = item.get("year")

                if not grader or not grade:
                    continue

                key = f"{grader}|{grade}|{year}" if year else f"{grader}|{grade}"
                shipping = item.get("shipping_jpy", SHIPPING_ESTIMATE)
                if item.get("shipping") == 0:
                    shipping = 0

                if key in profit_lookup:
                    info = profit_lookup[key]
                    sell = info["sell_after_fee"]
                    # 最大入札許容額 = 販売見込み - 送料 - 最低利益マージン(20%)
                    max_bid = int((sell - shipping) / 1.2)
                    current_price = item["price_jpy"]

                    if max_bid > current_price and max_bid > 0:
                        item["max_bid_jpy"] = max_bid
                        item["max_bid_usd"] = round(max_bid / USD_JPY_RATE, 2)
                        item["headroom"] = max_bid - current_price
                        item["yahoo_median"] = info["median"]
                        item["yahoo_count"] = info["count"]
                        item["lookup_key"] = key
                        all_auctions.append(item)

            if not result["has_next"]:
                break
            time.sleep(_random_interval())

        if qi < len(queries):
            time.sleep(_random_interval())

    # 終了間近かつ余裕があるものを優先
    all_auctions.sort(key=lambda x: (x.get("ends_in_hours", 999), -x["headroom"]))
    return all_auctions


# ============================================================
# 柱3: 交渉候補抽出（MakeOffer対象）
# ============================================================

def offer_candidates(queries, max_pages, profit_lookup, session):
    """価格交渉候補を抽出"""
    all_offers = []

    for qi, query in enumerate(queries, 1):
        keyword = query["keyword"]
        print(f"[{qi}/{len(queries)}] '{keyword}' (Offer candidates)")

        for page in range(1, max_pages + 1):
            result = fetch_active_listings(keyword, page=page,
                                            listing_type="buy_it_now", session=session)
            if result.get("error"):
                break

            if page == 1:
                print(f"  Total: {result['total']:,} listings")

            for item in result["items"]:
                # MakeOffer可能な商品のみ
                if item.get("listing_type") not in ("best_offer", "buy_or_offer"):
                    continue

                grader = item.get("grader", "")
                grade = item.get("grade", "")
                year = item.get("year")

                if not grader or not grade:
                    continue

                key = f"{grader}|{grade}|{year}" if year else f"{grader}|{grade}"

                if key in profit_lookup:
                    info = profit_lookup[key]
                    sell = info["sell_after_fee"]
                    shipping = item.get("shipping_jpy", SHIPPING_ESTIMATE)
                    if item.get("shipping") == 0:
                        shipping = 0

                    # 希望仕入価格 = 販売見込み - 送料 - 目標利益率50%分
                    target_cost = int(sell / 1.5)
                    target_price = target_cost - shipping
                    current_price = item["price_jpy"]
                    discount_needed = current_price - target_price

                    if target_price > 0 and discount_needed > 0:
                        discount_pct = discount_needed / current_price * 100
                        # 値引き30%以内なら交渉可能性あり
                        if discount_pct <= 30:
                            item["target_price_jpy"] = target_price
                            item["target_price_usd"] = round(target_price / USD_JPY_RATE, 2)
                            item["discount_needed"] = discount_needed
                            item["discount_pct"] = discount_pct
                            item["yahoo_median"] = info["median"]
                            item["yahoo_count"] = info["count"]
                            item["expected_profit"] = sell - target_cost
                            item["lookup_key"] = key
                            all_offers.append(item)

            if not result["has_next"]:
                break
            time.sleep(_random_interval())

        if qi < len(queries):
            time.sleep(_random_interval())

    # 値引き幅が小さい順（交渉成立しやすい順）
    all_offers.sort(key=lambda x: x["discount_pct"])
    return all_offers


# ============================================================
# 出力
# ============================================================

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='replace').decode())


def print_realtime_results(candidates, top_n=10):
    print()
    print(f"{'=' * 80}")
    print(f"  柱1: リアルタイム買い候補 ({len(candidates)}件)")
    print(f"{'=' * 80}")
    if not candidates:
        print("  候補なし")
        return

    print(f"  Top {min(top_n, len(candidates))}:")
    print()
    for i, c in enumerate(candidates[:top_n], 1):
        pi = c["profit_info"]
        safe_print(f"  #{i} [{c.get('grader','')}/{c.get('grade','')} {c.get('year','')}]")
        safe_print(f"     Title: {c['title'][:65]}")
        print(f"     Price: {c['price_jpy']:>8,}JPY + Ship:{c.get('shipping_jpy', SHIPPING_ESTIMATE):>5,} = Cost:{pi['cost']:>9,}")
        print(f"     Yahoo: {pi['yahoo_median']:>8,}JPY ({pi['yahoo_count']}件) -> Sell:{pi['sell']:>9,}")
        print(f"     Profit: +{pi['profit']:>8,}JPY (+{pi['pct']:.0f}%)")
        print(f"     URL: {c.get('url','')}")
        print()


def print_auction_results(auctions, top_n=10):
    print()
    print(f"{'=' * 80}")
    print(f"  柱2: オークション監視 ({len(auctions)}件)")
    print(f"{'=' * 80}")
    if not auctions:
        print("  候補なし")
        return

    print(f"  Top {min(top_n, len(auctions))} (終了間近優先):")
    print()
    for i, a in enumerate(auctions[:top_n], 1):
        ends = a.get("ends_in_hours", 0)
        ends_str = f"{int(ends)}h{int((ends % 1) * 60)}m" if ends < 24 else f"{ends / 24:.1f}d"
        safe_print(f"  #{i} [{a.get('grader','')}/{a.get('grade','')} {a.get('year','')}] ends: {ends_str}")
        safe_print(f"     Title: {a['title'][:65]}")
        print(f"     Current: {a['price_jpy']:>8,}JPY  Bids: {a.get('bids', 0)}")
        print(f"     MaxBid: {a['max_bid_jpy']:>8,}JPY (${a['max_bid_usd']:.0f}) Headroom: +{a['headroom']:>6,}")
        print(f"     Yahoo: {a['yahoo_median']:>8,}JPY ({a['yahoo_count']}件)")
        print(f"     URL: {a.get('url','')}")
        print()


def print_offer_results(offers, top_n=10):
    print()
    print(f"{'=' * 80}")
    print(f"  柱3: 交渉候補 ({len(offers)}件)")
    print(f"{'=' * 80}")
    if not offers:
        print("  候補なし")
        return

    print(f"  Top {min(top_n, len(offers))} (交渉成立しやすい順):")
    print()
    for i, o in enumerate(offers[:top_n], 1):
        safe_print(f"  #{i} [{o.get('grader','')}/{o.get('grade','')} {o.get('year','')}]")
        safe_print(f"     Title: {o['title'][:65]}")
        print(f"     Current: {o['price_jpy']:>8,}JPY -> Target: {o['target_price_jpy']:>8,}JPY (${o['target_price_usd']:.0f})")
        print(f"     Discount: -{o['discount_needed']:>6,}JPY (-{o['discount_pct']:.1f}%)")
        print(f"     Yahoo: {o['yahoo_median']:>8,}JPY ({o['yahoo_count']}件)")
        print(f"     ExpProfit: +{o['expected_profit']:>6,}JPY")
        print(f"     URL: {o.get('url','')}")
        print()

    # 交渉文テンプレ
    print("  --- 交渉メッセージテンプレート ---")
    print("  Hello, I am interested in this item.")
    print("  Would you consider accepting ${target_price_usd} for it?")
    print("  I am a serious buyer and can pay immediately.")
    print("  Thank you for your consideration.")
    print()


# ============================================================
# 実行ログ保存
# ============================================================

def save_monitor_log(mode, results, start_time):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"monitor_{mode}_{timestamp}.json"

    log_data = {
        "execution_time": start_time.isoformat(),
        "mode": mode,
        "executor": "COO-auto",
        "result_count": len(results),
    }

    # トップ10のサマリーだけ保存（全件だとファイルが大きくなる）
    summaries = []
    for r in results[:20]:
        s = {
            "title": r.get("title", "")[:80],
            "price_jpy": r.get("price_jpy", 0),
            "url": r.get("url", ""),
            "lookup_key": r.get("lookup_key", ""),
        }
        if "profit_info" in r:
            s["profit"] = r["profit_info"]["profit"]
            s["profit_pct"] = round(r["profit_info"]["pct"])
        if "max_bid_jpy" in r:
            s["max_bid_jpy"] = r["max_bid_jpy"]
            s["headroom"] = r["headroom"]
        if "target_price_jpy" in r:
            s["target_price_jpy"] = r["target_price_jpy"]
            s["discount_pct"] = round(r["discount_pct"], 1)
        summaries.append(s)

    log_data["top_results"] = summaries

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    return log_path


# ============================================================
# メイン
# ============================================================

def main():
    args = sys.argv[1:]
    args = [a for a in args if a not in ("ebay-watch",)]

    mode_realtime = "--all" in args or (not any(x in args for x in ("--auctions", "--offers")))
    mode_auctions = "--all" in args or "--auctions" in args
    mode_offers = "--all" in args or "--offers" in args
    args = [a for a in args if a not in ("--all", "--auctions", "--offers")]

    max_pages = MAX_PAGES_DEFAULT
    query_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            max_pages = int(args[i + 1])
            i += 2
        elif args[i] == "--queries" and i + 1 < len(args):
            query_filter = args[i + 1].split(",")
            i += 2
        else:
            i += 1

    queries = DEFAULT_QUERIES
    if query_filter:
        queries = [q for q in queries if any(kw in q["keyword"] for kw in query_filter)]

    start_time = datetime.now()
    print(f"{'=' * 80}")
    print(f"  eBay 仕入れ監視ダッシュボード")
    print(f"  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 80}")
    modes = []
    if mode_realtime: modes.append("リアルタイム")
    if mode_auctions: modes.append("オークション")
    if mode_offers: modes.append("交渉候補")
    print(f"  モード: {' / '.join(modes)}")
    print(f"  クエリ: {len(queries)}本  最大ページ: {max_pages}")
    print()

    # 利益条件ロード
    print("利益条件DB構築中（ヤフオク直近3か月）...")
    profit_lookup = build_profit_lookup(months=3)
    print(f"  {len(profit_lookup)}パターンの利益条件をロード")
    print()

    session = requests.Session()

    # 柱1: リアルタイム
    if mode_realtime:
        print("--- 柱1: リアルタイム監視 ---")
        candidates = realtime_monitor(queries, max_pages, profit_lookup, session)
        print_realtime_results(candidates)
        save_monitor_log("realtime", candidates, start_time)

    # 柱2: オークション
    if mode_auctions:
        print("--- 柱2: オークション監視 ---")
        auctions = auction_monitor(queries, max_pages, profit_lookup, session)
        print_auction_results(auctions)
        save_monitor_log("auction", auctions, start_time)

    # 柱3: 交渉候補
    if mode_offers:
        print("--- 柱3: 交渉候補抽出 ---")
        offers = offer_candidates(queries, max_pages, profit_lookup, session)
        print_offer_results(offers)
        save_monitor_log("offers", offers, start_time)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    print(f"{'=' * 80}")
    print(f"  完了: {duration:.0f}秒")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
