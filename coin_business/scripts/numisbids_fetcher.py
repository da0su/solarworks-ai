"""
numisbids_fetcher.py  ─  NumisBids 経由のオークションロット取得

Noble Numismatics / Noonans Mayfair / Spink / SINCONA など、
NumisBids プラットフォームを利用する複数オークションハウスに対応。

取得フロー:
  1. auction_schedule.json から対象オークション(source_key=noble/noonans/spink/sincona)を選択
  2. NumisBids のオークション結果ページからロット一覧を取得
  3. overseas_lot スキーマに変換 → auction_cost_calculator でコスト付与

NumisBids URL パターン:
  ファーム一覧: https://www.numisbids.com/n.php?p=firmprofile&sid=XXXX
  ロット一覧:   https://www.numisbids.com/n.php?p=results&sid=SALE_ID&cid=0&lot=0&ord=1

使い方:
  from scripts.numisbids_fetcher import fetch_numisbids_lots

  lots = fetch_numisbids_lots(dry_run=False)
  lots = fetch_numisbids_lots(sources=["noble"], dry_run=True)
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
REQUEST_DELAY    = 2.5    # サーバー負荷軽減のための待機秒数
MAX_PAGES        = 10     # 1オークション最大ページ数
LOTS_PER_PAGE    = 100    # NumisBids 1ページ最大件数

# NumisBids ソース → auction_schedule.json の source_key マッピング
# 同一 source_key でも auction_id (sale番号) が異なる
NUMISBIDS_SOURCES = ("noble", "noonans", "spink", "sincona")

# NumisBids ロット検索エンドポイント
NUMISBIDS_BASE   = "https://www.numisbids.com"
NUMISBIDS_RESULTS = f"{NUMISBIDS_BASE}/n.php"

# source_key → 通貨マッピング (auction_fee_rules.json と一致させること)
SOURCE_CURRENCY: dict[str, str] = {
    "noble":    "AUD",
    "noonans":  "GBP",
    "spink":    "GBP",
    "sincona":  "CHF",
}

# source_key → デフォルト為替レート (暫定値 / fetch_daily_rates.py で更新予定)
DEFAULT_FX_RATES: dict[str, float] = {
    "noble":    95.0,   # AUD/JPY
    "noonans":  190.0,  # GBP/JPY
    "spink":    190.0,  # GBP/JPY
    "sincona":  170.0,  # CHF/JPY
}


# ── セッション ────────────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         NUMISBIDS_BASE,
    })
    return s


# ── sale_id 抽出 ──────────────────────────────────────────────────

def _extract_sale_id(auction: dict) -> Optional[str]:
    """
    auction エントリから NumisBids sale ID を抽出。

    search_url 例:
      https://www.numisbids.com/n.php?p=results&sid=4095  → "4095"
      https://www.numisbids.com/n.php?p=firmprofile&sid=3 → "3" (firm ID)

    sale番号が search_url にない場合は None。
    """
    url = auction.get("search_url") or auction.get("url") or ""

    # p=results&sid=XXXX パターン
    m = re.search(r"[?&]sid=(\d+)", url)
    if m:
        return m.group(1)

    return None


# ── ページ取得 ────────────────────────────────────────────────────

def _fetch_page(
    session: requests.Session,
    sale_id: str,
    page: int = 1,
) -> Optional[str]:
    """NumisBids の1ページ分の HTML を取得。失敗時は None。"""
    params: dict = {
        "p":   "results",
        "sid": sale_id,
        "cid": "0",
        "lot": "0",
        "ord": "1",
    }
    if page > 1:
        params["pg"] = str(page)

    try:
        resp = session.get(NUMISBIDS_RESULTS, params=params, timeout=20)
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"  [NumisBids] sid={sale_id} page={page}: HTTP {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"  [NumisBids] sid={sale_id} page={page}: {e}")
        return None


# ── HTML パース ───────────────────────────────────────────────────

def _parse_numisbids_lots(html: str, sale_id: str) -> list[dict]:
    """
    NumisBids の HTML からロット情報を抽出。

    NumisBids の典型的なロット構造:
      <tr class="lot-row"> または <div class="lot-item">
      ロット番号: <td class="lot-number"> or data-lot 属性
      タイトル:   <td class="lot-description"> or <a class="lot-title">
      価格:       <td class="lot-price"> or <span class="current-bid">
      URL:        <a href="/n.php?p=lot&sid=XXXX&lot=YY">
    """
    lots: list[dict] = []

    # ── JSON-LD を試みる (NumisBids 一部ページ)
    json_ld_blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    for block in json_ld_blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict) and data.get("@type") in ("ItemList", "AuctionEvent"):
                items = data.get("itemListElement") or data.get("items") or []
                for item in items:
                    lot = _normalize_json_lot(item, sale_id)
                    if lot:
                        lots.append(lot)
        except (json.JSONDecodeError, KeyError):
            pass

    if lots:
        logger.debug(f"  [NumisBids HTML JSON-LD] sid={sale_id}: {len(lots)}件")
        return lots

    # ── HTML テーブル行パース
    # NumisBids は典型的に <tr> ベースのテーブルでロットを列挙する
    row_pattern = re.compile(
        r'<tr[^>]*class="[^"]*(?:lot-row|result-row|auction-lot)[^"]*"[^>]*>(.*?)</tr>',
        re.DOTALL | re.IGNORECASE,
    )

    for row_m in row_pattern.finditer(html):
        row = row_m.group(1)
        lot = _parse_lot_row(row, sale_id)
        if lot:
            lots.append(lot)

    if lots:
        logger.debug(f"  [NumisBids HTML table] sid={sale_id}: {len(lots)}件")
        return lots

    # ── fallback: ロットリンクとタイトルを抽出
    lot_links = re.findall(
        r'href="(/n\.php\?p=lot&(?:amp;)?sid=\d+&(?:amp;)?lot=(\d+)[^"]*)"[^>]*>'
        r'([^<]{5,120})<',
        html,
    )
    seen: set[str] = set()
    for href, lot_no, title in lot_links:
        title = title.strip()
        if not title or lot_no in seen:
            continue
        seen.add(lot_no)

        # コイン関連キーワードチェック（ノイズ除去）
        title_upper = title.upper()
        is_coin = any(
            kw in title_upper
            for kw in ["NGC", "PCGS", "MS", "PF", "GOLD", "SILVER", "SOVEREIGN",
                        "COIN", "PENNY", "FRANC", "POUND", "DOLLAR", "THALER",
                        "FLORIN", "DUCAT", "CROWN"]
        )
        if not is_coin:
            continue

        href_clean = href.replace("&amp;", "&")
        lots.append({
            "lot_number":    lot_no,
            "lot_title":     title,
            "lot_url":       f"{NUMISBIDS_BASE}{href_clean}",
            "current_price": 0.0,
        })

    logger.debug(f"  [NumisBids HTML fallback] sid={sale_id}: {len(lots)}件")
    return lots


def _parse_lot_row(row_html: str, sale_id: str) -> Optional[dict]:
    """テーブル行 HTML から1ロット分の情報を抽出。"""
    # タイトル
    title_m = re.search(
        r'<(?:td|div|a)[^>]+class="[^"]*(?:lot-description|lot-title|description)[^"]*"[^>]*>'
        r'(?:<[^>]+>)*\s*(.*?)\s*(?:</[^>]+>)*\s*</(?:td|div|a)>',
        row_html, re.DOTALL | re.IGNORECASE,
    )
    if not title_m:
        # <a href> のテキストからタイトルを試みる
        a_m = re.search(r'href="[^"]*p=lot[^"]*"[^>]*>([^<]{5,120})<', row_html)
        if not a_m:
            return None
        title = a_m.group(1).strip()
    else:
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()

    if not title or len(title) < 5:
        return None

    # ロット番号
    lot_no_m = re.search(
        r'<(?:td|div|span)[^>]+class="[^"]*lot-(?:number|no|num)[^"]*"[^>]*>'
        r'(?:<[^>]+>)*\s*(\d+)\s*',
        row_html, re.IGNORECASE,
    )
    if not lot_no_m:
        lot_no_m = re.search(r'&lot=(\d+)', row_html)
    lot_no = lot_no_m.group(1) if lot_no_m else ""

    # 現在入札額 / 推定価格
    price_m = re.search(
        r'<(?:td|div|span)[^>]+class="[^"]*(?:lot-price|current-bid|estimate|hammer)[^"]*"[^>]*>'
        r'[^0-9]*([0-9][0-9,\.]+)',
        row_html, re.IGNORECASE,
    )
    price = 0.0
    if price_m:
        try:
            price = float(price_m.group(1).replace(",", "").replace(".", "", price_m.group(1).count(".") - 1))
        except (ValueError, AttributeError):
            pass

    # URL
    url_m = re.search(r'href="(/n\.php\?p=lot[^"]+)"', row_html, re.IGNORECASE)
    url = ""
    if url_m:
        url = f"{NUMISBIDS_BASE}{url_m.group(1).replace('&amp;', '&')}"

    return {
        "lot_number":    lot_no,
        "lot_title":     title,
        "lot_url":       url,
        "current_price": price,
    }


def _normalize_json_lot(item: dict, sale_id: str) -> Optional[dict]:
    """JSON-LD オブジェクトから overseas_lot 用の基本フィールドを抽出。"""
    if not isinstance(item, dict):
        return None

    title = (
        item.get("name") or item.get("title") or
        item.get("item", {}).get("name") or ""
    )
    title = re.sub(r"<[^>]+>", "", str(title)).strip()
    if not title:
        return None

    url = item.get("url") or item.get("lotUrl") or ""
    lot_no = str(item.get("lotNumber") or item.get("lot") or item.get("position") or "")

    price_raw = (
        item.get("currentBid") or item.get("lowEstimate") or
        item.get("offers", {}).get("lowPrice") or
        item.get("price", {}).get("value") or 0
    )
    try:
        price = float(str(price_raw).replace(",", "").replace(" ", "")) if price_raw else 0.0
    except (ValueError, TypeError):
        price = 0.0

    return {
        "lot_number":    lot_no,
        "lot_title":     title,
        "lot_url":       url,
        "current_price": price,
    }


# ── ページ数カウント ─────────────────────────────────────────────

def _get_total_pages(html: str) -> Optional[int]:
    """
    NumisBids の HTML からトータルページ数を取得。
    "Page 1 of 3" / "1/3" などのパターンに対応。
    """
    m = re.search(
        r'[Pp]age\s+\d+\s+of\s+(\d+)|(\d+)\s*/\s*(\d+)\s*[Pp]age',
        html,
    )
    if m:
        return int(m.group(1) or m.group(3))

    # ページナビゲーションから推定
    page_links = re.findall(r'[?&]pg=(\d+)', html)
    if page_links:
        return max(int(p) for p in page_links)

    return None


# ── lots → overseas_lot スキーマ変換 ─────────────────────────────

def _to_overseas_lot(
    raw: dict,
    auction: dict,
    fx_rate: float,
    source: str,
) -> dict:
    """
    NumisBids から取得した生ロットデータを overseas_lot 標準スキーマに変換。
    コスト計算は auction_cost_calculator.enrich_lot_with_cost() に委譲。
    """
    price_raw  = float(raw.get("current_price") or 0)
    price_jpy  = int(price_raw * fx_rate) if price_raw > 0 else 0
    currency   = SOURCE_CURRENCY.get(source, "USD")

    # auction_schedule.json の display_name を使う
    auction_house_map: dict[str, str] = {
        "noble":    "Noble Numismatics",
        "noonans":  "Noonans Mayfair",
        "spink":    "Spink",
        "sincona":  "SINCONA",
    }

    lot: dict = {
        # ── 出所情報
        "source":         source,
        "auction_house":  auction_house_map.get(source, source.title()),
        "auction_id":     auction.get("id", ""),
        "auction_name":   auction.get("name", ""),

        # ── ロット情報
        "lot_number":     raw.get("lot_number") or "",
        "lot_title":      raw.get("lot_title") or "",
        "lot_url":        raw.get("lot_url") or "",

        # ── 価格情報
        "current_price":  price_raw,
        "realized_price": None,
        "currency":       currency,
        "price_jpy":      price_jpy,
        "fx_rate":        fx_rate,

        # ── 時間情報
        "start_date":     auction.get("start_date"),
        "end_date":       auction.get("end_date"),
        "lot_end_time":   None,

        # ── マッチング (初期値: 未照合)
        "coin_match_status": "unmatched",
        "management_no":     None,
        "match_score":       None,

        # ── 判定 (candidates_writer で設定)
        "judgment":        "pending",
        "judgment_reason": None,
        "buy_limit_jpy":   None,

        # ── 運用メタ
        "priority":          auction.get("priority", 1),
        "is_active_auction": auction.get("_status") in (STATUS_ACTIVE, STATUS_IMMINENT),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "status":            "pending",

        # ── dedup (candidates_writer で生成)
        "dedup_key": None,
    }

    # auction_fee_rules.json に基づくコスト計算
    # Noble/Noonans/Spink/SINCONA は ceo_confirmed=False → estimated_cost_jpy=None
    lot = enrich_lot_with_cost(lot, fx_rate=fx_rate, require_confirmed=True)
    return lot


# ── メイン取得関数 ────────────────────────────────────────────────

def fetch_numisbids_lots(
    sources: Optional[list[str]] = None,
    auction_ids: Optional[list[str]] = None,
    dry_run: bool = False,
    fx_rates: Optional[dict[str, float]] = None,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """
    NumisBids 経由のオークション（Noble / Noonans / Spink / SINCONA）から
    ロット一覧を取得し、overseas_lot スキーマのリストで返す。

    Args:
        sources     : 取得するソース ('noble', 'noonans', 'spink', 'sincona')
                      None の場合は全 NUMISBIDS_SOURCES を対象に
        auction_ids : 特定の auction_id に絞る場合に指定
        dry_run     : True の場合、取得のみで DB書き込み不要を示す
        fx_rates    : {'noble': 95.0, 'noonans': 190.0, ...} 形式の為替レート
                      None の場合は DEFAULT_FX_RATES を使用
        max_pages   : 1オークション当たりの最大ページ数

    Returns:
        list[dict]: overseas_lot スキーマのリスト
    """
    if fx_rates is None:
        fx_rates = {}

    target_sources = sources or list(NUMISBIDS_SOURCES)
    session = _session()

    # 対象オークションを決定
    all_auctions = get_all_auctions_with_status()
    target_auctions = []
    for a in all_auctions:
        source = a.get("source_key") or a.get("company") or ""
        if source not in target_sources:
            continue
        status = a.get("_status")
        if status not in (STATUS_ACTIVE, STATUS_IMMINENT):
            continue
        if auction_ids and a.get("id") not in auction_ids:
            continue
        target_auctions.append(a)

    if not target_auctions:
        logger.info(f"  [NumisBids] 対象オークションなし (sources={target_sources})")
        return []

    logger.info(f"  [NumisBids] 取得対象: {len(target_auctions)}件")

    all_lots: list[dict] = []

    for auction in target_auctions:
        source    = auction.get("source_key") or auction.get("company") or "unknown"
        sale_id   = _extract_sale_id(auction)
        fx_rate   = fx_rates.get(source) or DEFAULT_FX_RATES.get(source, 150.0)

        auction_name = auction.get("name", auction.get("id", ""))

        if not sale_id:
            logger.warning(
                f"  [NumisBids] sale_id 取得失敗: {auction.get('id')} "
                f"(search_url に ?sid=XXXX が必要)"
            )
            continue

        logger.info(
            f"  [NumisBids] {auction_name} "
            f"(source={source}, sid={sale_id}, fx={fx_rate}) 取得開始"
        )

        raw_lots: list[dict] = []
        total_pages: Optional[int] = None

        for page in range(1, max_pages + 1):
            html = _fetch_page(session, sale_id, page)
            if not html:
                break

            # 初回ページでトータルページ数を取得
            if page == 1:
                total_pages = _get_total_pages(html)
                if total_pages:
                    logger.debug(f"    総ページ数: {total_pages}")

            page_lots = _parse_numisbids_lots(html, sale_id)
            raw_lots.extend(page_lots)
            logger.debug(f"    page {page}: {len(page_lots)}件")

            # 最終ページ判定
            if page_lots and len(page_lots) < LOTS_PER_PAGE:
                break
            if total_pages and page >= total_pages:
                break
            if not page_lots:
                break

            time.sleep(REQUEST_DELAY)

        # overseas_lot スキーマに変換
        auction_lots: list[dict] = []
        for raw in raw_lots:
            lot = _to_overseas_lot(raw, auction, fx_rate=fx_rate, source=source)
            if lot.get("lot_title"):
                auction_lots.append(lot)

        all_lots.extend(auction_lots)
        logger.info(
            f"  [NumisBids] {auction_name}: {len(raw_lots)}件取得 "
            f"→ {len(auction_lots)}件変換"
        )
        time.sleep(REQUEST_DELAY)

    logger.info(f"  [NumisBids] 合計: {len(all_lots)}件")
    return all_lots


# ── スタンドアロン実行 ────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="NumisBids ロット取得")
    parser.add_argument(
        "--source", nargs="+",
        choices=list(NUMISBIDS_SOURCES) + ["all"],
        default=["all"],
        help="取得するオークションソース (default: all)",
    )
    parser.add_argument("--auction", nargs="+", help="取得するオークションID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pages", type=int, default=MAX_PAGES)
    args = parser.parse_args()

    sources = None if "all" in args.source else args.source

    lots = fetch_numisbids_lots(
        sources=sources,
        auction_ids=args.auction,
        dry_run=args.dry_run,
        max_pages=args.pages,
    )

    print(f"\n取得結果: {len(lots)}件\n")
    for lot in lots[:5]:
        print(f"  [{lot.get('auction_id', '')[:35]}]")
        print(f"  Lot {lot.get('lot_number', '?'):>6}: {lot.get('lot_title', '')[:60]}")
        price_raw = lot.get('current_price', 0)
        currency  = lot.get('currency', 'USD')
        price_jpy = lot.get('price_jpy', 0)
        cost_jpy  = lot.get('estimated_cost_jpy')
        print(
            f"  価格: {currency} {price_raw:,.0f} → ¥{price_jpy:,.0f}"
            + (f"  [仕入コスト: ¥{cost_jpy:,.0f}]" if cost_jpy else "  [仕入コスト: CEO未承認]")
        )
        print(f"  URL: {lot.get('lot_url', '')[:70]}")
        print()

    if not args.dry_run and lots:
        sys.path.insert(0, str(Path(__file__).parent))
        from candidates_writer import write_candidates
        result = write_candidates(lots, dry_run=False)
        print(f"daily_candidates 書き込み: {result}")
