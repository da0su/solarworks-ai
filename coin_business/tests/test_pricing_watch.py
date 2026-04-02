"""
coin_business/tests/test_pricing_watch.py
===========================================
Day 9 テスト: pricing 計算 + watchlist 状態遷移

テストクラス:
  TestPricingCalc          (8 tests)  — target/recommended/quality score
  TestWatchStateTransition (9 tests)  — 全 WatchStatus への遷移
  TestWatchCadence         (5 tests)  — 4 cadence tier + None
  TestPricingResultStatus  (4 tests)  — status_str()
  TestWatchRunResultStatus (4 tests)  — status_str()

合計: 30 tests
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from constants import ProfitCalc, WatchCadence, WatchStatus
from scripts.candidate_pricer import (
    PricingResult,
    calc_comparison_quality_score,
    calc_recommended_max_bid_jpy,
    calc_target_max_bid_jpy,
)
from scripts.keep_watch_refresher import (
    WatchRunResult,
    calc_next_refresh_at,
    calc_time_left_seconds,
    determine_watch_status,
)


# ================================================================
# TestPricingCalc
# ================================================================

class TestPricingCalc:
    """target_max_bid_jpy / recommended / comparison_quality_score の計算"""

    def test_target_max_bid_basic(self):
        """想定売却価格 200,000円 → target が正の値"""
        target = calc_target_max_bid_jpy(200_000)
        assert target is not None
        assert target > 0

    def test_target_max_bid_margin_boundary(self):
        """
        margin = (revenue - (target + fixed_costs)) / revenue >= 0.15
        revenue = 200000 * 0.9 = 180000
        target = 180000 * (1 - 0.15) - 2000 - 750 = 153000 - 2750 = 150250
        """
        target = calc_target_max_bid_jpy(200_000)
        revenue = 200_000 * (1.0 - ProfitCalc.YAHOO_AUCTION_FEE)
        cost    = target + ProfitCalc.US_FORWARDING_JPY + ProfitCalc.DOMESTIC_SHIPPING_JPY
        profit  = revenue - cost
        margin  = profit / revenue
        assert margin >= ProfitCalc.MIN_GROSS_MARGIN - 0.001  # floating tolerance

    def test_target_max_bid_none_on_zero_price(self):
        assert calc_target_max_bid_jpy(0) is None

    def test_target_max_bid_none_on_none(self):
        assert calc_target_max_bid_jpy(None) is None  # type: ignore

    def test_recommended_is_90pct_of_target(self):
        target = calc_target_max_bid_jpy(200_000)
        rec    = calc_recommended_max_bid_jpy(200_000)
        assert rec == int(target * 0.90)

    def test_recommended_none_when_target_none(self):
        assert calc_recommended_max_bid_jpy(0) is None

    def test_quality_score_high_3m(self):
        """3m に 5件 → score = min(1.0, 5*1.0/5) = 1.0"""
        score = calc_comparison_quality_score(
            recent_3m_count=5, recent_3_6m_count=0, recent_6_12m_count=0
        )
        assert score == 1.0

    def test_quality_score_weighted_blend(self):
        """3m=2, 3-6m=2, 6-12m=5 → weighted=2+1+1=4 → 4/5=0.8"""
        score = calc_comparison_quality_score(
            recent_3m_count=2, recent_3_6m_count=2, recent_6_12m_count=5
        )
        assert abs(score - 0.8) < 0.01

    def test_quality_score_zero(self):
        score = calc_comparison_quality_score(0, 0, 0)
        assert score == 0.0

    def test_quality_score_capped_at_1(self):
        """大量データでも 1.0 を超えない"""
        score = calc_comparison_quality_score(100, 100, 100)
        assert score == 1.0


# ================================================================
# TestWatchStateTransition
# ================================================================

class TestWatchStateTransition:
    """determine_watch_status の全状態遷移テスト"""

    _NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    _FUTURE_FAR   = _NOW + timedelta(hours=48)
    _FUTURE_22H   = _NOW + timedelta(hours=22)
    _FUTURE_5H    = _NOW + timedelta(hours=5)
    _FUTURE_30MIN = _NOW + timedelta(minutes=30)
    _PAST          = _NOW - timedelta(minutes=1)

    def test_ended_when_auction_past(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._PAST,
            current_price_jpy = 100_000,
            max_bid_jpy       = 150_000,
            time_left_seconds = None,
        )
        assert status == WatchStatus.ENDED

    def test_bid_ready_within_1h_price_ok(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_30MIN,
            current_price_jpy = 100_000,
            max_bid_jpy       = 150_000,
            time_left_seconds = 1800,  # 30 min
        )
        assert status == WatchStatus.BID_READY

    def test_ending_soon_within_1h_price_too_high(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_30MIN,
            current_price_jpy = 200_000,
            max_bid_jpy       = 150_000,
            time_left_seconds = 1800,
        )
        assert status == WatchStatus.ENDING_SOON

    def test_ending_soon_within_1h_no_price(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_30MIN,
            current_price_jpy = None,
            max_bid_jpy       = 150_000,
            time_left_seconds = 1800,
        )
        assert status == WatchStatus.ENDING_SOON

    def test_price_ok_more_than_1h(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_22H,
            current_price_jpy = 100_000,
            max_bid_jpy       = 150_000,
            time_left_seconds = 22 * 3600,
        )
        assert status == WatchStatus.PRICE_OK

    def test_price_too_high_more_than_1h(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_22H,
            current_price_jpy = 200_000,
            max_bid_jpy       = 150_000,
            time_left_seconds = 22 * 3600,
        )
        assert status == WatchStatus.PRICE_HIGH

    def test_watching_no_price_info(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = self._FUTURE_FAR,
            current_price_jpy = None,
            max_bid_jpy       = None,
            time_left_seconds = 48 * 3600,
        )
        assert status == WatchStatus.WATCHING

    def test_watching_no_end_time(self):
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = None,
            current_price_jpy = None,
            max_bid_jpy       = None,
            time_left_seconds = None,
        )
        assert status == WatchStatus.WATCHING

    def test_price_ok_no_end_time(self):
        """auction_end_at がなくても price OK 判定は働く"""
        status = determine_watch_status(
            now               = self._NOW,
            auction_end_at    = None,
            current_price_jpy = 80_000,
            max_bid_jpy       = 100_000,
            time_left_seconds = 10 * 3600,
        )
        assert status == WatchStatus.PRICE_OK


# ================================================================
# TestWatchCadence
# ================================================================

class TestWatchCadence:
    """WatchCadence.for_time_left の cadence tier テスト"""

    def test_normal_cadence_over_24h(self):
        interval = WatchCadence.for_time_left(25 * 3600)
        assert interval == WatchCadence.NORMAL_SECONDS  # 3h

    def test_within_24h_cadence(self):
        interval = WatchCadence.for_time_left(23 * 3600)
        assert interval == WatchCadence.WITHIN_24H_SECONDS  # 1h

    def test_within_6h_cadence(self):
        interval = WatchCadence.for_time_left(5 * 3600)
        assert interval == WatchCadence.WITHIN_6H_SECONDS  # 30min

    def test_within_1h_cadence(self):
        interval = WatchCadence.for_time_left(3600)
        assert interval == WatchCadence.WITHIN_1H_SECONDS  # 10min

    def test_none_returns_normal(self):
        interval = WatchCadence.for_time_left(None)
        assert interval == WatchCadence.NORMAL_SECONDS


# ================================================================
# TestCalcTimeLeft
# ================================================================

class TestCalcTimeLeft:
    """calc_time_left_seconds のテスト"""

    def test_positive_time_left(self):
        now    = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        end_at = now + timedelta(hours=5)
        left   = calc_time_left_seconds(end_at, now)
        assert left == 5 * 3600

    def test_past_end_returns_none(self):
        now    = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        end_at = now - timedelta(minutes=1)
        left   = calc_time_left_seconds(end_at, now)
        assert left is None

    def test_none_end_at_returns_none(self):
        assert calc_time_left_seconds(None) is None


# ================================================================
# TestPricingResultStatus
# ================================================================

class TestPricingResultStatus:
    """PricingResult.status_str()"""

    def test_ok_when_no_errors(self):
        r = PricingResult(candidates_found=5, candidates_priced=5, error_count=0)
        assert r.status_str() == "ok"

    def test_partial_when_some_priced_some_error(self):
        r = PricingResult(candidates_found=5, candidates_priced=3, error_count=2)
        assert r.status_str() == "partial"

    def test_error_when_none_priced(self):
        r = PricingResult(candidates_found=5, candidates_priced=0, error_count=5)
        assert r.status_str() == "error"

    def test_ok_when_zero_found(self):
        r = PricingResult(candidates_found=0, candidates_priced=0, error_count=0)
        assert r.status_str() == "ok"


# ================================================================
# TestWatchRunResultStatus
# ================================================================

class TestWatchRunResultStatus:
    """WatchRunResult.status_str()"""

    def test_ok_when_no_errors(self):
        r = WatchRunResult(items_checked=10, items_updated=10, error_count=0)
        assert r.status_str() == "ok"

    def test_partial_when_some_updated_some_error(self):
        r = WatchRunResult(items_checked=10, items_updated=7, error_count=3)
        assert r.status_str() == "partial"

    def test_error_when_none_updated(self):
        r = WatchRunResult(items_checked=5, items_updated=0, error_count=5)
        assert r.status_str() == "error"

    def test_ok_when_zero_checked(self):
        r = WatchRunResult(items_checked=0, items_updated=0, error_count=0)
        assert r.status_str() == "ok"
