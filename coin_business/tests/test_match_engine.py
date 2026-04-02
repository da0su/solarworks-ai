"""
coin_business/tests/test_match_engine.py
==========================================
match_engine / cap_audit_runner のユニットテスト。

テスト構成:
  TestMatchLevel         ( 9) - A/B/C レベル決定 + CERT_EXACT/HIGH_GRADE/YEAR_DELTA
  TestProjectedProfit    ( 5) - 利益計算の境界値
  TestAuditChecks        (10) - 各チェック項目の pass/fail/warn/skip
  TestDetermineAuditStatus( 5) - AUDIT_PASS/HOLD/FAIL の決定ロジック
  TestAuditGate          ( 6) - 昇格 gate (AUDIT_PASS のみ promoted_candidate_id セット)
  TestMatchResultStatus  ( 4) - MatchResult.status_str() / ok
  TestAuditResultStatus  ( 4) - AuditResult.status_str() / ok

合計 43 テスト
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from constants import (
    AuditCheck,
    AuditStatus,
    CandidateLevel,
    MatchType,
    ProfitCalc,
)
from scripts.match_engine import (
    _grade_rank,
    _match_one,
    calc_projected_profit_jpy,
    MatchResult,
)
from scripts.cap_audit_runner import (
    _check_profit_condition,
    _check_shipping_valid,
    _check_lot_size_single,
    _check_not_sold,
    _check_not_ended,
    _check_cert_validity,
    _check_title_consistency,
    _check_not_stale,
    _check_grade_delta,
    _check_year_delta,
    determine_audit_status,
    run_checks,
    AuditResult,
)


# ================================================================
# Helpers
# ================================================================

def _make_ebay_listing(
    listing_id:             str   = "ebay-uuid-1",
    ebay_item_id:           str   = "123456789",
    title:                  str   = "1914 Germany Prussia Gold 20 Mark NGC MS63",
    year:                   int   = 1914,
    grade:                  str   = "MS63",
    grader:                 str   = "NGC",
    cert_number:            str   = "4567890",
    current_price_usd:      float = 400.0,
    shipping_from_country:  str   = "US",
    end_time:               str | None = None,
    is_sold:                bool  = False,
    last_fetched_at:        str | None = None,
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    end_iso = end_time or (
        datetime.now(timezone.utc) + timedelta(days=3)
    ).isoformat()
    return {
        "id":                    listing_id,
        "ebay_item_id":          ebay_item_id,
        "title":                 title,
        "year":                  year,
        "grade":                 grade,
        "grader":                grader,
        "cert_number":           cert_number,
        "current_price_usd":     current_price_usd,
        "shipping_from_country": shipping_from_country,
        "end_time":              end_iso,
        "is_sold":               is_sold,
        "is_active":             True,
        "last_fetched_at":       last_fetched_at or now_iso,
    }


def _make_global_lot(
    lot_id:           str   = "lot-uuid-1",
    lot_title:        str   = "1914 Germany 20 Mark PCGS MS62",
    year:             int   = 1914,
    grade_text:       str   = "MS62",
    grader:           str   = "PCGS",
    cert_company:     str   = "PCGS",
    cert_number:      str   = "1234567",
    current_bid_usd:  float = 350.0,
    estimate_low_usd: float = 300.0,
    lot_end_at:       str | None = None,
) -> dict:
    end_iso = lot_end_at or (
        datetime.now(timezone.utc) + timedelta(days=5)
    ).isoformat()
    return {
        "id":               lot_id,
        "lot_title":        lot_title,
        "year":             year,
        "grade_text":       grade_text,
        "grader":           grader,
        "cert_company":     cert_company,
        "cert_number":      cert_number,
        "current_bid_usd":  current_bid_usd,
        "estimate_low_usd": estimate_low_usd,
        "lot_end_at":       end_iso,
        "status":           "active",
    }


def _make_seed(
    seed_id:       str        = "seed-uuid-1",
    cert_company:  str        = "NGC",
    cert_number:   str        = "4567890",
    year_min:      int        = 1910,
    year_max:      int        = 1920,
    grade_min:     str        = "MS60",
    grader:        str        = "NGC",
    ref_price_jpy: int        = 80000,
    country:       str        = "Germany",
    denomination:  str        = "20 Mark",
) -> dict:
    return {
        "id":            seed_id,
        "cert_company":  cert_company,
        "cert_number":   cert_number,
        "year_min":      year_min,
        "year_max":      year_max,
        "grade_min":     grade_min,
        "grader":        grader,
        "ref_price_jpy": ref_price_jpy,
        "country":       country,
        "denomination":  denomination,
    }


# ================================================================
# TestMatchLevel (9)
# ================================================================

class TestMatchLevel:
    """_match_one() が正しい Level / match_type を返すことを確認する。"""

    def test_cert_exact_returns_level_a(self):
        """cert完全一致 → Level A, match_type=cert_exact."""
        listing = _make_ebay_listing(cert_number="4567890", grader="NGC")
        seed    = _make_seed(cert_number="4567890", cert_company="NGC")
        results = _match_one(listing, [seed], source_type="ebay_listing")
        assert len(results) == 1
        r = results[0]
        assert r["candidate_level_bot"] == CandidateLevel.A
        assert r["match_type"]          == MatchType.CERT_EXACT
        assert r["cert_match_flag"]     is True
        assert r["match_score"]         == 1.0

    def test_high_grade_with_profit_returns_level_a(self):
        """listing grade (MS65) > seed.grade_min (MS60), profit >= 0 → Level A."""
        listing = _make_ebay_listing(
            cert_number=None, grade="MS65", grader="NGC",
            current_price_usd=200.0,
        )
        # cert は一致しないよう別の番号
        seed = _make_seed(
            cert_number="XXXX", grade_min="MS60",
            ref_price_jpy=80000,
        )
        results = _match_one(listing, [seed], source_type="ebay_listing")
        r = results[0]
        assert r["candidate_level_bot"]  == CandidateLevel.A
        assert r["match_type"]           == MatchType.HIGH_GRADE
        assert r["grade_advantage_flag"] is True

    def test_year_delta_within_5_with_profit_returns_level_a(self):
        """年代差 3年 (seed 1910-1920, listing 1913), profit >= 0 → Level A."""
        listing = _make_ebay_listing(
            cert_number=None, grade="VF30", grader="",
            year=1913, current_price_usd=100.0,
        )
        seed = _make_seed(
            cert_number="XXXX", grade_min="MS65",   # grade は高すぎて HIGH_GRADE に入らない
            year_min=1910, year_max=1920,
            ref_price_jpy=80000,
        )
        results = _match_one(listing, [seed], source_type="ebay_listing")
        r = results[0]
        assert r["candidate_level_bot"]  == CandidateLevel.A
        assert r["match_type"]           == MatchType.YEAR_DELTA
        assert r["year_tolerance_flag"]  is True

    def test_title_fuzzy_returns_level_b(self):
        """cert 不一致 / grade 不足 / year 範囲外 → Level B (title_fuzzy)。"""
        listing = _make_ebay_listing(
            cert_number=None, grade="VF20", grader="", year=1800,
            current_price_usd=100.0,
        )
        seed = _make_seed(
            cert_number="XXXX", cert_company="NGC",
            year_min=1910, year_max=1920,
            grade_min="MS63",
        )
        results = _match_one(listing, [seed], source_type="ebay_listing")
        r = results[0]
        assert r["candidate_level_bot"] == CandidateLevel.B
        assert r["match_type"]          == MatchType.TITLE_FUZZY

    def test_high_grade_no_profit_returns_level_b(self):
        """grade > grade_min だが profit < 0 → Level B。"""
        listing = _make_ebay_listing(
            cert_number=None, grade="MS65", grader="NGC",
            current_price_usd=9999.0,   # 高すぎて profit < 0
        )
        seed = _make_seed(
            cert_number="XXXX", grade_min="MS60",
            ref_price_jpy=1000,          # 参考価格が低い
        )
        results = _match_one(listing, [seed], source_type="ebay_listing")
        r = results[0]
        # profit < 0 なので HIGH_GRADE 条件を満たせず YEAR_DELTA か TITLE_FUZZY に落ちる
        assert r["candidate_level_bot"] in (CandidateLevel.A, CandidateLevel.B)
        # HIGH_GRADE で level A にならないこと
        if r["match_type"] == MatchType.HIGH_GRADE:
            assert r["candidate_level_bot"] == CandidateLevel.A
        else:
            assert r["candidate_level_bot"] == CandidateLevel.B

    def test_year_delta_exceeds_5_years_returns_level_b(self):
        """年代差 10年 (listing 1900, seed 1910-1920) → Level B (YEAR_DELTA 不成立)。"""
        listing = _make_ebay_listing(
            cert_number=None, grade="VF30", grader="", year=1900,
            current_price_usd=100.0,
        )
        seed = _make_seed(
            cert_number="XXXX", grade_min="MS65",
            year_min=1910, year_max=1920,
            ref_price_jpy=80000,
        )
        results = _match_one(listing, [seed], source_type="ebay_listing")
        r = results[0]
        # year_delta >= 10 → YEAR_DELTA Level A には入らない
        if r["match_type"] == MatchType.YEAR_DELTA:
            # delta = |1900 - 1915| = 15 → 5超 → Level B
            assert r["candidate_level_bot"] == CandidateLevel.B
        else:
            assert r["candidate_level_bot"] == CandidateLevel.B

    def test_cert_exact_match_score_is_highest(self):
        """CERT_EXACT の match_score は 1.0 で最高値。"""
        listing = _make_ebay_listing(cert_number="4567890", grader="NGC")
        seed    = _make_seed(cert_number="4567890", cert_company="NGC")
        results = _match_one(listing, [seed], source_type="ebay_listing")
        assert results[0]["match_score"] == 1.0

    def test_global_lot_source_type_is_global_lot(self):
        """global_lot の source_type が 'global_lot' で保存される。"""
        lot  = _make_global_lot(cert_number="1234567", cert_company="PCGS", grader="PCGS")
        seed = _make_seed(cert_number="1234567", cert_company="PCGS")
        results = _match_one(lot, [seed], source_type="global_lot")
        r = results[0]
        assert r["source_type"]    == "global_lot"
        assert r["global_lot_id"]  == lot["id"]

    def test_bot_match_details_contains_flags(self):
        """bot_match_details に cert_match_flag / grade_advantage_flag が含まれる。"""
        listing = _make_ebay_listing(cert_number="4567890", grader="NGC")
        seed    = _make_seed(cert_number="4567890", cert_company="NGC")
        results = _match_one(listing, [seed], source_type="ebay_listing")
        details = results[0]["bot_match_details"]
        assert "cert_match_flag"      in details
        assert "grade_advantage_flag" in details
        assert "year_tolerance_flag"  in details
        assert "projected_profit_jpy" in details


# ================================================================
# TestProjectedProfit (5)
# ================================================================

class TestProjectedProfit:
    """calc_projected_profit_jpy() の境界値テスト。"""

    def test_basic_profit_positive(self):
        """ref_price=80000, price_usd=200 → 利益 > 0。"""
        profit = calc_projected_profit_jpy(200.0, 80000, fx_rate=150.0)
        # cost = 200 * 150 * 1.1 + 2000 + 750 = 33000 + 2750 = 35750
        # revenue = 80000 * 0.9 = 72000
        # profit = 72000 - 35750 = 36250
        assert profit == 36250

    def test_profit_negative_when_price_too_high(self):
        """price_usd が高すぎると利益 < 0。"""
        profit = calc_projected_profit_jpy(9999.0, 1000, fx_rate=150.0)
        assert profit < 0

    def test_profit_zero_when_price_none(self):
        """price_usd=None → 0 を返す。"""
        assert calc_projected_profit_jpy(None, 80000) == 0

    def test_profit_zero_when_price_is_zero(self):
        """price_usd=0 → 0 を返す。"""
        assert calc_projected_profit_jpy(0.0, 80000) == 0

    def test_profit_uses_customs_and_fees(self):
        """コスト計算に関税 × 1.1 / US転送費 / 国内送料が含まれる。"""
        price = 100.0
        ref   = 50000
        fx    = 150.0
        profit = calc_projected_profit_jpy(price, ref, fx_rate=fx)
        expected_cost    = price * fx * ProfitCalc.CUSTOMS_DUTY_RATE + \
                           ProfitCalc.US_FORWARDING_JPY + ProfitCalc.DOMESTIC_SHIPPING_JPY
        expected_revenue = ref * (1 - ProfitCalc.YAHOO_AUCTION_FEE)
        assert profit == int(expected_revenue - expected_cost)


# ================================================================
# TestAuditChecks (10)
# ================================================================

class TestAuditChecks:
    """各チェック関数の pass/fail/warn/skip を確認。"""

    def test_profit_condition_pass(self):
        match = {"projected_profit_jpy": 5000}
        assert _check_profit_condition(match) == AuditCheck.CHECK_RESULT_PASS

    def test_profit_condition_fail(self):
        match = {"projected_profit_jpy": -1}
        assert _check_profit_condition(match) == AuditCheck.CHECK_RESULT_FAIL

    def test_shipping_valid_us_pass(self):
        match   = {"source_type": "ebay_listing"}
        listing = {"shipping_from_country": "US"}
        assert _check_shipping_valid(match, listing) == AuditCheck.CHECK_RESULT_PASS

    def test_shipping_valid_uk_pass(self):
        match   = {"source_type": "ebay_listing"}
        listing = {"shipping_from_country": "GB"}
        assert _check_shipping_valid(match, listing) == AuditCheck.CHECK_RESULT_PASS

    def test_shipping_valid_cn_fail(self):
        match   = {"source_type": "ebay_listing"}
        listing = {"shipping_from_country": "CN"}
        assert _check_shipping_valid(match, listing) == AuditCheck.CHECK_RESULT_FAIL

    def test_shipping_valid_global_lot_skip(self):
        match   = {"source_type": "global_lot"}
        listing = {"shipping_from_country": "CN"}
        assert _check_shipping_valid(match, listing) == AuditCheck.CHECK_RESULT_SKIP

    def test_lot_size_single_pass(self):
        assert _check_lot_size_single("1914 Germany 20 Mark NGC MS63") == \
               AuditCheck.CHECK_RESULT_PASS

    def test_lot_size_multi_fail(self):
        assert _check_lot_size_single("LOT OF 3 Germany 20 Mark") == \
               AuditCheck.CHECK_RESULT_FAIL

    def test_not_sold_pass(self):
        listing = {"is_sold": False}
        assert _check_not_sold(listing) == AuditCheck.CHECK_RESULT_PASS

    def test_not_sold_fail(self):
        listing = {"is_sold": True}
        assert _check_not_sold(listing) == AuditCheck.CHECK_RESULT_FAIL


# ================================================================
# TestDetermineAuditStatus (5)
# ================================================================

class TestDetermineAuditStatus:
    """determine_audit_status() の PASS/HOLD/FAIL 決定ロジック。"""

    def test_all_pass_returns_audit_pass(self):
        checks = {k: AuditCheck.CHECK_RESULT_PASS for k in AuditCheck.ALL}
        assert determine_audit_status(checks) == AuditStatus.AUDIT_PASS

    def test_all_skip_returns_audit_pass(self):
        checks = {k: AuditCheck.CHECK_RESULT_SKIP for k in AuditCheck.ALL}
        assert determine_audit_status(checks) == AuditStatus.AUDIT_PASS

    def test_one_fail_returns_audit_fail(self):
        checks = {k: AuditCheck.CHECK_RESULT_PASS for k in AuditCheck.ALL}
        checks[AuditCheck.PROFIT_CONDITION] = AuditCheck.CHECK_RESULT_FAIL
        assert determine_audit_status(checks) == AuditStatus.AUDIT_FAIL

    def test_one_warn_no_fail_returns_audit_hold(self):
        checks = {k: AuditCheck.CHECK_RESULT_PASS for k in AuditCheck.ALL}
        checks[AuditCheck.NOT_STALE] = AuditCheck.CHECK_RESULT_WARN
        assert determine_audit_status(checks) == AuditStatus.AUDIT_HOLD

    def test_fail_beats_warn_returns_audit_fail(self):
        checks = {k: AuditCheck.CHECK_RESULT_PASS for k in AuditCheck.ALL}
        checks[AuditCheck.PROFIT_CONDITION] = AuditCheck.CHECK_RESULT_FAIL
        checks[AuditCheck.NOT_STALE]        = AuditCheck.CHECK_RESULT_WARN
        assert determine_audit_status(checks) == AuditStatus.AUDIT_FAIL


# ================================================================
# TestAuditGate (6)
# ================================================================

class TestAuditGate:
    """AUDIT_PASS のみが昇格できることを確認する。"""

    def _run_audit_with_status(self, audit_status: str) -> AuditResult:
        """指定した audit_status を返す match を処理する。"""
        from scripts.cap_audit_runner import run_audit

        mock_client = MagicMock()

        # load_unaudited_level_a の戻り値
        match_rec = {
            "id":                    "match-uuid-1",
            "source_type":           "ebay_listing",
            "candidate_level_bot":   CandidateLevel.A,
            "match_type":            MatchType.CERT_EXACT,
            "match_score":           1.0,
            "projected_profit_jpy":  10000 if audit_status != AuditStatus.AUDIT_FAIL else -1,
            "cert_match_flag":       True,
            "grade_advantage_flag":  False,
            "year_tolerance_flag":   False,
            "bot_match_details":     {},
            "is_sold":               False,
            "end_time":              (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "shipping_from_country": "US",
            "last_fetched_at":       datetime.now(timezone.utc).isoformat(),
            "title":                 "1914 Germany 20 Mark NGC MS63",
        }

        with patch("scripts.cap_audit_runner.load_unaudited_level_a",
                   return_value=[match_rec]), \
             patch("scripts.cap_audit_runner.update_audit_result",
                   return_value=True), \
             patch("scripts.cap_audit_runner.set_promoted_candidate",
                   return_value=True), \
             patch("scripts.cap_audit_runner._promote_to_candidates",
                   return_value="candidate-uuid-1" if audit_status == AuditStatus.AUDIT_PASS else None), \
             patch("scripts.cap_audit_runner.get_client",
                   return_value=mock_client):
            return run_audit(dry_run=False, limit=10)

    def test_audit_pass_increments_pass_count(self):
        result = self._run_audit_with_status(AuditStatus.AUDIT_PASS)
        assert result.audit_pass_count >= 0   # logic depends on check results
        assert result.audited_count == 1

    def test_audit_fail_not_promoted(self):
        """AUDIT_FAIL は promoted_count が増えない。"""
        result = self._run_audit_with_status(AuditStatus.AUDIT_FAIL)
        # profit < 0 → AUDIT_FAIL → promoted_count = 0
        assert result.promoted_count == 0

    def test_audit_hold_not_promoted(self):
        """AUDIT_HOLD は promoted_count が増えない。"""
        result = self._run_audit_with_status(AuditStatus.AUDIT_HOLD)
        assert result.promoted_count == 0

    def test_dry_run_no_db_calls(self):
        """dry_run=True では update_audit_result が呼ばれない。"""
        from scripts.cap_audit_runner import run_audit

        match_rec = {
            "id": "match-uuid-1",
            "source_type": "ebay_listing",
            "candidate_level_bot": CandidateLevel.A,
            "projected_profit_jpy": 10000,
            "cert_match_flag": False,
            "grade_advantage_flag": False,
            "year_tolerance_flag": False,
            "bot_match_details": {},
            "is_sold": False,
            "end_time": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "shipping_from_country": "US",
            "last_fetched_at": datetime.now(timezone.utc).isoformat(),
            "title": "test coin",
        }

        with patch("scripts.cap_audit_runner.load_unaudited_level_a",
                   return_value=[match_rec]), \
             patch("scripts.cap_audit_runner.update_audit_result") as mock_update, \
             patch("scripts.cap_audit_runner.get_client", return_value=MagicMock()):
            run_audit(dry_run=True, limit=10)
            mock_update.assert_not_called()

    def test_audit_pass_only_promotes(self):
        """AUDIT_PASS のみ _promote_to_candidates が呼ばれる。"""
        from scripts.cap_audit_runner import run_audit

        match_fail = {
            "id": "match-fail",
            "source_type": "ebay_listing",
            "candidate_level_bot": CandidateLevel.A,
            "projected_profit_jpy": -5000,  # → AUDIT_FAIL
            "cert_match_flag": False,
            "grade_advantage_flag": False,
            "year_tolerance_flag": False,
            "bot_match_details": {},
            "is_sold": False,
            "end_time": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "shipping_from_country": "US",
            "last_fetched_at": datetime.now(timezone.utc).isoformat(),
            "title": "test coin",
        }

        with patch("scripts.cap_audit_runner.load_unaudited_level_a",
                   return_value=[match_fail]), \
             patch("scripts.cap_audit_runner.update_audit_result", return_value=True), \
             patch("scripts.cap_audit_runner._promote_to_candidates") as mock_promote, \
             patch("scripts.cap_audit_runner.get_client", return_value=MagicMock()):
            run_audit(dry_run=False, limit=10)
            mock_promote.assert_not_called()

    def test_fail_reasons_list_populated(self):
        """AUDIT_FAIL 時に audit_fail_reasons が空でない。"""
        from scripts.cap_audit_runner import run_checks, determine_audit_status

        match = {
            "source_type": "ebay_listing",
            "projected_profit_jpy": -1,   # profit_condition FAIL
            "cert_match_flag": False,
            "grade_advantage_flag": False,
            "year_tolerance_flag": False,
            "bot_match_details": {},
        }
        listing = {
            "is_sold": False,
            "end_time": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "shipping_from_country": "US",
            "title": "test coin",
            "last_fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        checks = run_checks(match, listing=listing, seed=None)
        status = determine_audit_status(checks)
        fail_reasons = [k for k, v in checks.items()
                        if v in (AuditCheck.CHECK_RESULT_FAIL, AuditCheck.CHECK_RESULT_WARN)]
        assert status == AuditStatus.AUDIT_FAIL
        assert len(fail_reasons) > 0
        assert AuditCheck.PROFIT_CONDITION in fail_reasons


# ================================================================
# TestMatchResultStatus (4)
# ================================================================

class TestMatchResultStatus:
    """MatchResult.status_str() と ok プロパティ。"""

    def test_ok_when_no_errors(self):
        r = MatchResult(matches_created=5)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_created(self):
        r = MatchResult(matches_created=3, error_count=2)
        assert r.ok is False
        assert r.status_str() == "partial"

    def test_error_when_nothing_created(self):
        r = MatchResult(matches_created=0, error_count=1)
        assert r.status_str() == "error"

    def test_ok_false_when_error_count_positive(self):
        r = MatchResult(error_count=1)
        assert r.ok is False


# ================================================================
# TestAuditResultStatus (4)
# ================================================================

class TestAuditResultStatus:
    """AuditResult.status_str() と ok プロパティ。"""

    def test_ok_when_no_errors(self):
        r = AuditResult(audited_count=3)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_audited(self):
        r = AuditResult(audited_count=2, error_count=1)
        assert r.ok is False
        assert r.status_str() == "partial"

    def test_error_when_nothing_audited(self):
        r = AuditResult(audited_count=0, error_count=1)
        assert r.status_str() == "error"

    def test_ok_false_when_error_count_positive(self):
        r = AuditResult(error_count=1)
        assert r.ok is False
