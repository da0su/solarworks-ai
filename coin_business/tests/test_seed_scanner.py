"""
coin_business/tests/test_seed_scanner.py
==========================================
SeedScanner のユニットテスト。

テスト構成:
  TestScanSeedHitLinkage     (10) - seed→hit の紐付け・保存フロー
  TestScanSeedDedup          ( 7) - 重複 hit 抑止 (事前チェック + UNIQUE)
  TestSeedStatusTransition   ( 6) - SCANNING → COOLDOWN 遷移
  TestCadenceByPriority      ( 7) - priority 別 cooldown 時間
  TestScanRunResult          ( 5) - ScanRunResult ステータス集計
  TestScannerUnconfigured    ( 3) - eBay API 未設定時の挙動
  TestDryRunNoDBWrites       ( 5) - dry_run=True は DB を呼ばない
  TestHitFields              ( 5) - hit に matched_query/hit_rank/hit_reason が入る

合計 48 テスト
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from constants import SeedType, ScannerCadence
from ebay.scanner import SeedScanner, ScanRunResult, SeedScanResult


# ================================================================
# Fixtures / helpers
# ================================================================

def _make_seed(
    seed_id:   str  = "seed-uuid-1",
    seed_type: str  = SeedType.CERT_EXACT,
    query:     str  = "NGC MS63 1921 Morgan Dollar",
    ref_price: int  = 30000,
    status:    str  = "READY",
) -> dict:
    return {
        "id":           seed_id,
        "seed_type":    seed_type,
        "search_query": query,
        "ref_price_jpy": ref_price,
        "seed_status":  status,
        "yahoo_lot_id": "lot-001",
        "scan_count":   0,
        "hit_count":    0,
    }


def _make_item(
    item_id: str = "v1|111|0",
    title:   str = "1921 Morgan Dollar NGC MS63",
    price:   float = 250.0,
) -> dict:
    return {
        "ebay_item_id":     item_id,
        "title":            title,
        "listing_url":      f"https://www.ebay.com/itm/{item_id}",
        "current_price_usd": price,
        "currency":         "USD",
        "bid_count":        0,
        "listing_type":     "FixedPrice",
        "seller_username":  "coin_seller",
        "is_active":        True,
        "is_sold":          False,
    }


_SENTINEL = object()  # 明示的な None と「未指定」を区別するための番兵


def _make_scanner(
    seeds: list[dict],
    items: list[dict],
    existing_hit_ids: set[str] | None = None,
    upsert_raw_return: object = _SENTINEL,  # _SENTINEL = 自動採番
    upsert_hit_return: str | None = "hit-uuid-1",
):
    """
    DB と eBay クライアントをモックした SeedScanner を返す。

    upsert_raw_return を省略するとき (= _SENTINEL)、呼び出し順に
    "listing-uuid-1", "listing-uuid-2", ... を返す。
    各アイテムに一意の UUID を与えることで、インスキャン dedup が
    誤発動しないようにする。
    固定値 (または None) が必要なテストは明示的に upsert_raw_return を渡す。
    """
    mock_client = MagicMock()

    # upsert_listing_raw の side_effect
    _counter: list[int] = [0]
    def _auto_uuid(*args, **kwargs):
        if upsert_raw_return is _SENTINEL:
            _counter[0] += 1
            return f"listing-uuid-{_counter[0]}"
        return upsert_raw_return  # None を含む明示的な値

    upsert_raw_mock = MagicMock(side_effect=_auto_uuid)

    patches = {
        "load_ready_seeds":            MagicMock(return_value=seeds),
        "mark_seed_scanning":          MagicMock(return_value=True),
        "mark_seed_scanned":           MagicMock(return_value=True),
        "requeue_cooled_seeds":        MagicMock(return_value=0),
        "upsert_listing_raw":          upsert_raw_mock,
        "get_existing_hit_listing_ids":MagicMock(return_value=existing_hit_ids or set()),
        "upsert_seed_hit":             MagicMock(return_value=upsert_hit_return),
        "record_scanner_run":          MagicMock(return_value=True),
    }

    mock_ebay = MagicMock()
    mock_ebay.is_configured = True
    mock_ebay.search_by_seed.return_value = items

    return mock_client, mock_ebay, patches


# ================================================================
# TestScanSeedHitLinkage (10)
# ================================================================

class TestScanSeedHitLinkage:
    """seed と hit が正しく紐付けられる。"""

    def _run_scan_seed(self, seed, items, **kw):
        mock_client, mock_ebay, patches = _make_scanner([seed], items, **kw)
        with (
            patch("ebay.scanner.load_ready_seeds", patches["load_ready_seeds"]),
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.requeue_cooled_seeds", patches["requeue_cooled_seeds"]),
            patch("ebay.scanner.upsert_listing_raw",   patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit",      patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            sr = scanner.scan_seed(seed)
            return sr, patches

    def test_fetched_count_equals_items(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        sr, _ = self._run_scan_seed(seed, items)
        assert sr.fetched == 2

    def test_saved_count_equals_items(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        sr, _ = self._run_scan_seed(seed, items)
        assert sr.saved == 2

    def test_hit_new_equals_items_when_no_dedup(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        sr, _ = self._run_scan_seed(seed, items)
        assert sr.hit_new == 2

    def test_upsert_raw_called_for_each_item(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0"), _make_item("v1|3|0")]
        _, patches = self._run_scan_seed(seed, items)
        assert patches["upsert_listing_raw"].call_count == 3

    def test_upsert_hit_called_for_each_item(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        _, patches = self._run_scan_seed(seed, items)
        assert patches["upsert_seed_hit"].call_count == 2

    def test_seed_id_passed_to_upsert_hit(self):
        seed  = _make_seed(seed_id="my-seed-id")
        items = [_make_item("v1|1|0")]
        _, patches = self._run_scan_seed(seed, items)
        call_kwargs = patches["upsert_seed_hit"].call_args[1]
        assert call_kwargs["seed_id"] == "my-seed-id"

    def test_ebay_item_id_passed_to_upsert_hit(self):
        seed  = _make_seed()
        items = [_make_item("v1|42|0")]
        _, patches = self._run_scan_seed(seed, items)
        call_kwargs = patches["upsert_seed_hit"].call_args[1]
        assert call_kwargs["ebay_item_id"] == "v1|42|0"

    def test_listing_id_from_upsert_raw_passed_to_hit(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0")]
        _, patches = self._run_scan_seed(
            seed, items, upsert_raw_return="raw-listing-uuid"
        )
        call_kwargs = patches["upsert_seed_hit"].call_args[1]
        assert call_kwargs["listing_id"] == "raw-listing-uuid"

    def test_no_hit_when_upsert_raw_fails(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0")]
        sr, patches = self._run_scan_seed(
            seed, items, upsert_raw_return=None
        )
        patches["upsert_seed_hit"].assert_not_called()
        assert sr.saved == 0

    def test_result_has_correct_seed_id(self):
        seed = _make_seed(seed_id="test-seed-123")
        sr, _ = self._run_scan_seed(seed, [])
        assert sr.seed_id == "test-seed-123"


# ================================================================
# TestScanSeedDedup (7)
# ================================================================

class TestScanSeedDedup:
    """重複 hit は skip される。"""

    def _run_with_existing(self, items, existing_ids: set[str]):
        seed = _make_seed()
        mock_client, mock_ebay, patches = _make_scanner(
            [seed], items, existing_hit_ids=existing_ids
        )
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit",     patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            sr = scanner.scan_seed(seed)
            return sr, patches

    def test_existing_hit_skipped(self):
        items = [_make_item("v1|1|0")]
        sr, patches = self._run_with_existing(
            items,
            existing_ids={"listing-uuid-1"},  # upsert_raw が返す UUID と一致
        )
        patches["upsert_seed_hit"].assert_not_called()
        assert sr.hit_skip == 1
        assert sr.hit_new  == 0

    def test_new_listing_not_skipped(self):
        items = [_make_item("v1|1|0")]
        sr, patches = self._run_with_existing(items, existing_ids=set())
        patches["upsert_seed_hit"].assert_called_once()
        assert sr.hit_new == 1

    def test_mixed_existing_and_new(self):
        """1 件目が既存 hit、2 件目が新規の混合ケース。"""
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        # upsert_raw は "listing-uuid-1", "listing-uuid-2" を順に返す
        # 既存 hit には 1 件目の UUID だけ入れる
        existing = {"listing-uuid-1"}
        sr, patches = self._run_with_existing(items, existing_ids=existing)
        assert sr.hit_skip == 1   # 1 件目のみ skip
        assert sr.hit_new  == 1   # 2 件目は新規

    def test_second_scan_skips_first_existing(self):
        """既存 hit の listing_id が 1 件目と一致する場合、1 件目だけ skip される。"""
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        # "listing-uuid-1" のみ既存 (1 件目の UUID と一致)
        existing = {"listing-uuid-1"}
        sr, patches = self._run_with_existing(items, existing_ids=existing)
        # 1 件目 skip、2 件目は新規 → upsert_seed_hit は 1 回
        assert patches["upsert_seed_hit"].call_count == 1
        assert sr.hit_skip == 1
        assert sr.hit_new  == 1

    def test_hit_skip_count_correct(self):
        items = [_make_item("v1|1|0")]
        sr, _ = self._run_with_existing(
            items, existing_ids={"listing-uuid-1"}
        )
        assert sr.hit_skip == 1

    def test_empty_existing_all_new(self):
        items = [_make_item("v1|10|0"), _make_item("v1|11|0"), _make_item("v1|12|0")]
        sr, patches = self._run_with_existing(items, existing_ids=set())
        assert patches["upsert_seed_hit"].call_count == 3
        assert sr.hit_new == 3

    def test_get_existing_called_with_seed_id(self):
        seed  = _make_seed(seed_id="check-seed-id")
        items = [_make_item("v1|1|0")]
        mock_client, mock_ebay, patches = _make_scanner([seed], items)
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        patches["get_existing_hit_listing_ids"].assert_called_once_with(
            mock_client, "check-seed-id"
        )


# ================================================================
# TestSeedStatusTransition (6)
# ================================================================

class TestSeedStatusTransition:
    """READY → SCANNING → COOLDOWN の遷移が正しい順序で呼ばれる。"""

    def _run_and_get_patches(self, seed, items):
        mock_client, mock_ebay, patches = _make_scanner([seed], items)
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        return patches, mock_client

    def test_mark_scanning_called(self):
        seed = _make_seed()
        patches, _ = self._run_and_get_patches(seed, [])
        patches["mark_seed_scanning"].assert_called_once()

    def test_mark_scanned_called(self):
        seed = _make_seed()
        patches, _ = self._run_and_get_patches(seed, [])
        patches["mark_seed_scanned"].assert_called_once()

    def test_scanning_called_before_scanned(self):
        """mark_seed_scanning が mark_seed_scanned より前に呼ばれる。"""
        call_order = []
        seed = _make_seed()
        mock_client, mock_ebay, patches = _make_scanner([seed], [])
        patches["mark_seed_scanning"].side_effect = lambda *a, **k: call_order.append("scanning")
        patches["mark_seed_scanned"].side_effect  = lambda *a, **k: call_order.append("scanned")
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        assert call_order == ["scanning", "scanned"]

    def test_mark_scanned_with_hit_count(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        patches, mock_client = self._run_and_get_patches(seed, items)
        call_kwargs = patches["mark_seed_scanned"].call_args[1]
        assert call_kwargs["hit_count_delta"] == 2

    def test_mark_scanned_called_on_search_error(self):
        """検索例外でも mark_seed_scanned は呼ばれる (COOLDOWN に確実に移行)。"""
        seed = _make_seed()
        mock_client, mock_ebay, patches = _make_scanner([seed], [])
        mock_ebay.search_by_seed.side_effect = RuntimeError("timeout")
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            sr = scanner.scan_seed(seed)
        patches["mark_seed_scanned"].assert_called_once()
        assert sr.error is True

    def test_empty_query_skips_all_transitions(self):
        """search_query が空なら scanning も scanned も呼ばれない。"""
        seed = _make_seed(query="")
        mock_client, mock_ebay, patches = _make_scanner([seed], [])
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            sr = scanner.scan_seed(seed)
        patches["mark_seed_scanning"].assert_not_called()
        patches["mark_seed_scanned"].assert_not_called()
        assert sr.error is True


# ================================================================
# TestCadenceByPriority (7)
# ================================================================

class TestCadenceByPriority:
    """seed_type ごとに異なるクールダウン時間が設定される。"""

    def test_cert_exact_cooldown_1h(self):
        assert ScannerCadence.cooldown_hours(SeedType.CERT_EXACT) == 1

    def test_cert_title_cooldown_2h(self):
        assert ScannerCadence.cooldown_hours(SeedType.CERT_TITLE) == 2

    def test_title_normalized_cooldown_4h(self):
        assert ScannerCadence.cooldown_hours(SeedType.TITLE_NORMALIZED) == 4

    def test_year_denom_grade_cooldown_6h(self):
        assert ScannerCadence.cooldown_hours(SeedType.YEAR_DENOM_GRADE) == 6

    def test_cert_exact_highest_frequency(self):
        """CERT_EXACT が最も短いクールダウン (最高頻度)。"""
        assert (
            ScannerCadence.cooldown_hours(SeedType.CERT_EXACT)
            < ScannerCadence.cooldown_hours(SeedType.CERT_TITLE)
            < ScannerCadence.cooldown_hours(SeedType.TITLE_NORMALIZED)
            < ScannerCadence.cooldown_hours(SeedType.YEAR_DENOM_GRADE)
        )

    def test_unknown_seed_type_returns_max(self):
        """未知の seed_type は最大値 (最低頻度) を返す。"""
        assert ScannerCadence.cooldown_hours("UNKNOWN_TYPE") == \
               ScannerCadence.YEAR_DENOM_GRADE_HOURS

    def test_cooldown_passed_to_mark_seed_scanned(self):
        """scan_seed が mark_seed_scanned に正しい cooldown_hours を渡す。"""
        seed = _make_seed(seed_type=SeedType.CERT_EXACT)
        mock_client, mock_ebay, patches = _make_scanner([seed], [])
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit", patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        call_kwargs = patches["mark_seed_scanned"].call_args[1]
        assert call_kwargs["cooldown_hours"] == 1  # CERT_EXACT = 1h


# ================================================================
# TestScanRunResult (5)
# ================================================================

class TestScanRunResult:
    def test_ok_when_no_errors(self):
        r = ScanRunResult(seeds_scanned=3, hits_saved=10)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_saved(self):
        r = ScanRunResult(hits_saved=5, error_count=1)
        assert r.status_str() == "partial"

    def test_error_when_nothing_saved(self):
        r = ScanRunResult(hits_saved=0, error_count=1)
        assert r.status_str() == "error"

    def test_counts_accumulate_across_seeds(self):
        r = ScanRunResult()
        r.seeds_scanned += 3
        r.hits_found    += 15
        r.hits_saved    += 12
        assert r.seeds_scanned == 3
        assert r.hits_found    == 15
        assert r.hits_saved    == 12

    def test_ok_false_when_error_count_positive(self):
        r = ScanRunResult(error_count=1)
        assert r.ok is False


# ================================================================
# TestScannerUnconfigured (3)
# ================================================================

class TestScannerUnconfigured:
    def test_run_returns_error_when_unconfigured(self):
        mock_client = MagicMock()
        mock_ebay   = MagicMock()
        mock_ebay.is_configured = False
        scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
        result = scanner.run(limit=10)
        assert result.ok is False
        assert result.error_count == 1

    def test_run_returns_no_seeds_scanned_when_unconfigured(self):
        mock_client = MagicMock()
        mock_ebay   = MagicMock()
        mock_ebay.is_configured = False
        scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
        result = scanner.run()
        assert result.seeds_scanned == 0

    def test_run_error_message_mentions_credentials(self):
        mock_client = MagicMock()
        mock_ebay   = MagicMock()
        mock_ebay.is_configured = False
        scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
        result = scanner.run()
        assert any("EBAY_CLIENT" in e for e in result.errors)


# ================================================================
# TestDryRunNoDBWrites (5)
# ================================================================

class TestDryRunNoDBWrites:
    def _run_dry(self, seeds, items):
        mock_client, mock_ebay, patches = _make_scanner(seeds, items)
        with (
            patch("ebay.scanner.load_ready_seeds",     patches["load_ready_seeds"]),
            patch("ebay.scanner.mark_seed_scanning",   patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",    patches["mark_seed_scanned"]),
            patch("ebay.scanner.requeue_cooled_seeds", patches["requeue_cooled_seeds"]),
            patch("ebay.scanner.upsert_listing_raw",   patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit",      patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            result = scanner.run(dry_run=True, limit=10)
            return result, patches

    def test_dry_run_no_mark_scanning(self):
        result, patches = self._run_dry(
            [_make_seed()], [_make_item()]
        )
        patches["mark_seed_scanning"].assert_not_called()

    def test_dry_run_no_mark_scanned(self):
        result, patches = self._run_dry(
            [_make_seed()], [_make_item()]
        )
        patches["mark_seed_scanned"].assert_not_called()

    def test_dry_run_no_upsert_raw(self):
        result, patches = self._run_dry(
            [_make_seed()], [_make_item()]
        )
        patches["upsert_listing_raw"].assert_not_called()

    def test_dry_run_no_upsert_hit(self):
        result, patches = self._run_dry(
            [_make_seed()], [_make_item()]
        )
        patches["upsert_seed_hit"].assert_not_called()

    def test_dry_run_no_requeue(self):
        result, patches = self._run_dry(
            [_make_seed()], [_make_item()]
        )
        patches["requeue_cooled_seeds"].assert_not_called()


# ================================================================
# TestHitFields (5)
# ================================================================

class TestHitFields:
    """hit に matched_query / hit_rank / hit_reason が正しく入る。"""

    def _get_hit_call_kwargs(self, seed, items):
        mock_client, mock_ebay, patches = _make_scanner([seed], items)
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit",     patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        if not patches["upsert_seed_hit"].called:
            return {}
        return patches["upsert_seed_hit"].call_args[1]

    def test_matched_query_is_seed_query(self):
        seed = _make_seed(query="NGC MS63 Morgan")
        kw = self._get_hit_call_kwargs(seed, [_make_item()])
        assert kw["matched_query"] == "NGC MS63 Morgan"

    def test_hit_rank_starts_at_1(self):
        seed  = _make_seed()
        items = [_make_item("v1|1|0"), _make_item("v1|2|0")]
        mock_client, mock_ebay, patches = _make_scanner([seed], items)
        ranks = []
        def capture_rank(**kwargs):
            ranks.append(kwargs["hit_rank"])
            return "hit-uuid"
        patches["upsert_seed_hit"].side_effect = capture_rank
        with (
            patch("ebay.scanner.mark_seed_scanning", patches["mark_seed_scanning"]),
            patch("ebay.scanner.mark_seed_scanned",  patches["mark_seed_scanned"]),
            patch("ebay.scanner.upsert_listing_raw",  patches["upsert_listing_raw"]),
            patch("ebay.scanner.get_existing_hit_listing_ids",
                  patches["get_existing_hit_listing_ids"]),
            patch("ebay.scanner.upsert_seed_hit",     patches["upsert_seed_hit"]),
        ):
            scanner = SeedScanner(mock_client, ebay_client=mock_ebay)
            scanner.scan_seed(seed)
        assert ranks[0] == 1
        assert ranks[1] == 2

    def test_hit_reason_cert_exact(self):
        seed = _make_seed(seed_type=SeedType.CERT_EXACT)
        kw   = self._get_hit_call_kwargs(seed, [_make_item()])
        assert kw["hit_reason"] == "cert_number_match"

    def test_hit_reason_year_denom_grade(self):
        seed = _make_seed(seed_type=SeedType.YEAR_DENOM_GRADE)
        kw   = self._get_hit_call_kwargs(seed, [_make_item()])
        assert kw["hit_reason"] == "year_denom_grade"

    def test_match_score_equals_seed_type_priority(self):
        seed = _make_seed(seed_type=SeedType.CERT_EXACT)
        kw   = self._get_hit_call_kwargs(seed, [_make_item()])
        assert kw["match_score"] == pytest.approx(1.0)
