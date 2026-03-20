"""eBay Browse API クライアント

eBay Browse API (v1) を使ってアクティブリスティングを検索する。
ボット検知を回避し、安定的にデータ取得できる公式API経由。

セットアップ:
    1. https://developer.ebay.com でDeveloper Account作成（無料・即日承認）
    2. Application作成 → Production keysetを取得
    3. .envにキーを追記:
        EBAY_CLIENT_ID=your_client_id
        EBAY_CLIENT_SECRET=your_client_secret

使い方:
    from scripts.ebay_api_client import EbayBrowseAPI

    api = EbayBrowseAPI()
    results = api.search("NGC coins", limit=50)
    for item in results["items"]:
        print(item["title"], item["price"])
"""

import os
import sys
import time
import base64
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


class EbayBrowseAPI:
    """eBay Browse API v1 クライアント"""

    TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    SCOPE = "https://api.ebay.com/oauth/api_scope"

    # カテゴリID
    COINS_CATEGORY = "11116"  # Coins & Paper Money > Coins:World + Coins:US

    def __init__(self):
        self.client_id = os.environ.get("EBAY_CLIENT_ID", "")
        self.client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
        self._token = None
        self._token_expires = None

        if not self.client_id or not self.client_secret:
            print("WARNING: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が .env に未設定")
            print("  eBay Developer Account を作成してキーを取得してください:")
            print("  https://developer.ebay.com")

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_token(self) -> str:
        """Client Credentials Grant でアクセストークン取得"""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        resp = requests.post(
            self.TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": self.SCOPE,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        expires_in = data.get("expires_in", 7200)
        self._token_expires = datetime.now() + timedelta(seconds=expires_in - 60)

        return self._token

    def search(self, query: str, limit: int = 50, offset: int = 0,
               category_ids: str = None,
               buying_options: str = None,
               sort: str = None,
               min_price: float = None,
               max_price: float = None) -> dict:
        """Browse API search

        Args:
            query: 検索キーワード
            limit: 1ページの件数（最大200）
            offset: オフセット（最大10000）
            category_ids: カテゴリID（カンマ区切り）
            buying_options: "FIXED_PRICE", "AUCTION", "BEST_OFFER" etc
            sort: "price", "-price", "newlyListed", "endingSoonest"
            min_price: 最低価格（USD）
            max_price: 最高価格（USD）

        Returns:
            {"items": [...], "total": int, "offset": int, "limit": int}
        """
        if not self.is_configured:
            return {"items": [], "total": 0, "error": "API keys not configured"}

        token = self._get_token()

        params = {
            "q": query,
            "limit": str(min(limit, 200)),
            "offset": str(offset),
        }

        if category_ids:
            params["category_ids"] = category_ids

        # Build filter string
        filters = []
        if buying_options:
            filters.append(f"buyingOptions:{{{buying_options}}}")
        if min_price is not None:
            filters.append(f"price:[{min_price}..],priceCurrency:USD")
        if max_price is not None:
            filters.append(f"price:[..{max_price}],priceCurrency:USD")

        if filters:
            params["filter"] = ",".join(filters)

        if sort:
            params["sort"] = sort

        try:
            resp = requests.get(
                self.SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    "Accept": "application/json",
                },
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return {"items": [], "total": 0, "error": str(e)}

        data = resp.json()
        total = data.get("total", 0)
        raw_items = data.get("itemSummaries", [])

        # 中間形式に変換
        items = []
        for raw in raw_items:
            item = self._convert_item(raw)
            if item:
                items.append(item)

        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_next": (offset + len(items)) < min(total, 10000),
        }

    def search_all(self, query: str, max_items: int = 1000, **kwargs) -> list[dict]:
        """全ページ取得"""
        all_items = []
        offset = 0
        page_size = 200

        while offset < max_items:
            result = self.search(query, limit=page_size, offset=offset, **kwargs)
            if result.get("error"):
                print(f"  API Error: {result['error']}")
                break

            items = result["items"]
            all_items.extend(items)

            if not result["has_next"] or not items:
                break

            offset += page_size
            time.sleep(0.5)  # API rate limit respect

        return all_items

    def _convert_item(self, raw: dict) -> dict | None:
        """API レスポンス → 共通形式に変換"""
        title = raw.get("title", "").strip()
        if not title:
            return None

        item = {
            "title": title,
            "item_id": raw.get("itemId", ""),
            "url": raw.get("itemWebUrl", ""),
        }

        # 価格
        price_info = raw.get("price", {})
        if price_info:
            try:
                value = float(price_info.get("value", 0))
                currency = price_info.get("currency", "USD")
                if currency == "USD":
                    item["price_usd"] = value
                    item["price_jpy"] = int(value * 150)  # 概算
                elif currency == "GBP":
                    item["price_usd"] = round(value * 1.27, 2)
                    item["price_jpy"] = int(value * 1.27 * 150)
                elif currency == "EUR":
                    item["price_usd"] = round(value * 1.09, 2)
                    item["price_jpy"] = int(value * 1.09 * 150)
                else:
                    item["price_usd"] = value
                    item["price_jpy"] = int(value * 150)
            except (ValueError, TypeError):
                pass

        if "price_jpy" not in item:
            return None

        # 入札情報
        item["bids"] = raw.get("bidCount", 0)
        buying_options = raw.get("buyingOptions", [])
        if "AUCTION" in buying_options:
            item["listing_type"] = "auction"
        elif "BEST_OFFER" in buying_options:
            item["listing_type"] = "best_offer"
        elif "FIXED_PRICE" in buying_options:
            item["listing_type"] = "buy_it_now"

        # 送料
        shipping = raw.get("shippingOptions", [{}])
        if shipping:
            ship_cost = shipping[0].get("shippingCost", {})
            if ship_cost:
                try:
                    ship_val = float(ship_cost.get("value", 0))
                    item["shipping_usd"] = ship_val
                    item["shipping_jpy"] = int(ship_val * 150)
                except (ValueError, TypeError):
                    pass
            ship_type = shipping[0].get("shippingCostType", "")
            if ship_type == "FREE":
                item["shipping"] = 0
                item["shipping_jpy"] = 0

        # 終了時刻
        end_date = raw.get("itemEndDate", "")
        if end_date:
            item["end_date"] = end_date
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                now = datetime.now(end_dt.tzinfo)
                delta = end_dt - now
                item["ends_in_hours"] = max(0, delta.total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

        # 出品者
        seller = raw.get("seller", {})
        if seller:
            item["seller"] = seller.get("username", "")
            item["seller_feedback_pct"] = seller.get("feedbackPercentage", "")
            item["seller_feedback_score"] = seller.get("feedbackScore", 0)

        # コンディション
        item["condition"] = raw.get("condition", "")

        # 画像
        image = raw.get("image", {})
        if image:
            item["thumbnail"] = image.get("imageUrl", "")

        # カテゴリ
        categories = raw.get("categories", [])
        if categories:
            item["category"] = categories[0].get("categoryName", "")

        return item


def test_connection():
    """接続テスト"""
    api = EbayBrowseAPI()
    if not api.is_configured:
        print("eBay API キーが未設定です。")
        print()
        print("セットアップ手順:")
        print("  1. https://developer.ebay.com でアカウント作成")
        print("  2. My Account > Application Access > Create a keyset")
        print("  3. .env に追記:")
        print("     EBAY_CLIENT_ID=your_app_id")
        print("     EBAY_CLIENT_SECRET=your_cert_id")
        return False

    print("eBay Browse API 接続テスト...")
    result = api.search("NGC coins", limit=3)

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return False

    print(f"OK: {result['total']} items found")
    for item in result["items"][:3]:
        print(f"  ${item.get('price_usd',0):.2f} | {item['title'][:60]}")

    return True


if __name__ == "__main__":
    test_connection()
