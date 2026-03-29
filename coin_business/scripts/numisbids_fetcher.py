"""
numisbids_fetcher.py  ─  NumisBids 経由のオークションロット取得

Noble Numismatics / Noonans Mayfair / Spink / SINCONA など、
NumisBids プラットフォームを利用する複数オークションハウスに対応。

取得フロー:
  1. auction_schedule.json から対象オークションを選択 (source_key=noble/noonans/spink)
  2. numisbids_sale_id が設定されていれば直接取得
     設定がなければ NumisBids home から sale_id を自動発見
  3. https://www.numisbids.com/sale/{SALE_ID}/?pg={PAGE} からロット一覧を取得
  4. overseas_lot スキーマに変換 → auction_cost_calculator でコスト付与

NumisBids URL パターン（実測済み）:
  ロット一覧: https://www.numisbids.com/sale/{SALE_ID}/?pg={PAGE}
              100件/ページ、ページネーションは data-next_page 属性で確認
  ロット詳細: https://www.numisbids.com/sale/{SALE_ID}/lot/{LOT_NO}

NumisBids ホームからの sale_id 発見:
  https://www.numisbids.com/n.php?p=home に全社の最新 sale_id が埋め込まれている
  /sale/{SALE_ID}/lot/{LOT_NO} パターンと社名画像 URL から対応関係を特定

使い方:
  from scripts.numisbids_fetcher import fetch_numisbids_lots

  lots = fetch_numisbids_lots(dry_run=False)
  lots = fetch_numisbids_lots(sources=['noble'], dry_run=True)
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
REQUEST_DELAY    = 2.0    # サーバー負荷軽減のための待機秒数
MAX_PAGES        = 15     # 1オークション最大ページ数 (Nobel等は700+ロット)
LOTS_PER_PAGE    = 100    # NumisBids 固定 100件/ページ

NUMISBIDS_BASE   = "https://www.numisbids.com"
NUMISBIDS_HOME   = f"{NUMISBIDS_BASE}/n.php?p=home"

# source_key → NumisBids 社名識別子 (media URL に含まれる)
SOURCE_TO_FIRM: dict[str, str] = {
    "noble":    "noble",
    "noonans":  "dnw",     # Dix Noonan Webb = Noonans の旧社名 (NumisBids URL)
    "spink":    "spink",
    "sincona":  "sincona",
}

# source_key → 通貨 (auction_fee_rules.json と一致させること)
SOURCE_CURRENCY: dict[str, str] = {
    "noble":    "AUD",
    "noonans":  "GBP",
    "spink":    "GBP",
    "sincona":  "CHF",
}

# source_key → デフォルト為替レート (暫定値 / fetch_daily_rates.py で更新予定)
DEFAULT_FX_RATES: dict[str, float] = {
    "noble":    95.0,    # AUD/JPY
    "noonans":  190.0,   # GBP/JPY
    "spink":    190.0,   # GBP/JPY
    "sincona":  170.0,   # CHF/JPY
}

NUMISBIDS_SOURCES = tuple(SOURCE_CURRENCY.keys())

# auction_house 表示名
AUCTION_HOUSE_NAME: dict[str, str] = {
    "noble":    "Noble Numismatics",
    "noonans":  "Noonans Mayfair",
    "spink":    "Spink",
    "sincona":  "SINCONA",
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


# ── sale_id 発見 ─────────────────────────────────────────────────

def discover_sale_ids(session: requests.Session) -> dict[str, str]:
    """
    NumisBids home から {source_key: sale_id} のマッピングを返す。

    home ページには現在開催中・直前の各社 sale が埋め込まれている。
    メディア URL パターン:
      //media.numisbids.com/sales/hosted/{FIRM_CODE}/{SALE_CODE}/thumb...

    href パターン:
      /sale/{SALE_ID}/lot/{LOT_NO}

    対応関係:
      ページ内の sale_id と firm コードが近接している箇所から抽出。
    """
    result: dict[str, str] = {}
    try:
        resp = session.get(NUMISBIDS_HOME, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"  [NumisBids discover] home {resp.status_code}")
            return result

        html = resp.text

        # /sale/SALE_ID/lot/XXX と //media.numisbids.com/sales/hosted/FIRM/SALE_CODE を対応付ける
        # HTML の近接位置から推定
        for source, firm in SOURCE_TO_FIRM.items():
            # firm コードの画像 URL を検索
            firm_img_pattern = rf'media\.numisbids\.com/sales/hosted/{re.escape(firm)}/([^/]+)/thumb'
            firm_matches = re.finditer(firm_img_pattern, html, re.IGNORECASE)

            for m in firm_matches:
                # 画像から前後 500 文字以内に /sale/{SALE_ID}/lot/ リンクがあるはず
                search_area = html[max(0, m.start() - 500): m.end() + 500]
                sale_m = re.search(r'/sale/(\d+)/lot/', search_area)
                if sale_m:
                    sale_id = sale_m.group(1)
                    if source not in result:
                        result[source] = sale_id
                        logger.debug(f"  [NumisBids discover] {source} → sale_id={sale_id}")
                    break

    except Exception as e:
        logger.warning(f"  [NumisBids discover] エラー: {e}")

    return result


def _get_sale_id(auction: dict, discovered: dict[str, str]) -> Optional[str]:
    """
    オークションエントリから sale_id を取得。
    優先順: auction 設定 > discovery > None
    """
    # auction_schedule.json に numisbids_sale_id が設定されている場合
    explicit = auction.get("numisbids_sale_id")
    if explicit:
        return str(explicit)

    # search_url に /sale/XXXXX/ パターンがある場合
    url = auction.get("search_url") or ""
    m = re.search(r"/sale/(\d+)", url)
    if m:
        return m.group(1)

    # 自動発見結果を使用
    source = auction.get("source_key") or auction.get("company") or ""
    return discovered.get(source)


# ── ページ取得 ────────────────────────────────────────────────────

def _fetch_page(
    session: requests.Session,
    sale_id: str,
    page: int = 1,
) -> Optional[str]:
    """
    NumisBids の1ページ分の HTML を取得。
    URL: https://www.numisbids.com/sale/{sale_id}/?pg={page}
    失敗時は None。
    """
    url = f"{NUMISBIDS_BASE}/sale/{sale_id}/"
    params = {}
    if page > 1:
        params["pg"] = str(page)

    try:
        resp = session.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"  [NumisBids] sale={sale_id} pg={page}: HTTP {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"  [NumisBids] sale={sale_id} pg={page}: {e}")
        return None


# ── HTML パース ───────────────────────────────────────────────────

def _has_next_page(html: str) -> Optional[int]:
    """
    HTML の data-next_page 属性から次ページ番号を取得。
    なければ None。

    NumisBids は以下の形式でページ情報を埋め込む:
      <div id="nb_nav_nextpage" ... data-next_page="2" ...></div>
    """
    m = re.search(r'id="nb_nav_nextpage"[^>]*data-next_page="(\d+)"', html)
    if m:
        return int(m.group(1))
    return None


def _parse_numisbids_lots(html: str, sale_id: str) -> list[dict]:
    """
    NumisBids /sale/SALE_ID/?pg=N ページからロット情報を抽出。

    HTML 構造（実測済み）:
      <span class="lot"><a href="/sale/SSSSS/lot/NNNNN">Lot NNNNN</a></span>
      <span class="estimate">Estimate: <span class="rateclick" ...>120 AUD</span></span>
      <span class="summary"><a href="/sale/SSSSS/lot/NNNNN">TITLE</a></span>

    Returns: 100件以下のロットリスト
    """
    lots: list[dict] = []

    # ロット番号
    lot_nos = re.findall(
        rf'<span class="lot"><a href="/sale/{re.escape(sale_id)}/lot/(\d+)">Lot',
        html,
    )

    # タイトル
    titles = re.findall(
        rf'<span class="summary"><a href="/sale/{re.escape(sale_id)}/lot/\d+">([^<]+)</a>',
        html,
    )

    # 見積価格 (例: "120 AUD", "500 GBP")
    # data-message 属性内に HTML が含まれるため [^>]* では失敗する
    # → <span class="estimate">Estimate: ... の後の最初の "数字 通貨" を取得
    estimates = re.findall(
        r'class="estimate">Estimate:.*?>(\d[\d,]*)\s+([A-Z]{3})</span>',
        html,
        re.DOTALL,
    )
    # タプル (value, currency) → "value CURRENCY" 形式に統一
    estimates_str = [f"{v} {c}" for v, c in estimates]

    # 結合
    count = min(len(lot_nos), len(titles))
    for i in range(count):
        lot_no = lot_nos[i]
        title  = titles[i].strip()

        # 価格パース: "120 AUD" → price=120.0, detected_currency="AUD"
        price  = 0.0
        detected_currency: Optional[str] = None
        if i < len(estimates_str):
            est_raw = estimates_str[i].strip()
            est_m = re.match(r"([0-9,\.]+)\s*([A-Z]{3})", est_raw)
            if est_m:
                try:
                    price = float(est_m.group(1).replace(",", ""))
                    detected_currency = est_m.group(2)
                except ValueError:
                    pass

        lots.append({
            "lot_number":          lot_no,
            "lot_title":           title,
            "lot_url":             f"{NUMISBIDS_BASE}/sale/{sale_id}/lot/{lot_no}",
            "current_price":       price,
            "detected_currency":   detected_currency,
        })

    if len(lot_nos) != len(titles):
        logger.debug(
            f"  [NumisBids parse] sale={sale_id}: "
            f"lot_nos={len(lot_nos)} titles={len(titles)} → {count}件使用"
        )

    return lots


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
    price_raw = float(raw.get("current_price") or 0)

    # 通貨: detected_currency (ページから) > SOURCE_CURRENCY デフォルト
    currency = raw.get("detected_currency") or SOURCE_CURRENCY.get(source, "USD")
    price_jpy = int(price_raw * fx_rate) if price_raw > 0 else 0

    lot: dict = {
        # ── 出所情報
        "source":         source,
        "auction_house":  AUCTION_HOUSE_NAME.get(source, source.title()),
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
        src = a.get("source_key") or a.get("company") or ""
        if src not in target_sources:
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

    # NumisBids home から sale_id を自動発見
    discovered = discover_sale_ids(session)
    if discovered:
        logger.info(f"  [NumisBids] 自動発見: {discovered}")
    time.sleep(REQUEST_DELAY)

    all_lots: list[dict] = []

    for auction in target_auctions:
        source    = auction.get("source_key") or auction.get("company") or "unknown"
        sale_id   = _get_sale_id(auction, discovered)
        fx_rate   = fx_rates.get(source) or DEFAULT_FX_RATES.get(source, 150.0)

        auction_name = auction.get("name", auction.get("id", ""))

        if not sale_id:
            logger.warning(
                f"  [NumisBids] sale_id 未取得: {auction.get('id')} — "
                f"auction_schedule.json に numisbids_sale_id を追加してください"
            )
            continue

        logger.info(
            f"  [NumisBids] {auction_name} "
            f"(source={source}, sale={sale_id}, fx={fx_rate}) 取得開始"
        )

        raw_lots: list[dict] = []
        page = 1

        while page <= max_pages:
            html = _fetch_page(session, sale_id, page)
            if not html:
                break

            page_lots = _parse_numisbids_lots(html, sale_id)
            raw_lots.extend(page_lots)
            logger.debug(f"    pg={page}: {len(page_lots)}件")

            # 次ページ確認
            next_pg = _has_next_page(html)
            if not next_pg or not page_lots:
                break

            page = next_pg
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
    parser.add_argument("--pages",   type=int, default=MAX_PAGES)
    parser.add_argument(
        "--discover", action="store_true",
        help="sale_id 自動発見のみ実行（取得なし）",
    )
    args = parser.parse_args()

    if args.discover:
        session = _session()
        found = discover_sale_ids(session)
        print("\n=== NumisBids sale_id 自動発見結果 ===")
        for src, sid in found.items():
            print(f"  {src:<12}: sale_id={sid}")
            print(f"             URL: https://www.numisbids.com/sale/{sid}/")
        if not found:
            print("  発見なし (開催中オークションがない可能性)")
        print("\n→ auction_schedule.json の numisbids_sale_id に追記してください")
        raise SystemExit(0)

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
        price_raw = lot.get("current_price", 0)
        currency  = lot.get("currency", "USD")
        price_jpy = lot.get("price_jpy", 0)
        cost_jpy  = lot.get("estimated_cost_jpy")
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
