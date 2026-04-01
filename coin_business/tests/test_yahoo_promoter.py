"""
tests/test_yahoo_promoter.py
==============================
db.yahoo_promoter_repo モジュールのユニットテスト。

テスト項目:
  1. promote_to_main -- APPROVED_TO_MAIN の正常系
  2. promote_to_main -- PENDING_CEO / HELD / REJECTED は昇格しない
  3. promote_to_main -- yahoo_lot_id が空は昇格しない
  4. promote_to_main -- yahoo_sold_lots への upsert が呼ばれること
  5. promote_to_main -- staging の status が PROMOTED に更新されること
  6. promote_to_main -- 本DB upsert 失敗時は False を返す
  7. load_approved_staging -- APPROVED_TO_MAIN のみ取得
  8. count_promotable -- 件数返却
  9. get_approval_info -- 承認情報取得
  10. PromoteResult デフォルト値
  11. 統合テスト (DB接続あり, @pytest.mark.integration)

実行:
  cd coin_business
  python -m pytest tests/test_yahoo_promoter.py -v -m "not integration"
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from db.yahoo_promoter_repo import (
    PromoteResult,
    count_promotable,
    get_approval_info,
    load_approved_staging,
    promote_to_main,
    record_promoter_run,
)
from constants import YahooStagingStatus, Table


# ================================================================
# fixtures
# ================================================================

FAKE_STAGING_ID   = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
FAKE_LOT_ID       = "m99887766"
FAKE_MAIN_LOT_ID  = "11112222-3333-4444-5555-666677778888"

APPROVED_STAGING_REC = {
    "id":               FAKE_STAGING_ID,
    "yahoo_lot_id":     FAKE_LOT_ID,
    "lot_title":        "1921 Morgan Dollar NGC MS63",
    "title_normalized": "1921 Morgan Dollar NGC MS63",
    "year":             1921,
    "denomination":     "Morgan Dollar",
    "cert_company":     "NGC",
    "cert_number":      "12345678",
    "grade_text":       "MS63",
    "sold_price_jpy":   50000,
    "sold_date":        "2024-03-15",
    "parse_confidence": 0.90,
    "status":           YahooStagingStatus.APPROVED_TO_MAIN,
}


@pytest.fixture
def mock_client_ok():
    """正常系クライアントモック"""
    client = MagicMock()

    # upsert レスポンス (yahoo_sold_lots)
    upsert_resp = MagicMock()
    upsert_resp.data = [{"id": FAKE_MAIN_LOT_ID, "yahoo_lot_id": FAKE_LOT_ID}]
    client.table.return_value.upsert.return_value.execute.return_value = upsert_resp

    # update レスポンス (staging status → PROMOTED)
    update_resp = MagicMock()
    update_resp.data = [{"id": FAKE_STAGING_ID, "status": "PROMOTED"}]
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_resp

    # select (count_promotable)
    count_resp = MagicMock()
    count_resp.count = 3
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = count_resp

    return client


@pytest.fixture
def mock_client_upsert_fail():
    """upsert 失敗クライアントモック"""
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.side_effect = Exception("DB error")
    return client


# ================================================================
# 1. 正常系 -- APPROVED_TO_MAIN の昇格
# ================================================================

class TestPromoteApproved:
    def test_approved_returns_true(self, mock_client_ok):
        result = promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        assert result is True

    def test_approved_calls_upsert_on_main_table(self, mock_client_ok):
        promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        table_calls = [str(c) for c in mock_client_ok.table.call_args_list]
        assert any(Table.YAHOO_SOLD_LOTS in c for c in table_calls)

    def test_approved_calls_update_on_staging(self, mock_client_ok):
        promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        table_calls = [str(c) for c in mock_client_ok.table.call_args_list]
        assert any(Table.YAHOO_SOLD_LOTS_STAGING in c for c in table_calls)

    def test_approved_sets_staging_to_promoted(self, mock_client_ok):
        promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        update_call = mock_client_ok.table.return_value.update.call_args
        assert update_call is not None
        update_data = update_call[0][0]
        assert update_data["status"] == YahooStagingStatus.PROMOTED

    def test_approved_with_approved_by(self, mock_client_ok):
        result = promote_to_main(
            mock_client_ok, APPROVED_STAGING_REC,
            approved_by="cap", approved_at="2024-03-15T09:00:00+00:00",
        )
        assert result is True

    def test_upsert_includes_source_staging_id(self, mock_client_ok):
        promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        upsert_call = mock_client_ok.table.return_value.upsert.call_args
        sent_rec = upsert_call[0][0]
        assert sent_rec["source_staging_id"] == FAKE_STAGING_ID

    def test_upsert_includes_yahoo_lot_id(self, mock_client_ok):
        promote_to_main(mock_client_ok, APPROVED_STAGING_REC)
        upsert_call = mock_client_ok.table.return_value.upsert.call_args
        sent_rec = upsert_call[0][0]
        assert sent_rec["yahoo_lot_id"] == FAKE_LOT_ID


# ================================================================
# 2. ガード -- 非承認ステータスは昇格しない
# ================================================================

class TestPromoteStatusGuard:
    """PENDING_CEO / HELD / REJECTED は昇格させない。"""

    @pytest.mark.parametrize("forbidden_status", [
        YahooStagingStatus.PENDING_CEO,
        YahooStagingStatus.HELD,
        YahooStagingStatus.REJECTED,
        YahooStagingStatus.PROMOTED,   # 既に昇格済みも再昇格させない
    ])
    def test_forbidden_status_returns_false(self, mock_client_ok, forbidden_status):
        rec = dict(APPROVED_STAGING_REC)
        rec["status"] = forbidden_status
        result = promote_to_main(mock_client_ok, rec)
        assert result is False

    @pytest.mark.parametrize("forbidden_status", [
        YahooStagingStatus.PENDING_CEO,
        YahooStagingStatus.HELD,
        YahooStagingStatus.REJECTED,
    ])
    def test_forbidden_status_no_db_write(self, mock_client_ok, forbidden_status):
        """非承認ステータスでは DB に書かないこと。"""
        rec = dict(APPROVED_STAGING_REC)
        rec["status"] = forbidden_status
        promote_to_main(mock_client_ok, rec)
        mock_client_ok.table.return_value.upsert.assert_not_called()


# ================================================================
# 3. ガード -- yahoo_lot_id が空は昇格しない
# ================================================================

class TestPromoteEmptyLotId:
    def test_empty_lot_id_returns_false(self, mock_client_ok):
        rec = dict(APPROVED_STAGING_REC)
        rec["yahoo_lot_id"] = ""
        result = promote_to_main(mock_client_ok, rec)
        assert result is False

    def test_none_lot_id_returns_false(self, mock_client_ok):
        rec = dict(APPROVED_STAGING_REC)
        rec["yahoo_lot_id"] = None
        result = promote_to_main(mock_client_ok, rec)
        assert result is False

    def test_empty_lot_id_no_db_write(self, mock_client_ok):
        rec = dict(APPROVED_STAGING_REC)
        rec["yahoo_lot_id"] = None
        promote_to_main(mock_client_ok, rec)
        mock_client_ok.table.return_value.upsert.assert_not_called()


# ================================================================
# 4. upsert 失敗時
# ================================================================

class TestPromoteUpsertFailure:
    def test_upsert_fail_returns_false(self, mock_client_upsert_fail):
        result = promote_to_main(mock_client_upsert_fail, APPROVED_STAGING_REC)
        assert result is False

    def test_upsert_fail_does_not_update_staging(self, mock_client_upsert_fail):
        """本DB への upsert が失敗した場合、staging の更新も行わないこと。"""
        promote_to_main(mock_client_upsert_fail, APPROVED_STAGING_REC)
        mock_client_upsert_fail.table.return_value.update.assert_not_called()


# ================================================================
# 5. count_promotable
# ================================================================

class TestCountPromotable:
    def test_returns_count(self, mock_client_ok):
        n = count_promotable(mock_client_ok)
        assert n == 3

    def test_db_error_returns_minus_one(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("DB error")
        result = count_promotable(client)
        assert result == -1


# ================================================================
# 6. load_approved_staging
# ================================================================

class TestLoadApprovedStaging:
    def test_returns_list(self):
        client = MagicMock()
        resp = MagicMock()
        resp.data = [APPROVED_STAGING_REC, APPROVED_STAGING_REC]
        (client.table.return_value.select.return_value
         .eq.return_value.order.return_value.range.return_value.execute.return_value) = resp
        records = load_approved_staging(client)
        assert isinstance(records, list)
        assert len(records) == 2

    def test_db_error_returns_empty_list(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        result = load_approved_staging(client)
        assert result == []


# ================================================================
# 7. get_approval_info
# ================================================================

class TestGetApprovalInfo:
    def test_returns_approved_by_and_at(self):
        client = MagicMock()
        resp = MagicMock()
        resp.data = [{"reviewer": "cap", "reviewed_at": "2024-03-15T09:00:00+00:00"}]
        (client.table.return_value.select.return_value
         .eq.return_value.eq.return_value.order.return_value
         .limit.return_value.execute.return_value) = resp
        info = get_approval_info(client, FAKE_STAGING_ID)
        assert info["approved_by"] == "cap"
        assert info["approved_at"] == "2024-03-15T09:00:00+00:00"

    def test_no_review_returns_empty_dict(self):
        client = MagicMock()
        resp = MagicMock()
        resp.data = []
        (client.table.return_value.select.return_value
         .eq.return_value.eq.return_value.order.return_value
         .limit.return_value.execute.return_value) = resp
        info = get_approval_info(client, FAKE_STAGING_ID)
        assert info == {}

    def test_db_error_returns_empty_dict(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        info = get_approval_info(client, FAKE_STAGING_ID)
        assert info == {}


# ================================================================
# 8. PromoteResult デフォルト値
# ================================================================

class TestPromoteResultDefaults:
    def test_default_ok_is_true(self):
        r = PromoteResult()
        assert r.ok is True

    def test_default_promoted_count_is_zero(self):
        r = PromoteResult()
        assert r.promoted_count == 0

    def test_default_skipped_count_is_zero(self):
        r = PromoteResult()
        assert r.skipped_count == 0

    def test_default_error_count_is_zero(self):
        r = PromoteResult()
        assert r.error_count == 0

    def test_total_processed(self):
        r = PromoteResult(promoted_count=3, skipped_count=1, error_count=2)
        assert r.total_processed == 6


# ================================================================
# 9. 統合テスト (DB 接続あり)
# ================================================================

@pytest.mark.integration
class TestIntegrationPromoter:
    """
    実際の Supabase に接続して動作を確認するテスト。
    SUPABASE_URL / SUPABASE_KEY が設定されている場合のみ実行する。
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_env(self):
        import os
        if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
            pytest.skip("SUPABASE_URL / SUPABASE_KEY 未設定のためスキップ")

    def test_promote_flow_e2e(self):
        """
        staging レコードを用意 → approve → promote → PROMOTED になること。
        """
        from scripts.supabase_client import get_client
        from db.yahoo_repo import upsert_staging_records
        from db.yahoo_review_repo import save_review_decision

        client = get_client()
        test_lot_id = "test_promoter_e2e_001"

        # staging レコードを用意
        upsert_staging_records(client, [{
            "yahoo_lot_id":     test_lot_id,
            "lot_title":        "E2E Test Promoter NGC MS63",
            "title_normalized": "E2E Test Promoter NGC MS63",
            "sold_price_jpy":   50000,
            "sold_date":        "2024-01-01",
            "status":           YahooStagingStatus.PENDING_CEO,
            "parse_confidence": 0.80,
        }])

        # staging_id 取得
        resp = client.table("yahoo_sold_lots_staging").select("id").eq(
            "yahoo_lot_id", test_lot_id
        ).limit(1).execute()
        assert resp.data
        staging_id = resp.data[0]["id"]

        # approve
        review_result = save_review_decision(
            client, staging_id, "approved", reviewer="cap",
        )
        assert review_result.ok

        # staging の status が APPROVED_TO_MAIN になっていることを確認
        staging = client.table("yahoo_sold_lots_staging").select("status").eq(
            "id", staging_id
        ).limit(1).execute()
        assert staging.data[0]["status"] == YahooStagingStatus.APPROVED_TO_MAIN

        # 昇格実行
        rec = client.table("yahoo_sold_lots_staging").select("*").eq(
            "id", staging_id
        ).limit(1).execute().data[0]
        ok = promote_to_main(client, rec, approved_by="cap")
        assert ok

        # yahoo_sold_lots に存在することを確認
        main_lot = client.table("yahoo_sold_lots").select("*").eq(
            "yahoo_lot_id", test_lot_id
        ).limit(1).execute()
        assert main_lot.data
        assert main_lot.data[0]["source_staging_id"] == staging_id

        # staging が PROMOTED になっていることを確認
        staging2 = client.table("yahoo_sold_lots_staging").select("status").eq(
            "id", staging_id
        ).limit(1).execute()
        assert staging2.data[0]["status"] == YahooStagingStatus.PROMOTED

        # クリーンアップ
        try:
            client.table("yahoo_sold_lots").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
            client.table("yahoo_sold_lots_staging").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
        except Exception:
            pass
