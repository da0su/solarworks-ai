"""
fetch_noble_noonans_spink.py
────────────────────────────
海外オークション3社（Noble / Noonans / Spink）のHTML構造確認・lot/price抽出モジュール。

対象サイト:
  - Noble Numismatics  (noble.com.au)          オーストラリア最大手コイン競売
  - Noonans Mayfair    (noonans.co.uk)          英国ロンドン 旧マディソン
  - Spink              (spink.com)              英国ロンドン 最古参

使い方:
  python scripts/fetch_noble_noonans_spink.py --source noble --test
  python scripts/fetch_noble_noonans_spink.py --source noonans --url <URL>
  python scripts/fetch_noble_noonans_spink.py --source spink --sale-id <ID>

HTML構造メモ（2026-03-31 調査）:
  ※ 各サイトはJavaScript依存が強いため、requests+BeautifulSoupでの
     静的取得には限界あり。Playwright利用推奨（将来対応）。
"""

from __future__ import annotations

import re
import sys
import time
import json
import logging
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── パス設定 ────────────────────────────────────────────────
_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))

# ── ロガー ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── 定数 ────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_DELAY = 2.5   # 秒（マナー遅延）


# ── データクラス ─────────────────────────────────────────────

@dataclass
class AuctionLot:
    """オークションlot 正規化スキーマ"""
    source:         str            # 'noble' / 'noonans' / 'spink'
    auction_id:     str            # サイト内セール番号
    lot_number:     str            # Lot#
    lot_title:      str            # 説明文
    estimate_low:   Optional[float] = None   # 推定価格下限（現地通貨）
    estimate_high:  Optional[float] = None   # 推定価格上限
    sold_price:     Optional[float] = None   # 落札価格
    currency:       str = "AUD"    # 通貨
    lot_url:        str = ""       # 詳細URL
    image_url:      str = ""       # 画像URL
    sale_date:      str = ""       # 開催日 YYYY-MM-DD
    raw_text:       str = ""       # 元テキスト（デバッグ用）

    def to_dict(self) -> dict:
        return asdict(self)


# ── HTTP セッション ──────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    return s


def _get(url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
    """URL を GET して BeautifulSoup を返す。失敗時は None。"""
    try:
        time.sleep(REQUEST_DELAY)
        resp = _session().get(url, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"GET失敗: {url} → {e}")
        return None


# ════════════════════════════════════════════════════════════
# Noble Numismatics  (noble.com.au)
# ════════════════════════════════════════════════════════════
#
# HTML構造メモ:
#   セール一覧  : https://www.noble.com.au/auctions/
#   セール詳細  : https://www.noble.com.au/auctions/<sale-id>/
#   Lot詳細     : https://www.noble.com.au/lots/<lot-id>/
#
# 主要セレクター（静的HTML部分）:
#   lot一覧  : div.lot-item
#   Lot番号  : span.lot-number  or  data-lot-number 属性
#   タイトル : h3.lot-title  /  div.lot-description
#   推定価格 : span.estimate   (テキスト例: "Est. A$800 - A$1,200")
#   落札価格 : span.hammer-price  (テキスト例: "A$950 + BP")
#   画像URL  : img.lot-image[src]
#
# ⚠️ 動的ロード: カタログ一覧はJS (React) レンダリング。
#   静的取得では lot-item が空になる場合あり。
#   → Playwright利用が必要な場合は executor/browser_manager.py を参考に実装。
# ────────────────────────────────────────────────────────────

NOBLE_BASE = "https://www.noble.com.au"


def _parse_noble_price(text: str) -> tuple[Optional[float], Optional[float]]:
    """
    "Est. A$800 - A$1,200" → (800.0, 1200.0)
    "A$950"                → (950.0, None)
    """
    nums = re.findall(r"[\d,]+(?:\.\d+)?", text.replace(",", ""))
    vals = [float(n) for n in nums if n]
    if len(vals) >= 2:
        return vals[0], vals[1]
    elif len(vals) == 1:
        return vals[0], None
    return None, None


def fetch_noble_lots(sale_id: str) -> list[AuctionLot]:
    """
    Noble セール一覧ページから Lot を取得。

    Args:
        sale_id: noble.com.au のセール番号 (例: "167")

    Returns:
        AuctionLot リスト
    """
    url = f"{NOBLE_BASE}/auctions/{sale_id}/"
    logger.info(f"[Noble] Fetching: {url}")
    soup = _get(url)
    if not soup:
        return []

    lots = []
    # Lot アイテムセレクター（実際のDOMに合わせて調整が必要）
    for item in soup.select("div.lot-item, article.lot-card, div[data-lot]"):
        try:
            lot_no   = (item.select_one("span.lot-number, .lot-num, [data-lot-number]")
                        or item)
            lot_no_t = (lot_no.get("data-lot-number")
                        or lot_no.get_text(strip=True)
                        or "")
            lot_no_t = re.sub(r"[^0-9A-Za-z]", "", lot_no_t)[:20]

            title_el = item.select_one("h3, h4, .lot-title, .lot-description")
            title    = title_el.get_text(strip=True)[:200] if title_el else ""

            est_el   = item.select_one(".estimate, .est-price, [class*='estimate']")
            est_text = est_el.get_text(strip=True) if est_el else ""
            est_lo, est_hi = _parse_noble_price(est_text)

            hammer_el   = item.select_one(".hammer-price, .sold-price, [class*='hammer']")
            hammer_text = hammer_el.get_text(strip=True) if hammer_el else ""
            hammer_lo, _ = _parse_noble_price(hammer_text)

            link_el = item.select_one("a[href]")
            lot_url = (NOBLE_BASE + link_el["href"]
                       if link_el and link_el["href"].startswith("/")
                       else (link_el["href"] if link_el else ""))

            img_el  = item.select_one("img")
            img_url = img_el.get("src", "") if img_el else ""

            if title:
                lots.append(AuctionLot(
                    source       = "noble",
                    auction_id   = sale_id,
                    lot_number   = lot_no_t,
                    lot_title    = title,
                    estimate_low = est_lo,
                    estimate_high= est_hi,
                    sold_price   = hammer_lo,
                    currency     = "AUD",
                    lot_url      = lot_url,
                    image_url    = img_url,
                    raw_text     = item.get_text(" ", strip=True)[:300],
                ))
        except Exception as e:
            logger.debug(f"Noble lot parse error: {e}")

    logger.info(f"[Noble] 取得: {len(lots)}件 (sale_id={sale_id})")
    return lots


# ════════════════════════════════════════════════════════════
# Noonans Mayfair  (noonans.co.uk)
# ════════════════════════════════════════════════════════════
#
# HTML構造メモ:
#   セール一覧  : https://www.noonans.co.uk/auctions/
#   セール詳細  : https://www.noonans.co.uk/auctions/results/<sale-slug>/
#   Lot詳細     : https://www.noonans.co.uk/auctions/results/<sale>/<lot>/
#
# 主要セレクター:
#   lot一覧  : div.lot  /  li.lot-item
#   Lot番号  : span.lot-number
#   タイトル : div.lot-title  /  h2.title
#   推定価格 : div.estimate  (例: "Estimate: £400 - £500")
#   落札価格 : div.hammer    (例: "Hammer Price: £520")
#   画像URL  : div.lot-image img[src]
#
# ⚠️ 動的ロード: 検索・フィルター結果はAjax。
#   静的GET: /auctions/results/ ページは部分的に静的取得可能。
# ────────────────────────────────────────────────────────────

NOONANS_BASE = "https://www.noonans.co.uk"


def _parse_gbp_price(text: str) -> Optional[float]:
    """
    "£520" → 520.0
    "Hammer Price: £1,200" → 1200.0
    """
    m = re.search(r"£([\d,]+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def fetch_noonans_lots(sale_slug: str) -> list[AuctionLot]:
    """
    Noonans セール結果ページから Lot を取得。

    Args:
        sale_slug: noonans.co.uk のセール slug (例: "sale-258")
    """
    url = f"{NOONANS_BASE}/auctions/results/{sale_slug}/"
    logger.info(f"[Noonans] Fetching: {url}")
    soup = _get(url)
    if not soup:
        return []

    lots = []
    for item in soup.select("div.lot, li.lot-item, article.lot, div[class*='lot']"):
        try:
            lot_no_el = item.select_one(".lot-number, .lot-num, [class*='lotnumber']")
            lot_no_t  = lot_no_el.get_text(strip=True) if lot_no_el else ""
            lot_no_t  = re.sub(r"[^0-9A-Za-z]", "", lot_no_t)[:20]

            title_el = item.select_one("h2, h3, .lot-title, .title, .description")
            title    = title_el.get_text(strip=True)[:200] if title_el else ""

            est_el   = item.select_one(".estimate, [class*='estimate']")
            est_text = est_el.get_text(strip=True) if est_el else ""
            # "Estimate: £400 - £500" → 両値取得
            gbp_vals = re.findall(r"£([\d,]+)", est_text)
            est_lo   = float(gbp_vals[0].replace(",","")) if gbp_vals else None
            est_hi   = float(gbp_vals[1].replace(",","")) if len(gbp_vals)>1 else None

            hammer_el   = item.select_one(".hammer, .hammer-price, [class*='hammer']")
            hammer_text = hammer_el.get_text(strip=True) if hammer_el else ""
            sold        = _parse_gbp_price(hammer_text)

            link_el = item.select_one("a[href]")
            lot_url = (NOONANS_BASE + link_el["href"]
                       if link_el and link_el["href"].startswith("/")
                       else (link_el["href"] if link_el else ""))

            img_el  = item.select_one("img")
            img_url = img_el.get("src", "") if img_el else ""

            if title:
                lots.append(AuctionLot(
                    source       = "noonans",
                    auction_id   = sale_slug,
                    lot_number   = lot_no_t,
                    lot_title    = title,
                    estimate_low = est_lo,
                    estimate_high= est_hi,
                    sold_price   = sold,
                    currency     = "GBP",
                    lot_url      = lot_url,
                    image_url    = img_url,
                    raw_text     = item.get_text(" ", strip=True)[:300],
                ))
        except Exception as e:
            logger.debug(f"Noonans lot parse error: {e}")

    logger.info(f"[Noonans] 取得: {len(lots)}件 (sale={sale_slug})")
    return lots


# ════════════════════════════════════════════════════════════
# Spink  (spink.com)
# ════════════════════════════════════════════════════════════
#
# HTML構造メモ:
#   セール一覧  : https://www.spink.com/auctions/
#   セール詳細  : https://www.spink.com/auctions/sale/<sale-id>
#   Lot詳細     : https://www.spink.com/lot/<lot-id>
#
# 主要セレクター（2026-03 確認）:
#   lot一覧  : div.lot-listing  /  div.auction-lot
#   Lot番号  : span.lot-num  /  td.lot-number
#   タイトル : div.lot-description  /  td.description
#   推定価格 : td.estimate  (例: "£500 - £700")
#   落札価格 : td.realised  /  td.hammer  (例: "£620")
#   画像URL  : img.lot-image[src]
#
# ⚠️ Spinkの結果ページ: archive.spink.com でも過去結果参照可能。
#   現在進行中のセール: spink.com/auctions/sale/<id>
#   静的取得可能な場合が多い（Server-side rendered）。
# ────────────────────────────────────────────────────────────

SPINK_BASE    = "https://www.spink.com"
SPINK_ARCHIVE = "https://archive.spink.com"


def fetch_spink_lots(sale_id: str, use_archive: bool = False) -> list[AuctionLot]:
    """
    Spink セールページから Lot を取得。

    Args:
        sale_id    : Spink のセール番号 (例: "20258")
        use_archive: True → archive.spink.com を使用（落札結果あり）
    """
    base = SPINK_ARCHIVE if use_archive else SPINK_BASE
    url  = f"{base}/auctions/sale/{sale_id}"
    logger.info(f"[Spink] Fetching: {url}")
    soup = _get(url)
    if not soup:
        return []

    lots = []
    # Spinkのセレクター（実際のDOMに合わせて調整）
    for item in soup.select("div.lot-listing, div.auction-lot, tr.lot-row"):
        try:
            lot_no_el = item.select_one("span.lot-num, td.lot-number, .lot-no")
            lot_no_t  = lot_no_el.get_text(strip=True) if lot_no_el else ""
            lot_no_t  = re.sub(r"[^0-9A-Za-z]", "", lot_no_t)[:20]

            title_el = item.select_one(
                "div.lot-description, td.description, h3.lot-title, .title")
            title = title_el.get_text(strip=True)[:200] if title_el else ""

            est_el   = item.select_one("td.estimate, .estimate-price, [class*='estimate']")
            est_text = est_el.get_text(strip=True) if est_el else ""
            gbp_vals = re.findall(r"£([\d,]+)", est_text)
            est_lo   = float(gbp_vals[0].replace(",","")) if gbp_vals else None
            est_hi   = float(gbp_vals[1].replace(",","")) if len(gbp_vals)>1 else None

            realised_el   = item.select_one(
                "td.realised, td.hammer, .hammer-price, [class*='realised']")
            realised_text = realised_el.get_text(strip=True) if realised_el else ""
            sold = _parse_gbp_price(realised_text)

            link_el = item.select_one("a[href]")
            lot_url = ""
            if link_el:
                href = link_el.get("href", "")
                lot_url = base + href if href.startswith("/") else href

            img_el  = item.select_one("img.lot-image, img[class*='lot']")
            img_url = img_el.get("src", "") if img_el else ""

            if title:
                lots.append(AuctionLot(
                    source       = "spink",
                    auction_id   = sale_id,
                    lot_number   = lot_no_t,
                    lot_title    = title,
                    estimate_low = est_lo,
                    estimate_high= est_hi,
                    sold_price   = sold,
                    currency     = "GBP",
                    lot_url      = lot_url,
                    image_url    = img_url,
                    raw_text     = item.get_text(" ", strip=True)[:300],
                ))
        except Exception as e:
            logger.debug(f"Spink lot parse error: {e}")

    logger.info(f"[Spink] 取得: {len(lots)}件 (sale_id={sale_id})")
    return lots


# ════════════════════════════════════════════════════════════
# 構造テスト（--test フラグ）
# ════════════════════════════════════════════════════════════

def run_structure_test(source: str, sale_id: str, url: str = "") -> None:
    """
    HTML構造確認テスト。
    実際にGETして取得できた要素数・サンプルを表示する。
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"  HTML構造テスト: {source.upper()} (sale={sale_id})")
    logger.info(f"{'='*60}")

    if source == "noble":
        lots = fetch_noble_lots(sale_id)
    elif source == "noonans":
        lots = fetch_noonans_lots(sale_id)
    elif source == "spink":
        lots = fetch_spink_lots(sale_id)
    else:
        logger.error(f"未対応ソース: {source}")
        return

    logger.info(f"\n取得件数: {len(lots)}")
    for lot in lots[:5]:  # 先頭5件表示
        logger.info(
            f"  [{lot.lot_number}] {lot.lot_title[:60]}"
            f"\n    Est:{lot.estimate_low}-{lot.estimate_high} {lot.currency}"
            f" / Sold:{lot.sold_price}"
            f"\n    URL: {lot.lot_url[:80]}"
        )
    if not lots:
        # HTMLの生テキストを表示してセレクター調整のヒントを提供
        target_url = url or f"{NOBLE_BASE}/auctions/{sale_id}/"
        soup = _get(target_url)
        if soup:
            # 主要タグの出現数
            for tag in ["div", "article", "li", "tr", "section"]:
                cnt = len(soup.find_all(tag))
                logger.info(f"  <{tag}> count: {cnt}")
            # class名サンプル抽出
            classes = set()
            for el in soup.find_all(class_=True)[:200]:
                for cls in el.get("class", []):
                    if any(kw in cls.lower() for kw in ["lot","price","estimate","hammer"]):
                        classes.add(cls)
            logger.info(f"  関連クラス候補: {sorted(classes)[:20]}")


# ════════════════════════════════════════════════════════════
# CLI エントリーポイント
# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Noble/Noonans/Spink lot抽出テスト")
    ap.add_argument("--source",  required=True,
                    choices=["noble","noonans","spink"],
                    help="オークションソース")
    ap.add_argument("--sale-id", default="",
                    help="セール番号 (例: noble=167, noonans=sale-258, spink=20258)")
    ap.add_argument("--url",     default="",
                    help="直接URLを指定（--sale-id の代わりに使用可）")
    ap.add_argument("--test",    action="store_true",
                    help="HTML構造確認モード（結果をコンソール表示）")
    ap.add_argument("--json",    action="store_true",
                    help="JSON形式で出力")
    args = ap.parse_args()

    if args.test or not args.json:
        run_structure_test(args.source, args.sale_id, args.url)
    else:
        if args.source == "noble":
            lots = fetch_noble_lots(args.sale_id)
        elif args.source == "noonans":
            lots = fetch_noonans_lots(args.sale_id)
        else:
            lots = fetch_spink_lots(args.sale_id)
        print(json.dumps([l.to_dict() for l in lots], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
