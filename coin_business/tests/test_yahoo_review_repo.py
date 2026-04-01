"""
tests/test_yahoo_review_repo.py
==================================
db.yahoo_review_repo モジュールのユニットテスト。

テスト項目:
  1. save_review_decision -- approve / reject / hold の正常系
  2. save_review_decision -- 無効な decision のバリデーション
  3. save_review_decision -- ダブルレビュー警告
  4. save_review_decision -- staging_id が空のバリデーション
  5. DECISION_TO_STATUS マッピング確認
  6. ReviewResult のデフォルト値確認
  7. 統合テスト (DB接続あり, @pytest.mark.integration)

実行:
  cd coin_business
  python -m pytest tests/test_yahoo_review_repo.py -v -m "not integration"
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from db.yahoo_review_repo import (
    DECISION_TO_STATUS,
    VALID_DECISIONS,
    ReviewResult,
    save_review_decision,
    get_review_history,
    load_pending_review,
    count_pending_review,
)
from constants import YahooStagingStatus


# ================================================================
# fixtures
# ================================================================

FAKE_STAGING_ID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
FAKE_REVIEW_UUID = "11112222-3333-4444-5555-666677778888"


@pytest.fixture
def mock_client_ok():
    """
    正常系クライアントモック:
      - insert → FAKE_REVIEW_UUID を返す
      - update → 成功
    """
    client = MagicMock()

    # insert レスポンス
    insert_resp = MagicMock()
    insert_resp.data = [{"id": FAKE_REVIEW_UUID}]
    client.table.return_value.insert.return_value.execute.return_value = insert_resp

    # update レスポンス
    update_resp = MagicMock()
    update_resp.data = [{"id": FAKE_STAGING_ID, "status": "updated"}]
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_resp

    # select (get_review_history 用) → 履歴なし
    select_resp = MagicMock()
    select_resp.data = []
    (client.table.return_value.select.return_value
     .eq.return_value.order.return_value.execute.return_value) = select_resp

    return client


@pytest.fixture
def mock_client_with_history():
    """履歴が1件ある状態のモック。"""
    client = MagicMock()

    # insert レスポンス
    insert_resp = MagicMock()
    insert_resp.data = [{"id": FAKE_REVIEW_UUID}]
    client.table.return_value.insert.return_value.execute.return_value = insert_resp

    # update レスポンス
    update_resp = MagicMock()
    update_resp.data = [{"id": FAKE_STAGING_ID}]
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_resp

    # select → 既存履歴あり (approved)
    select_resp = MagicMock()
    select_resp.data = [{
        "id":          "old-review-uuid",
        "decision":    "approved",
        "reason":      None,
        "reviewer":    "ceo",
        "review_note": None,
        "reviewed_at": "2024-03-15T09:00:00+00:00",
    }]
    (client.table.return_value.select.return_value
     .eq.return_value.order.return_value.execute.return_value) = select_resp

    return client


# ================================================================
# 1. 正常系 -- approve
# ================================================================

class TestSaveReviewApprove:
    """approve 決定の正常系テスト。"""

    def test_approve_returns_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "approved", reviewer="ceo"
        )
        assert result.ok is True

    def test_approve_sets_staging_status(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "approved", reviewer="ceo"
        )
        assert result.staging_status == YahooStagingStatus.APPROVED_TO_MAIN

    def test_approve_saves_review_id(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "approved", reviewer="ceo"
        )
        assert result.review_id == FAKE_REVIEW_UUID

    def test_approve_no_error(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "approved"
        )
        assert result.error == ""

    def test_approve_calls_insert_on_reviews_table(self, mock_client_ok):
        save_review_decision(mock_client_ok, FAKE_STAGING_ID, "approved")
        # reviews テーブルへの insert が呼ばれたことを確認
        table_calls = [str(c) for c in mock_client_ok.table.call_args_list]
        assert any("yahoo_sold_lot_reviews" in c for c in table_calls)

    def test_approve_calls_update_on_staging_table(self, mock_client_ok):
        save_review_decision(mock_client_ok, FAKE_STAGING_ID, "approved")
        # staging テーブルへの update が呼ばれたことを確認
        table_calls = [str(c) for c in mock_client_ok.table.call_args_list]
        assert any("yahoo_sold_lots_staging" in c for c in table_calls)

    def test_approve_with_reason_and_note(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok,
            FAKE_STAGING_ID,
            "approved",
            reviewer    = "cap",
            reason      = "cert 一致",
            review_note = "良品",
        )
        assert result.ok is True


# ================================================================
# 2. 正常系 -- reject
# ================================================================

class TestSaveReviewReject:
    def test_reject_returns_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "rejected"
        )
        assert result.ok is True

    def test_reject_sets_staging_status(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "rejected"
        )
        assert result.staging_status == YahooStagingStatus.REJECTED


# ================================================================
# 3. 正常系 -- hold
# ================================================================

class TestSaveReviewHold:
    def test_hold_returns_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "held"
        )
        assert result.ok is True

    def test_hold_sets_staging_status(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "held"
        )
        assert result.staging_status == YahooStagingStatus.HELD


# ================================================================
# 4. バリデーション -- 無効な decision
# ================================================================

class TestInvalidDecision:
    def test_invalid_decision_returns_not_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "unknown_status"
        )
        assert result.ok is False

    def test_invalid_decision_has_error_message(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "unknown_status"
        )
        assert result.error != ""
        assert "unknown_status" in result.error.lower() or "decision" in result.error.lower()

    def test_invalid_decision_no_db_call(self, mock_client_ok):
        """バリデーション失敗時は DB に触れないこと。"""
        save_review_decision(mock_client_ok, FAKE_STAGING_ID, "bad")
        mock_client_ok.table.assert_not_called()

    def test_empty_decision_returns_not_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, ""
        )
        assert result.ok is False

    def test_none_staging_id_returns_not_ok(self, mock_client_ok):
        result = save_review_decision(
            mock_client_ok, "", "approved"
        )
        assert result.ok is False


# ================================================================
# 5. ダブルレビュー警告
# ================================================================

class TestDoubleReviewWarning:
    def test_double_approve_triggers_warning(self, mock_client_with_history):
        """既に approved の案件を再度 approved すると warning が出る。"""
        result = save_review_decision(
            mock_client_with_history, FAKE_STAGING_ID, "approved"
        )
        assert result.ok is True
        assert result.warning != ""

    def test_double_approve_still_saves(self, mock_client_with_history):
        """警告が出ても保存は実行される。"""
        result = save_review_decision(
            mock_client_with_history, FAKE_STAGING_ID, "approved"
        )
        assert result.review_id == FAKE_REVIEW_UUID

    def test_held_to_approve_no_warning(self, mock_client_ok):
        """履歴なし → approve は警告なし。"""
        result = save_review_decision(
            mock_client_ok, FAKE_STAGING_ID, "approved"
        )
        assert result.warning == ""


# ================================================================
# 6. DECISION_TO_STATUS マッピング
# ================================================================

class TestDecisionToStatusMapping:
    def test_approved_maps_to_approved_to_main(self):
        assert DECISION_TO_STATUS["approved"] == YahooStagingStatus.APPROVED_TO_MAIN

    def test_rejected_maps_to_rejected(self):
        assert DECISION_TO_STATUS["rejected"] == YahooStagingStatus.REJECTED

    def test_held_maps_to_held(self):
        assert DECISION_TO_STATUS["held"] == YahooStagingStatus.HELD

    def test_all_valid_decisions_covered(self):
        """VALID_DECISIONS の全要素が DECISION_TO_STATUS に存在すること。"""
        for d in VALID_DECISIONS:
            assert d in DECISION_TO_STATUS, f"{d} が DECISION_TO_STATUS にない"


# ================================================================
# 7. ReviewResult デフォルト値
# ================================================================

class TestReviewResultDefaults:
    def test_default_ok_is_false(self):
        r = ReviewResult()
        assert r.ok is False

    def test_default_error_is_empty(self):
        r = ReviewResult()
        assert r.error == ""

    def test_default_warning_is_empty(self):
        r = ReviewResult()
        assert r.warning == ""

    def test_default_staging_status_is_empty(self):
        r = ReviewResult()
        assert r.staging_status == ""


# ================================================================
# 8. Integration テスト (DB接続あり)
# ================================================================

@pytest.mark.integration
class TestIntegrationReview:
    """
    実際の Supabase に接続して動作を確認するテスト。
    SUPABASE_URL / SUPABASE_KEY が設定されている場合のみ実行する。
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_env(self):
        import os
        if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
            pytest.skip("SUPABASE_URL / SUPABASE_KEY 未設定のためスキップ")

    def test_approve_flow_e2e(self):
        """
        staging レコードを1件 upsert → approve → status が APPROVED_TO_MAIN になること。
        """
        from scripts.supabase_client import get_client
        from db.yahoo_repo import upsert_staging_records
        from constants import YahooStagingStatus

        client = get_client()
        test_lot_id = "test_review_flow_e2e_001"

        # テスト用 staging レコードを用意
        upsert_staging_records(client, [{
            "yahoo_lot_id":     test_lot_id,
            "lot_title":        "E2E Test Coin NGC MS63",
            "title_normalized": "E2E Test Coin NGC MS63",
            "sold_price_jpy":   50000,
            "sold_date":        "2024-01-01",
            "status":           YahooStagingStatus.PENDING_CEO,
            "parse_confidence": 0.65,
        }])

        # staging_id を取得
        resp = client.table("yahoo_sold_lots_staging").select("id").eq(
            "yahoo_lot_id", test_lot_id
        ).limit(1).execute()
        assert resp.data, "テスト staging レコードが見つからない"
        staging_id = resp.data[0]["id"]

        # approve
        result = save_review_decision(
            client, staging_id, "approved",
            reviewer    = "cap",
            review_note = "E2E テスト用承認",
        )
        assert result.ok, f"approve 失敗: {result.error}"
        assert result.staging_status == YahooStagingStatus.APPROVED_TO_MAIN

        # レビュー履歴確認
        history = get_review_history(client, staging_id)
        assert len(history) >= 1
        assert history[0]["decision"] == "approved"

        # staging の status 確認
        staging = client.table("yahoo_sold_lots_staging").select("status").eq(
            "id", staging_id
        ).limit(1).execute()
        assert staging.data[0]["status"] == YahooStagingStatus.APPROVED_TO_MAIN

        # クリーンアップ
        try:
            client.table("yahoo_sold_lots_staging").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
        except Exception:
            pass
