"""
coin_business/tests/test_ebay_client.py
=========================================
EbayBrowseClient._normalize_item() のマッピングテストと
ebay_repo の snapshot 冪等性テスト。

テスト構成:
  TestNormalizeItem         (14) - _normalize_item() の各フィールドマッピング
  TestNormalizeItemEdge     ( 6) - 欠損値・型変換のエッジケース
  TestListingTypeMapping    ( 5) - buyingOptions → listing_type 変換
  TestSearchBySeedPriceCalc ( 4) - search_by_seed の価格フィルタ計算
  TestEbayClientUnconfigured( 3) - API 未設定時の挙動
  TestSnapshotDiff          ( 5) - insert_snapshot の差分計算
  TestSnapshotIdempotency   ( 3) - snapshot は常に INSERT (重複しない)
  TestIngestResultStatus    ( 4) - IngestResult.status_str() / ok / 集計
  TestIngestDryRun          ( 3) - dry_run=True は DB を呼ばない

合計 47 テスト
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ebay.client import EbayBrowseClient
from scripts.ebay_api_ingest import IngestResult, run_ingest


# ================================================================
# Fixtures / helpers
# ================================================================

def _make_raw(
    item_id:        str  = "v1|123456|0",
    title:          str  = "1921 Morgan Silver Dollar NGC MS63",
    price_value:    str  = "250.00",
    price_currency: str  = "USD",
    end_date:       str  = "2026-04-10T12:00:00.000Z",
    buying_options: list | None = None,
    bid_count:      int | None  = None,
    seller_username:str  = "coin_dealer_us",
    country:        str  = "US",
) -> dict:
    raw: dict = {
        "itemId":     item_id,
        "title":      title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {
            "value":    price_value,
            "currency": price_currency,
        },
        "itemEndDate": end_date,
        "buyingOptions": buying_options or ["FIXED_PRICE"],
        "seller": {
            "username":      seller_username,
            "feedbackScore": 1234,
        },
        "itemLocation": {"country": country},
        "image":          {"imageUrl": "https://i.ebayimg.com/img1.jpg"},
        "thumbnailImages": [{"imageUrl": "https://i.ebayimg.com/thumb1.jpg"}],
        "condition": "Very Fine",
    }
    if bid_count is not None:
        raw["bidCount"] = bid_count
    return raw


# ================================================================
# TestNormalizeItem (14)
# ================================================================

class TestNormalizeItem:
    def test_ebay_item_id_mapped(self):
        raw  = _make_raw(item_id="v1|999|0")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["ebay_item_id"] == "v1|999|0"

    def test_title_mapped_and_stripped(self):
        raw  = _make_raw(title="  1921 Morgan  ")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["title"] == "1921 Morgan"

    def test_listing_url_mapped(self):
        raw  = _make_raw(item_id="v1|111|0")
        item = EbayBrowseClient._normalize_item(raw)
        assert "ebay.com/itm/v1|111|0" in item["listing_url"]

    def test_current_price_usd_float(self):
        raw  = _make_raw(price_value="375.50", price_currency="USD")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["current_price_usd"] == 375.50

    def test_currency_stored(self):
        raw  = _make_raw(price_currency="USD")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["currency"] == "USD"

    def test_end_time_mapped(self):
        raw  = _make_raw(end_date="2026-04-10T12:00:00.000Z")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["end_time"] == "2026-04-10T12:00:00.000Z"

    def test_seller_username_mapped(self):
        raw  = _make_raw(seller_username="top_seller_99")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["seller_username"] == "top_seller_99"

    def test_seller_id_same_as_username(self):
        raw  = _make_raw(seller_username="top_seller_99")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["seller_id"] == item["seller_username"]

    def test_seller_feedback_score_int(self):
        raw  = _make_raw()
        item = EbayBrowseClient._normalize_item(raw)
        assert item["seller_feedback_score"] == 1234

    def test_shipping_from_country_mapped(self):
        raw  = _make_raw(country="US")
        item = EbayBrowseClient._normalize_item(raw)
        assert item["shipping_from_country"] == "US"

    def test_image_url_mapped(self):
        raw  = _make_raw()
        item = EbayBrowseClient._normalize_item(raw)
        assert item["image_url"] == "https://i.ebayimg.com/img1.jpg"

    def test_thumbnail_url_from_thumbnailImages(self):
        raw  = _make_raw()
        item = EbayBrowseClient._normalize_item(raw)
        assert item["thumbnail_url"] == "https://i.ebayimg.com/thumb1.jpg"

    def test_condition_mapped(self):
        raw  = _make_raw()
        item = EbayBrowseClient._normalize_item(raw)
        assert item["condition"] == "Very Fine"

    def test_raw_payload_is_json_string_containing_itemId(self):
        raw  = _make_raw(item_id="v1|777|0")
        item = EbayBrowseClient._normalize_item(raw)
        payload = json.loads(item["raw_payload"])
        assert payload["itemId"] == "v1|777|0"


# ================================================================
# TestNormalizeItemEdge (6)
# ================================================================

class TestNormalizeItemEdge:
    def test_returns_none_when_no_item_id(self):
        raw = _make_raw()
        raw.pop("itemId", None)
        assert EbayBrowseClient._normalize_item(raw) is None

    def test_returns_none_when_empty_title(self):
        raw = _make_raw(title="   ")
        assert EbayBrowseClient._normalize_item(raw) is None

    def test_bid_count_zero_when_missing(self):
        raw = _make_raw()
        raw.pop("bidCount", None)
        item = EbayBrowseClient._normalize_item(raw)
        assert item["bid_count"] == 0

    def test_bid_count_mapped_when_present(self):
        raw = _make_raw(bid_count=12)
        item = EbayBrowseClient._normalize_item(raw)
        assert item["bid_count"] == 12

    def test_gbp_converted_to_usd(self):
        raw = _make_raw(price_value="100.00", price_currency="GBP")
        item = EbayBrowseClient._normalize_item(raw)
        # GBP * 1.27 = 127.00
        assert item["current_price_usd"] == pytest.approx(127.00, rel=1e-2)

    def test_eur_converted_to_usd(self):
        raw = _make_raw(price_value="100.00", price_currency="EUR")
        item = EbayBrowseClient._normalize_item(raw)
        # EUR * 1.09 = 109.00
        assert item["current_price_usd"] == pytest.approx(109.00, rel=1e-2)


# ================================================================
# TestListingTypeMapping (5)
# ================================================================

class TestListingTypeMapping:
    def _listing_type(self, buying_options: list) -> str:
        raw  = _make_raw(buying_options=buying_options)
        item = EbayBrowseClient._normalize_item(raw)
        return item["listing_type"]

    def test_fixed_price(self):
        assert self._listing_type(["FIXED_PRICE"]) == "FixedPrice"

    def test_auction(self):
        assert self._listing_type(["AUCTION"]) == "Auction"

    def test_auction_with_bin(self):
        assert self._listing_type(["AUCTION", "FIXED_PRICE"]) == "AuctionWithBIN"

    def test_best_offer(self):
        assert self._listing_type(["BEST_OFFER"]) == "BestOffer"

    def test_unknown_falls_back_to_first(self):
        assert self._listing_type(["SOME_OTHER"]) == "SOME_OTHER"


# ================================================================
# TestSearchBySeedPriceCalc (4)
# ================================================================

class TestSearchBySeedPriceCalc:
    """search_by_seed が ref_price_jpy から正しい USD 価格レンジを計算する。"""

    def _get_search_call_params(self, seed: dict):
        """EbayBrowseClient.search() に渡された kwargs を返す。"""
        client = EbayBrowseClient.__new__(EbayBrowseClient)
        captured = {}

        def mock_search(**kwargs):
            captured.update(kwargs)
            return []

        client.search = mock_search  # type: ignore
        client.search_by_seed(seed, limit=10)
        return captured

    def test_price_range_from_ref_price_jpy(self):
        seed = {"search_query": "NGC MS63 Morgan", "ref_price_jpy": 30000}
        # 30000 / 150 = 200 USD ref
        # min = max(1.0, 200 * 0.3) = 60.0
        # max = 200 * 3.0 = 600.0
        params = self._get_search_call_params(seed)
        assert params["min_price"] == pytest.approx(60.0, rel=1e-2)
        assert params["max_price"] == pytest.approx(600.0, rel=1e-2)

    def test_min_price_at_least_1_usd(self):
        seed = {"search_query": "test coin", "ref_price_jpy": 100}
        # 100 / 150 ≈ 0.67 USD; 0.67 * 0.3 ≈ 0.20 → max(1.0, 0.20) = 1.0
        params = self._get_search_call_params(seed)
        assert params["min_price"] >= 1.0

    def test_no_price_filter_when_ref_price_zero(self):
        seed = {"search_query": "test coin", "ref_price_jpy": 0}
        params = self._get_search_call_params(seed)
        assert params.get("min_price") is None
        assert params.get("max_price") is None

    def test_no_price_filter_when_ref_price_missing(self):
        seed = {"search_query": "test coin"}
        params = self._get_search_call_params(seed)
        assert params.get("min_price") is None
        assert params.get("max_price") is None


# ================================================================
# TestEbayClientUnconfigured (3)
# ================================================================

class TestEbayClientUnconfigured:
    def _unconfigured_client(self) -> EbayBrowseClient:
        client = EbayBrowseClient.__new__(EbayBrowseClient)
        auth   = MagicMock()
        auth.is_configured = False
        client._auth = auth
        return client

    def test_search_returns_empty_list_when_unconfigured(self):
        client = self._unconfigured_client()
        result = client.search("Morgan Dollar")
        assert result == []

    def test_get_item_returns_none_when_unconfigured(self):
        client = self._unconfigured_client()
        result = client.get_item("v1|123|0")
        assert result is None

    def test_is_configured_false(self):
        client = self._unconfigured_client()
        assert client.is_configured is False


# ================================================================
# TestSnapshotDiff (5)
# ================================================================

class TestSnapshotDiff:
    """insert_snapshot が差分計算を正しく行う。"""

    def _mock_client(self):
        c = MagicMock()
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return c

    def test_price_delta_calculated_when_prev_exists(self):
        from db.ebay_repo import insert_snapshot
        client = self._mock_client()
        item = {"current_price_usd": 300.0, "is_active": True, "is_sold": False, "bid_count": 5}
        prev = {"current_price_usd": 250.0, "bid_count": 3}
        insert_snapshot(client, "listing-uuid", "v1|100|0", item, prev)
        snap = client.table.return_value.insert.call_args[0][0]
        assert snap["price_delta_usd"] == pytest.approx(50.0)

    def test_bid_delta_calculated_when_prev_exists(self):
        from db.ebay_repo import insert_snapshot
        client = self._mock_client()
        item = {"current_price_usd": 300.0, "is_active": True, "is_sold": False, "bid_count": 7}
        prev = {"current_price_usd": 300.0, "bid_count": 4}
        insert_snapshot(client, "listing-uuid", "v1|100|0", item, prev)
        snap = client.table.return_value.insert.call_args[0][0]
        assert snap["bid_delta"] == 3

    def test_no_delta_when_no_prev(self):
        from db.ebay_repo import insert_snapshot
        client = self._mock_client()
        item = {"current_price_usd": 300.0, "is_active": True, "is_sold": False, "bid_count": 5}
        insert_snapshot(client, "listing-uuid", "v1|100|0", item, prev=None)
        snap = client.table.return_value.insert.call_args[0][0]
        assert "price_delta_usd" not in snap
        assert "bid_delta" not in snap

    def test_time_left_seconds_calculated_from_future_end_time(self):
        from db.ebay_repo import insert_snapshot
        from datetime import datetime, timezone, timedelta
        client = self._mock_client()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        item = {"current_price_usd": 300.0, "is_active": True, "is_sold": False,
                "bid_count": 0, "end_time": future}
        insert_snapshot(client, "listing-uuid", "v1|100|0", item)
        snap = client.table.return_value.insert.call_args[0][0]
        # 2 時間 = 7200 秒 ±60 秒の誤差を許容
        assert 7100 <= snap["time_left_seconds"] <= 7260

    def test_time_left_zero_for_past_end_time(self):
        from db.ebay_repo import insert_snapshot
        client = self._mock_client()
        past_end = "2020-01-01T00:00:00.000Z"
        item = {"current_price_usd": 100.0, "is_active": False, "is_sold": True,
                "bid_count": 0, "end_time": past_end}
        insert_snapshot(client, "listing-uuid", "v1|100|0", item)
        snap = client.table.return_value.insert.call_args[0][0]
        assert snap["time_left_seconds"] == 0


# ================================================================
# TestSnapshotIdempotency (3)
# ================================================================

class TestSnapshotIdempotency:
    """snapshot は UPSERT ではなく常に INSERT (時系列追跡)。"""

    def test_snapshot_uses_insert_not_upsert(self):
        """insert_snapshot が table().insert() を呼ぶ (upsert ではない)。"""
        from db.ebay_repo import insert_snapshot
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        item = {"current_price_usd": 100.0, "is_active": True, "is_sold": False, "bid_count": 0}
        insert_snapshot(client, "uid-1", "v1|1|0", item)
        client.table.return_value.insert.assert_called_once()
        client.table.return_value.upsert.assert_not_called()

    def test_snapshot_includes_listing_id(self):
        from db.ebay_repo import insert_snapshot
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        item = {"current_price_usd": 100.0, "is_active": True, "is_sold": False, "bid_count": 0}
        insert_snapshot(client, "my-listing-uuid", "v1|1|0", item)
        snap = client.table.return_value.insert.call_args[0][0]
        assert snap["listing_id"] == "my-listing-uuid"

    def test_snapshot_includes_ebay_item_id(self):
        from db.ebay_repo import insert_snapshot
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        item = {"current_price_usd": 100.0, "is_active": True, "is_sold": False, "bid_count": 0}
        insert_snapshot(client, "uuid-x", "v1|42|0", item)
        snap = client.table.return_value.insert.call_args[0][0]
        assert snap["ebay_item_id"] == "v1|42|0"


# ================================================================
# TestIngestResultStatus (4)
# ================================================================

class TestIngestResultStatus:
    def test_ok_when_no_errors(self):
        r = IngestResult(seeds_scanned=1, listings_saved=5)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_saved(self):
        r = IngestResult(listings_saved=3, error_count=1)
        assert r.ok is False
        assert r.status_str() == "partial"

    def test_error_when_nothing_saved(self):
        r = IngestResult(listings_saved=0, error_count=2)
        assert r.status_str() == "error"

    def test_counts_accumulate(self):
        r = IngestResult()
        r.seeds_scanned    += 2
        r.listings_fetched += 10
        r.listings_saved   += 8
        r.snapshots_saved  += 8
        assert r.seeds_scanned    == 2
        assert r.listings_fetched == 10
        assert r.listings_saved   == 8
        assert r.snapshots_saved  == 8


# ================================================================
# TestIngestDryRun (3)
# ================================================================

class TestIngestDryRun:
    """dry_run=True のとき DB に書き込まない。"""

    def _patched_ingest(self, seeds, ebay_items):
        """run_ingest を呼び出し、DB 呼び出しを全てモックする。"""
        with (
            patch("scripts.ebay_api_ingest.get_client") as mock_get_client,
            patch("scripts.ebay_api_ingest.EbayBrowseClient") as mock_ebay_cls,
            patch("scripts.ebay_api_ingest.load_ready_seeds", return_value=seeds),
            patch("scripts.ebay_api_ingest.mark_seed_scanning")  as mock_scanning,
            patch("scripts.ebay_api_ingest.mark_seed_scanned")   as mock_scanned,
            patch("scripts.ebay_api_ingest.upsert_listing_raw")  as mock_upsert,
            patch("scripts.ebay_api_ingest.insert_snapshot")     as mock_snap,
            patch("scripts.ebay_api_ingest.requeue_cooled_seeds", return_value=0),
        ):
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            ebay_instance = MagicMock()
            ebay_instance.is_configured = True
            ebay_instance.search_by_seed.return_value = ebay_items
            mock_ebay_cls.return_value = ebay_instance

            result = run_ingest(dry_run=True, limit=10)
            return result, mock_scanning, mock_scanned, mock_upsert, mock_snap

    def test_dry_run_no_upsert_listing_raw(self):
        seeds = [{"id": "seed-1", "search_query": "NGC MS63 Morgan"}]
        items = [_make_raw()]
        result, _, _, mock_upsert, _ = self._patched_ingest(seeds, items)
        mock_upsert.assert_not_called()

    def test_dry_run_no_insert_snapshot(self):
        seeds = [{"id": "seed-1", "search_query": "NGC MS63 Morgan"}]
        items = [_make_raw()]
        result, _, _, _, mock_snap = self._patched_ingest(seeds, items)
        mock_snap.assert_not_called()

    def test_dry_run_no_mark_seed_scanning(self):
        seeds = [{"id": "seed-1", "search_query": "NGC MS63 Morgan"}]
        items = [_make_raw()]
        result, mock_scanning, mock_scanned, _, _ = self._patched_ingest(seeds, items)
        mock_scanning.assert_not_called()
        mock_scanned.assert_not_called()
