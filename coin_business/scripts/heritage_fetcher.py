"""
heritage_fetcher.py  ─  Heritage Auctions 現在出品ロット取得

Heritage の開催中・直前オークションから「現在入札可能なロット」を取得し、
overseas_lot スキーマに変換して返す。

取得対象:
  auction_schedule.json の heritage エントリで status=active/imminent のもの
  → search_url / sale番号からロット一覧を取得

取得方式:
  1. JSON埋め込みデータ抽出 (window.__INITIAL_STATE__ / window.heritage 等)
  2. HTML正規表現パース (fallback)
  ※ JSヘビーページは Playwright対応予定（heritage_fetcher_playwright.py で後日追加）

使い方:
  from scripts.heritage_fetcher import fetch_heritage_lots

  lots = fetch_heritage_lots(dry_run=False)
  # → list[overseas_lot dict]

  # 特定オークションのみ
  lots = fetch_heritage_lots(auction_ids=["heritage_hk_spring_world_2026_apr"])
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))

from auction_status_checker import (
    get_active_auctions,
    get_all_auctions_with_status,
    STATUS_ACTIVE, STATUS_IMMINENT,
)
from auction_cost_calculator import enrich_lot_with_cost

logger = logging.getLogger(__name__)

# ── 定数 ──────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_DELAY      = 2.0   # 秒
MAX_PAGES          = 10    # 1オークション最大ページ数 (Heritageは25件/page → 10p=250件)
LOTS_PER_PAGE      = 25    # Heritage実測値: auction_name+atype形式は25件/page固定
LOTS_PER_PAGE_REQ  = 25    # リクエスト時の Nrpp 値
USD_JPY_RATE       = 150.0 # 暫定為替レート（fetch_daily_rates.py で更新予定）

# Heritage のロット検索エンドポイント（catalog mode）
HERITAGE_SEARCH_BASE = "https://coins.ha.com/c/search/results.zx"

# 検索パラメータ（現在出品 / World Coins 専用）
# 変更履歴: CHG-027 2026-04-03
#   旧 N=790+231+4294967251 + type=surl-XXXXX →
#       JS描画ページ or Medals/Tokens/Nuggets アーカイブを返す（使用不可）
#   新 mode=live + auction_name=XXXXX + type=atype →
#       サーバーサイド描画で実際のオークションロット一覧を返す（確認済）
#   確認: sale 61610 page1=Ancients (EXCLUDE_KWで除外) / page2〜=World Coins (NGC/PCGS含む)
SEARCH_PARAMS_LIVE = {
    "dept": "1909",              # World & Ancient Coins
    "mode": "live",              # 現在出品中 (archiveではなく)
    "type": "atype",             # サーバーサイド描画モード
    "Nrpp": str(LOTS_PER_PAGE_REQ),  # 25件/page (Heritage固定値)
    # auction_name は _fetch_page() で動的に設定
}


# ── セッション ────────────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         "https://coins.ha.com/",
    })
    return s


# ── sale番号抽出 ─────────────────────────────────────────────────

def _extract_sale_no(auction: dict) -> Optional[str]:
    """
    auction エントリから Heritage sale 番号を抽出。

    search_url 例:
      https://coins.ha.com/c/search/results.zx?...&type=surl-61610
      → "61610"

    name 例:
      "Heritage HK Spring World Coins Showcase #61610"
      → "61610"
    """
    # search_url から
    url = auction.get("search_url") or auction.get("url") or ""
    m = re.search(r"surl-(\d+)", url)
    if m:
        return m.group(1)

    # name から (#XXXXX パターン)
    name = auction.get("name") or ""
    m = re.search(r"#(\d{4,6})", name)
    if m:
        return m.group(1)

    return None


# ── Heritage ページ取得 ───────────────────────────────────────────

def _fetch_page(session: requests.Session, sale_no: str, page: int = 1) -> Optional[str]:
    """
    Heritage の1ページ分の HTML を取得。
    【CHG-027】 mode=live&auction_name=XXXXX&type=atype 形式に変更。
    失敗時は None。
    """
    params = dict(SEARCH_PARAMS_LIVE)
    # auction_name=XXXXX で sale を指定 (type=surl-XXXXX は JS描画のため不使用)
    params["auction_name"] = sale_no
    if page > 1:
        params["No"] = str((page - 1) * LOTS_PER_PAGE_REQ)

    try:
        resp = session.get(HERITAGE_SEARCH_BASE, params=params, timeout=20)
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"  [Heritage] sale={sale_no} page={page}: HTTP {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"  [Heritage] sale={sale_no} page={page}: {e}")
        return None


# ── JSON埋め込みデータ抽出 ────────────────────────────────────────

def _extract_json_lots(html: str) -> list[dict]:
    """
    Heritage の HTML ページに埋め込まれた JSON データからロット情報を抽出。

    Heritage は window.digitalData / window.__lotData__ / JSON-LD 等に
    構造化データを埋め込む場合がある。
    """
    lots = []

    # ── JSON-LD (schema.org ItemList) を試みる
    json_ld_blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    for block in json_ld_blocks:
        try:
            data = json.loads(block.strip())
            # ItemList 形式
            if isinstance(data, dict) and data.get("@type") in ("ItemList", "AuctionEvent"):
                items = data.get("itemListElement") or data.get("items") or []
                for item in items:
                    lot = _normalize_json_lot(item)
                    if lot:
                        lots.append(lot)
            # 単一 Product
            elif isinstance(data, dict) and data.get("@type") == "Product":
                lot = _normalize_json_lot(data)
                if lot:
                    lots.append(lot)
        except (json.JSONDecodeError, KeyError):
            pass

    # ── window.__NEXT_DATA__ (Next.js) を試みる
    if not lots:
        m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                next_data = json.loads(m.group(1))
                # props.pageProps.lots 等のパスを探索
                lots_data = (
                    next_data.get("props", {}).get("pageProps", {}).get("lots")
                    or next_data.get("props", {}).get("pageProps", {}).get("items")
                )
                if lots_data and isinstance(lots_data, list):
                    for item in lots_data:
                        lot = _normalize_json_lot(item)
                        if lot:
                            lots.append(lot)
            except (json.JSONDecodeError, KeyError):
                pass

    return lots


def _normalize_json_lot(item: dict) -> Optional[dict]:
    """JSON オブジェクトから overseas_lot 用の基本フィールドを抽出。"""
    if not isinstance(item, dict):
        return None

    # タイトル候補
    title = (item.get("name") or item.get("title") or
             item.get("item", {}).get("name") or "")
    title = re.sub(r"<[^>]+>", "", str(title)).strip()
    if not title:
        return None

    # URL
    url = (item.get("url") or item.get("lotUrl") or
           item.get("item", {}).get("url") or "")

    # ロット番号
    lot_no = str(item.get("lotNumber") or item.get("lot") or
                 item.get("position") or "")

    # 価格
    price_raw = (item.get("currentBid") or item.get("lowEstimate") or
                 item.get("offers", {}).get("lowPrice") or
                 item.get("price", {}).get("value") or 0)
    try:
        price_usd = float(str(price_raw).replace(",", "").replace("$", "")) if price_raw else 0.0
    except ValueError:
        price_usd = 0.0

    return {
        "lot_number":    lot_no,
        "lot_title":     title,
        "lot_url":       url,
        "current_price": price_usd,
    }


# ── HTML 正規表現パース (fallback) ────────────────────────────────

def _parse_html_lots(html: str, sale_no: str) -> list[dict]:
    """
    HTML を正規表現でパースしてロット情報を抽出する fallback。
    Heritage の ATG フレームワーク・各種クラス名に対応。
    """
    lots = []

    # ロットブロックを抽出（Heritage の典型的なHTML構造）
    # パターン1: <div class="lot-search-item"> 系
    block_pattern = re.compile(
        r'<(?:div|article|li)[^>]+class="[^"]*(?:lot-search-item|search-result-item|item-info)[^"]*"[^>]*>'
        r'(.*?)(?=<(?:div|article|li)[^>]+class="[^"]*(?:lot-search-item|search-result-item|item-info)|</(?:div|article|ul)>)',
        re.DOTALL | re.IGNORECASE,
    )

    # タイトル抽出パターン
    title_pattern = re.compile(
        r'<(?:span|h\d|div|a)[^>]+class="[^"]*(?:lot-title|item-title|title-wrapper)[^"]*"[^>]*>'
        r'\s*(?:<[^>]+>)*\s*(.*?)\s*(?:</[^>]+>)*\s*</(?:span|h\d|div|a)>',
        re.DOTALL | re.IGNORECASE,
    )

    # ロット番号
    lot_no_pattern = re.compile(r'[Ll]ot\s*#?\s*(\d+)', re.IGNORECASE)

    # 現在入札額
    bid_pattern = re.compile(
        r'(?:Current\s+Bid|Starting\s+Bid|Estimate)[:\s$]*([0-9,]+)',
        re.IGNORECASE,
    )

    # URL (coins.ha.com / historical.ha.com 両対応)
    url_pattern = re.compile(
        r'href="(https://(?:coins|historical)\.ha\.com/itm/[^"?]+(?:\?[^"]*)?)"',
        re.IGNORECASE,
    )

    # ── ブロック単位でパース
    for block_m in block_pattern.finditer(html):
        block = block_m.group(1)

        title_m = title_pattern.search(block)
        if not title_m:
            continue
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
        if not title or len(title) < 5:
            continue

        url_m    = url_pattern.search(block)
        url      = url_m.group(1) if url_m else ""

        lot_no_m = lot_no_pattern.search(block)
        lot_no   = lot_no_m.group(1) if lot_no_m else ""

        bid_m    = bid_pattern.search(block)
        price_usd = 0.0
        if bid_m:
            try:
                price_usd = float(bid_m.group(1).replace(",", ""))
            except ValueError:
                pass

        lots.append({
            "lot_number":    lot_no,
            "lot_title":     title,
            "lot_url":       url,
            "current_price": price_usd,
        })

    # ── fallback: URL+タイトルの単純抽出 (coins/historical 両対応)
    if not lots:
        urls_and_titles = re.findall(
            r'href="(https://(?:coins|historical)\.ha\.com/itm/[^"]+)"[^>]*>[^<]*<[^>]+>([^<]{10,120})<',
            html,
        )
        for url, title in urls_and_titles[:50]:
            title = title.strip()
            if any(kw in title.upper() for kw in ["NGC", "PCGS", "MS", "PF", "GOLD", "SILVER"]):
                lots.append({
                    "lot_number":    "",
                    "lot_title":     title,
                    "lot_url":       url,
                    "current_price": 0.0,
                })

    logger.debug(f"  [Heritage HTML fallback] sale={sale_no}: {len(lots)}件パース")
    return lots


# ── lots → overseas_lot スキーマ変換 ─────────────────────────────

def _to_overseas_lot(raw: dict, auction: dict, fx_rate: float = USD_JPY_RATE) -> dict:
    """
    Heritage から取得した生ロットデータを overseas_lot 標準スキーマに変換。
    コスト計算は auction_cost_calculator.enrich_lot_with_cost() に委譲。
    （Heritage は ceo_confirmed=False のため estimated_cost_jpy=None となる）
    """
    price_usd = float(raw.get("current_price") or 0)
    price_jpy = int(price_usd * fx_rate) if price_usd > 0 else 0

    lot = {
        # ── 出所情報
        "source":         "heritage",
        "auction_house":  "Heritage Auctions",
        "auction_id":     auction.get("id", ""),
        "auction_name":   auction.get("name", ""),

        # ── ロット情報
        "lot_number":     raw.get("lot_number") or "",
        "lot_title":      raw.get("lot_title") or "",
        "lot_url":        raw.get("lot_url") or "",

        # ── 価格情報
        "current_price":  price_usd,
        "realized_price": None,
        "currency":       "USD",
        "price_jpy":      price_jpy,
        "fx_rate":        fx_rate,

        # ── 時間情報
        "start_date":     auction.get("start_date"),
        "end_date":       auction.get("end_date"),
        "lot_end_time":   None,   # Heritage は lot単位の終了時刻を取得困難

        # ── マッチング (初期値: 未照合)
        "coin_match_status": "unmatched",
        "management_no":     None,
        "match_score":       None,

        # ── 判定 (candidates_writer で設定)
        "judgment":           "pending",
        "judgment_reason":    None,
        "buy_limit_jpy":      None,   # coin_matcher で設定

        # ── 運用メタ
        "priority":          auction.get("priority", 1),
        "is_active_auction": auction.get("_status") in (STATUS_ACTIVE, STATUS_IMMINENT),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "status":            "pending",

        # ── dedup (candidates_writer で生成)
        "dedup_key": None,
    }

    # auction_fee_rules.json に基づくコスト計算
    # Heritage は ceo_confirmed=False → estimated_cost_jpy=None (安全ロック)
    lot = enrich_lot_with_cost(lot, fx_rate=fx_rate, require_confirmed=True)
    return lot


# ── メイン取得関数 ─────────────────────────────────────────────────

def fetch_heritage_lots(
    auction_ids: Optional[list[str]] = None,
    dry_run: bool = False,
    fx_rate: float = USD_JPY_RATE,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """
    Heritage の開催中・直前オークションからロット一覧を取得し、
    overseas_lot スキーマのリストで返す。

    Args:
        auction_ids : 取得するオークションID (None=active/imminentの全て)
        dry_run     : True の場合、取得のみで DB書き込み不要を示す
        fx_rate     : USD/JPY 為替レート
        max_pages   : 1オークション当たりの最大ページ数

    Returns:
        list[dict]: overseas_lot スキーマのリスト
    """
    session = _session()

    # 対象オークションを決定
    if auction_ids:
        all_auctions = get_all_auctions_with_status()
        target_auctions = [
            a for a in all_auctions
            if a.get("id") in auction_ids
            and a.get("company") == "heritage"
        ]
    else:
        target_auctions = [
            a for a in get_active_auctions()
            if a.get("company") == "heritage"
        ]

    if not target_auctions:
        logger.info("  [Heritage] 対象オークションなし")
        return []

    logger.info(f"  [Heritage] 取得対象: {len(target_auctions)}件")

    all_lots: list[dict] = []

    for auction in target_auctions:
        sale_no = _extract_sale_no(auction)
        if not sale_no:
            logger.warning(f"  [Heritage] sale番号取得失敗: {auction.get('id')}")
            continue

        auction_name = auction.get("name", auction.get("id", ""))
        logger.info(f"  [Heritage] {auction_name} (sale={sale_no}) 取得開始")

        raw_lots: list[dict] = []

        for page in range(1, max_pages + 1):
            html = _fetch_page(session, sale_no, page)
            if not html:
                break

            # JSON埋め込みを優先試行
            json_lots = _extract_json_lots(html)
            if json_lots:
                raw_lots.extend(json_lots)
                logger.debug(f"    page {page}: JSON {len(json_lots)}件")
            else:
                # HTML fallback
                html_lots = _parse_html_lots(html, sale_no)
                raw_lots.extend(html_lots)
                logger.debug(f"    page {page}: HTML {len(html_lots)}件")

            # 最終ページ判定（取得件数が LOTS_PER_PAGE_REQ 未満 = 最終ページ）
            page_count = len(json_lots or html_lots)
            if page_count < LOTS_PER_PAGE_REQ:
                break

            time.sleep(REQUEST_DELAY)

        # overseas_lot スキーマに変換
        for raw in raw_lots:
            lot = _to_overseas_lot(raw, auction, fx_rate=fx_rate)
            if lot.get("lot_title"):
                all_lots.append(lot)

        logger.info(
            f"  [Heritage] {auction_name}: {len(raw_lots)}件取得 "
            f"→ {sum(1 for l in all_lots if l.get('auction_id') == auction.get('id'))}件変換"
        )
        time.sleep(REQUEST_DELAY)

    logger.info(f"  [Heritage] 合計: {len(all_lots)}件")
    return all_lots


# ── スタンドアロン実行 ────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Heritage Auctions ロット取得")
    parser.add_argument("--auction", nargs="+", help="取得するオークションID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pages", type=int, default=MAX_PAGES)
    parser.add_argument("--fx-rate", type=float, default=USD_JPY_RATE)
    args = parser.parse_args()

    lots = fetch_heritage_lots(
        auction_ids=args.auction,
        dry_run=args.dry_run,
        fx_rate=args.fx_rate,
        max_pages=args.pages,
    )

    print(f"\n取得結果: {len(lots)}件\n")
    for lot in lots[:5]:
        print(f"  [{lot.get('auction_id', '')[:30]}]")
        print(f"  Lot {lot.get('lot_number', '?'):>6}: {lot.get('lot_title', '')[:60]}")
        print(f"  価格: USD {lot.get('current_price', 0):,.0f} → ¥{lot.get('price_jpy', 0):,.0f}")
        print(f"  URL: {lot.get('lot_url', '')[:70]}")
        print()

    if not args.dry_run and lots:
        from candidates_writer import write_candidates
        result = write_candidates(lots, dry_run=False)
        print(f"daily_candidates 書き込み: {result}")
