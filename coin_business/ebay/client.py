"""
coin_business/ebay/client.py
==============================
eBay Browse API v1 クライアント。

責務:
  - eBay Browse API で listing を検索する
  - seed レコードから検索パラメータを生成する
  - API レスポンスを DB 保存用の正規化 dict に変換する
  - API 失敗時に全体を停止させない (空リスト返却 + ログ)

主要メソッド:
  search(query, limit, **filter_params)     → list[ListingItem]
  search_by_seed(seed_rec, limit)           → list[ListingItem]
  get_item(ebay_item_id)                    → Optional[ListingItem]

ListingItem は dict で、DB カラムと直接対応する:
  ebay_item_id, title, listing_url, listing_type,
  current_price_usd, currency, bid_count, end_time, start_time,
  seller_id, seller_username, seller_feedback_score,
  shipping_from_country, image_url, thumbnail_url,
  condition, is_active, raw_payload (元 API レスポンス全体)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from ebay.auth import EbayTokenManager

logger = logging.getLogger(__name__)

# ================================================================
# eBay Browse API エンドポイント
# ================================================================

BROWSE_SEARCH_URL  = "https://api.ebay.com/buy/browse/v1/item_summary/search"
BROWSE_ITEM_URL    = "https://api.ebay.com/buy/browse/v1/item/{item_id}"
MARKETPLACE_ID     = "EBAY_US"
COINS_CATEGORY_ID  = "11116"    # Coins & Paper Money
DEFAULT_PAGE_SIZE  = 50
MAX_PAGE_SIZE      = 200
REQUEST_TIMEOUT    = 30         # 秒
RATE_LIMIT_SLEEP   = 0.3        # リクエスト間隔 (秒)


# ================================================================
# EbayBrowseClient
# ================================================================

class EbayBrowseClient:
    """
    eBay Browse API v1 ラッパー。

    Usage:
        client = EbayBrowseClient()
        items = client.search("NGC MS63 Morgan Dollar", limit=50)
        for item in items:
            print(item["title"], item["current_price_usd"])

    Args:
        token_mgr: EbayTokenManager インスタンス (省略時は自動生成)
    """

    def __init__(self, token_mgr: EbayTokenManager | None = None):
        self._auth = token_mgr or EbayTokenManager()

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self._auth.is_configured

    # ----------------------------------------------------------------
    # 公開 API
    # ----------------------------------------------------------------

    def search(
        self,
        query:        str,
        limit:        int = DEFAULT_PAGE_SIZE,
        offset:       int = 0,
        category_ids: str = COINS_CATEGORY_ID,
        buying_options: str | None = None,   # "AUCTION" | "FIXED_PRICE" | None=both
        min_price:    float | None = None,
        max_price:    float | None = None,
        sort:         str = "endingSoonest",
    ) -> list[dict]:
        """
        eBay Browse API で検索して正規化 listing リストを返す。

        Args:
            query:         検索キーワード
            limit:         取得件数 (max 200)
            offset:        ページングオフセット
            category_ids:  カテゴリ ID (デフォルト: コイン)
            buying_options: オークション / 即決 フィルタ
            min_price:     最低価格 (USD)
            max_price:     最高価格 (USD)
            sort:          並び順 (endingSoonest / price / -price / newlyListed)

        Returns:
            list of ListingItem dict。API エラー時は空リスト。
        """
        if not self.is_configured:
            logger.warning("eBay API 未設定 — search をスキップ")
            return []

        params = {
            "q":            query,
            "limit":        str(min(limit, MAX_PAGE_SIZE)),
            "offset":       str(offset),
            "category_ids": category_ids,
            "sort":         sort,
        }

        # フィルタ文字列を構築
        filter_parts: list[str] = []
        if buying_options:
            filter_parts.append(f"buyingOptions:{{{buying_options}}}")
        if min_price is not None:
            filter_parts.append(f"price:[{min_price:.2f}..],priceCurrency:USD")
        if max_price is not None:
            filter_parts.append(f"price:[..{max_price:.2f}],priceCurrency:USD")
        if filter_parts:
            params["filter"] = ",".join(filter_parts)

        try:
            token = self._auth.get_token()
        except RuntimeError as exc:
            logger.error("eBay トークン取得失敗: %s", exc)
            return []

        try:
            resp = requests.get(
                BROWSE_SEARCH_URL,
                headers=self._headers(token),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            sc = exc.response.status_code if exc.response else "?"
            logger.warning("eBay Browse API HTTP エラー %s: %s", sc, exc)
            if sc == 429:
                logger.warning("Rate limit hit — 60 秒待機")
                time.sleep(60)
            return []
        except requests.exceptions.RequestException as exc:
            logger.warning("eBay Browse API ネットワークエラー: %s", exc)
            return []

        data = resp.json()
        raw_items = data.get("itemSummaries", [])
        logger.debug("eBay search '%s': %d 件取得 (total=%s)",
                     query[:60], len(raw_items), data.get("total", "?"))

        items: list[dict] = []
        for raw in raw_items:
            item = self._normalize_item(raw)
            if item:
                items.append(item)
        return items

    def search_all_pages(
        self,
        query:    str,
        max_items: int = 500,
        **kwargs,
    ) -> list[dict]:
        """
        全ページを取得して結合して返す。

        Args:
            query:     検索キーワード
            max_items: 最大取得件数 (eBay 上限 10000)
        """
        all_items: list[dict] = []
        offset    = 0
        page_size = min(MAX_PAGE_SIZE, max_items)

        while len(all_items) < max_items:
            batch = self.search(query, limit=page_size, offset=offset, **kwargs)
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < page_size:
                break   # 最終ページ
            offset    += page_size
            time.sleep(RATE_LIMIT_SLEEP)

        return all_items[:max_items]

    def search_by_seed(
        self,
        seed: dict,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict]:
        """
        yahoo_coin_seeds レコードのパラメータで eBay を検索する。

        seed の search_query を主キーにしつつ、
        cert_company / year_min / year_max / denomination を
        filter として追加する。

        Args:
            seed:  yahoo_coin_seeds の 1 レコード (dict)
            limit: 取得件数

        Returns:
            list of ListingItem dict
        """
        query = seed.get("search_query") or seed.get("yahoo_lot_id", "")
        if not query:
            logger.warning("seed の search_query が空 — スキップ seed_id=%s",
                           seed.get("id", "?"))
            return []

        # 価格フィルタ (ref_price の 50%〜300% を探索範囲とする)
        min_price: float | None = None
        max_price: float | None = None
        ref_price = seed.get("ref_price_jpy")
        if ref_price and isinstance(ref_price, (int, float)) and ref_price > 0:
            # 円→USD 概算 (150 円/ドル)
            ref_usd   = ref_price / 150
            min_price = max(1.0, round(ref_usd * 0.3, 2))
            max_price = round(ref_usd * 3.0, 2)

        return self.search(
            query     = query,
            limit     = limit,
            min_price = min_price,
            max_price = max_price,
        )

    def get_item(self, ebay_item_id: str) -> Optional[dict]:
        """
        item_id で単体 listing を取得する。

        Returns:
            ListingItem dict or None (エラー・未発見時)
        """
        if not self.is_configured:
            return None

        try:
            token = self._auth.get_token()
            url   = BROWSE_ITEM_URL.format(item_id=ebay_item_id)
            resp  = requests.get(
                url,
                headers = self._headers(token),
                timeout = REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning("eBay get_item %s 失敗: %s", ebay_item_id, exc)
            return None

        return self._normalize_item(resp.json())

    # ----------------------------------------------------------------
    # 内部ヘルパー
    # ----------------------------------------------------------------

    @staticmethod
    def _headers(token: str) -> dict:
        return {
            "Authorization":           f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            "Accept":                  "application/json",
        }

    @staticmethod
    def _normalize_item(raw: dict) -> Optional[dict]:
        """
        eBay Browse API の itemSummary を DB 保存用 dict に正規化する。

        マッピング:
          ebay_item_id       ← itemId
          title              ← title
          listing_url        ← itemWebUrl
          listing_type       ← buyingOptions[0]
          current_price_usd  ← price.value (USD)
          currency           ← price.currency
          bid_count          ← bidCount
          end_time           ← itemEndDate
          seller_id          ← seller.username
          seller_username    ← seller.username
          seller_feedback_score ← seller.feedbackScore
          shipping_from_country ← itemLocation.country
          image_url          ← image.imageUrl
          thumbnail_url      ← thumbnailImages[0].imageUrl (or image.imageUrl)
          condition          ← condition
          is_active          ← (end_time が未来なら True)
          raw_payload        ← raw dict 全体 (JSON)
        """
        ebay_item_id = raw.get("itemId", "")
        title        = (raw.get("title") or "").strip()
        if not ebay_item_id or not title:
            return None

        item: dict = {
            "ebay_item_id": ebay_item_id,
            "title":        title,
            "listing_url":  raw.get("itemWebUrl", ""),
            "raw_payload":  json.dumps(raw, ensure_ascii=False),
            "is_active":    True,
            "is_sold":      False,
        }

        # ── 価格
        price_info = raw.get("price", {})
        if price_info:
            currency = price_info.get("currency", "USD")
            item["currency"] = currency
            try:
                val = float(price_info.get("value", 0))
                if currency == "USD":
                    item["current_price_usd"] = val
                elif currency == "GBP":
                    item["current_price_usd"] = round(val * 1.27, 2)
                elif currency == "EUR":
                    item["current_price_usd"] = round(val * 1.09, 2)
                else:
                    item["current_price_usd"] = val
            except (ValueError, TypeError):
                pass

        # ── 落札価格が設定されていれば sold 扱い
        if raw.get("currentBidPrice"):
            try:
                item["current_price_usd"] = float(
                    raw["currentBidPrice"].get("value", item.get("current_price_usd", 0))
                )
            except (ValueError, TypeError):
                pass

        # ── 出品種別
        buying_options = raw.get("buyingOptions", [])
        if "AUCTION" in buying_options and "FIXED_PRICE" in buying_options:
            item["listing_type"] = "AuctionWithBIN"
        elif "AUCTION" in buying_options:
            item["listing_type"] = "Auction"
        elif "FIXED_PRICE" in buying_options:
            item["listing_type"] = "FixedPrice"
        elif "BEST_OFFER" in buying_options:
            item["listing_type"] = "BestOffer"
        else:
            item["listing_type"] = buying_options[0] if buying_options else "Unknown"

        # ── 入札数
        bid_count = raw.get("bidCount")
        if bid_count is not None:
            try:
                item["bid_count"] = int(bid_count)
            except (ValueError, TypeError):
                item["bid_count"] = 0
        else:
            item["bid_count"] = 0

        # ── 終了日時
        end_date = raw.get("itemEndDate", "")
        if end_date:
            item["end_time"] = end_date

        # ── 出品者
        seller = raw.get("seller", {})
        if seller:
            username = seller.get("username", "")
            item["seller_id"]              = username
            item["seller_username"]        = username
            item["seller_feedback_score"]  = int(seller.get("feedbackScore", 0) or 0)

        # ── 発送元
        location = raw.get("itemLocation", {})
        if location:
            item["shipping_from_country"] = location.get("country", "")

        # ── 画像
        image = raw.get("image", {})
        if image:
            item["image_url"]     = image.get("imageUrl", "")
            item["thumbnail_url"] = image.get("imageUrl", "")
        thumbs = raw.get("thumbnailImages", [])
        if thumbs:
            item["thumbnail_url"] = thumbs[0].get("imageUrl", item.get("thumbnail_url", ""))

        # ── コンディション
        condition = raw.get("condition", "")
        if condition:
            item["condition"] = condition

        return item
