"""
tests/test_yahoo_staging_repo.py
===================================
db.yahoo_repo モジュールのユニットテスト。

テスト項目:
  1. normalize_lot_record — 正規化出力の検証
  2. upsert_staging_records (dry-run) — 冪等性の検証
  3. upsert_staging_records — yahoo_lot_id なしレコードのスキップ
  4. upsert_staging_records — status が必ず PENDING_CEO になること
  5. 実DB upsert (integration) — 環境変数がある場合のみ実行

実行:
  cd coin_business

  # 単体テストのみ (DB 接続不要)
  python -m pytest tests/test_yahoo_staging_repo.py -v -m "not integration"

  # 統合テスト含む (SUPABASE_URL / SUPABASE_KEY が必要)
  python -m pytest tests/test_yahoo_staging_repo.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from yahoo.normalizer import normalize_lot_record, normalize_title
from db.yahoo_repo import (
    UpsertResult,
    upsert_staging_records,
)
from constants import YahooStagingStatus


# ================================================================
# fixtures
# ================================================================

@pytest.fixture
def sample_mt_row():
    """market_transactions の典型的な1行。"""
    return {
        "id":            9001,
        "title":         "1921 Morgan Silver Dollar NGC MS63 #12345678",
        "price_jpy":     45000,
        "sold_date":     "2024-03-15",
        "url":           "https://page.auctions.yahoo.co.jp/jp/auction/m99887766",
        "item_id":       "m99887766",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "grader":        "NGC",
        "grade":         "MS63",
        "year":          1921,
        "denomination":  None,
        "country":       "アメリカ",
    }


@pytest.fixture
def mock_client():
    """Supabase クライアントのモック。"""
    client = MagicMock()
    # upsert().execute() が data リストを返すよう設定
    upsert_resp = MagicMock()
    upsert_resp.data = [{"id": "uuid-1"}]
    client.table.return_value.upsert.return_value.execute.return_value = upsert_resp
    return client


# ================================================================
# 1. normalize_lot_record
# ================================================================

class TestNormalizeLotRecord:
    """normalize_lot_record の出力フィールドを検証する。"""

    def test_required_fields_present(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        # 最低限必要なフィールドが存在すること
        assert "yahoo_lot_id"       in rec
        assert "lot_title"          in rec
        assert "title_normalized"   in rec
        assert "sold_price_jpy"     in rec
        assert "sold_date"           in rec
        assert "parse_confidence"   in rec
        assert "status"             in rec

    def test_yahoo_lot_id_from_item_id(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec["yahoo_lot_id"] == "m99887766"

    def test_yahoo_lot_id_from_url(self, sample_mt_row):
        """item_id がない場合は URL から抽出する。"""
        row = dict(sample_mt_row)
        row["item_id"] = None
        rec = normalize_lot_record(row)
        assert rec["yahoo_lot_id"] == "m99887766"

    def test_yahoo_lot_id_override(self, sample_mt_row):
        """yahoo_listing_id 引数で上書きできる。"""
        rec = normalize_lot_record(sample_mt_row, yahoo_listing_id="override_id")
        assert rec["yahoo_lot_id"] == "override_id"

    def test_status_is_pending_ceo(self, sample_mt_row):
        """status は必ず PENDING_CEO"""
        rec = normalize_lot_record(sample_mt_row)
        assert rec["status"] == YahooStagingStatus.PENDING_CEO

    def test_sold_price_jpy(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec["sold_price_jpy"] == 45000

    def test_sold_at_normalized(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec["sold_date"] == "2024-03-15"

    def test_cert_company_extracted(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec.get("cert_company") == "NGC"

    def test_cert_number_extracted(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec.get("cert_number") == "12345678"

    def test_year_extracted(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        assert rec.get("year") == 1921

    def test_title_normalized_no_noise(self, sample_mt_row):
        """正規化されたタイトルにノイズキーワードが含まれないこと。"""
        row = dict(sample_mt_row)
        row["title"] = "1921 Morgan Dollar NGC MS63 送料無料 【美品】 ★★★"
        rec = normalize_lot_record(row)
        normalized = rec.get("title_normalized", "")
        assert "送料" not in normalized
        assert "美品" not in normalized

    def test_none_values_excluded(self, sample_mt_row):
        """None 値は出力 dict に含まれない。"""
        row = dict(sample_mt_row)
        row["thumbnail_url"] = None
        row["denomination"] = None
        rec = normalize_lot_record(row)
        # image_url が None の場合は key 自体が存在しない
        assert "image_url" not in rec or rec["image_url"] is not None

    def test_parse_confidence_between_0_and_1(self, sample_mt_row):
        rec = normalize_lot_record(sample_mt_row)
        conf = rec.get("parse_confidence", 0.0)
        assert 0.0 <= conf <= 1.0

    def test_empty_title(self):
        row = {"id": 1, "title": "", "item_id": "m12345", "price_jpy": 1000, "sold_date": "2024-01-01"}
        rec = normalize_lot_record(row)
        assert rec["yahoo_lot_id"] == "m12345"


# ================================================================
# 2. normalize_title
# ================================================================

class TestNormalizeTitle:
    """normalize_title の個別動作を検証する。"""

    def test_fullwidth_to_halfwidth(self):
        """全角英数が半角に変換されること。"""
        result = normalize_title("ＮＧＣ　ＭＳ６３　１９２１年")
        assert "NGC" in result
        assert "MS63" in result
        assert "1921" in result

    def test_noise_removed_send_free(self):
        result = normalize_title("NGC MS63 Morgan Dollar 送料無料")
        assert "送料" not in result

    def test_noise_removed_beautiful(self):
        result = normalize_title("NGC MS63 Morgan 美品")
        assert "美品" not in result

    def test_brackets_removed(self):
        result = normalize_title("NGC MS63 【レア品】 Morgan Dollar")
        assert "【" not in result
        assert "レア品" not in result

    def test_multiple_spaces_collapsed(self):
        result = normalize_title("NGC   MS63   1921")
        assert "  " not in result

    def test_empty_returns_empty(self):
        assert normalize_title("") == ""

    def test_none_returns_empty(self):
        assert normalize_title(None) == ""


# ================================================================
# 3. upsert_staging_records (dry-run)
# ================================================================

class TestUpsertStagingRecordsDryRun:
    """dry_run=True のときは DB に書かず件数のみ返すことを確認する。"""

    def test_dry_run_does_not_call_db(self, mock_client):
        records = [
            {"yahoo_lot_id": "m001", "lot_title": "Test Coin 1", "status": "PENDING_CEO"},
            {"yahoo_lot_id": "m002", "lot_title": "Test Coin 2", "status": "PENDING_CEO"},
        ]
        result = upsert_staging_records(mock_client, records, dry_run=True)
        # DB の upsert が呼ばれていないこと
        mock_client.table.assert_not_called()
        assert result.upserted_count == 2

    def test_dry_run_returns_correct_counts(self, mock_client):
        records = [
            {"yahoo_lot_id": f"m{i:03d}", "lot_title": f"Coin {i}"} for i in range(5)
        ]
        result = upsert_staging_records(mock_client, records, dry_run=True)
        assert result.total_submitted == 5
        assert result.upserted_count == 5
        assert result.error_count == 0


# ================================================================
# 4. upsert_staging_records — lot_id なしスキップ
# ================================================================

class TestUpsertSkipsNullLotId:
    """yahoo_lot_id が None のレコードはスキップされることを確認する。"""

    def test_null_lot_id_skipped(self, mock_client):
        records = [
            {"yahoo_lot_id": "m001", "lot_title": "Valid Coin"},
            {"yahoo_lot_id": None,   "lot_title": "No ID Coin"},
            {"lot_title":    "No Key Coin"},  # key 自体なし
        ]
        result = upsert_staging_records(mock_client, records, dry_run=True)
        assert result.skipped_count == 2
        assert result.upserted_count == 1

    def test_empty_string_lot_id_skipped(self, mock_client):
        records = [
            {"yahoo_lot_id": "", "lot_title": "Empty ID Coin"},
        ]
        result = upsert_staging_records(mock_client, records, dry_run=True)
        assert result.skipped_count == 1
        assert result.upserted_count == 0


# ================================================================
# 5. upsert_staging_records — status が PENDING_CEO に強制される
# ================================================================

class TestUpsertForcePendingCeo:
    """status に何を入れても upsert 時は PENDING_CEO に上書きされることを確認する。"""

    def test_status_overridden_to_pending_ceo(self, mock_client):
        records = [
            {"yahoo_lot_id": "m001", "lot_title": "Coin", "status": "APPROVED_TO_MAIN"},
        ]
        # dry_run=False で実際に mock_client.table().upsert() が呼ばれることを確認
        result = upsert_staging_records(mock_client, records, dry_run=False)

        # upsert が呼ばれた引数を検証
        upsert_call_args = mock_client.table.return_value.upsert.call_args
        assert upsert_call_args is not None
        sent_records = upsert_call_args[0][0]  # positional arg 0
        assert sent_records[0]["status"] == YahooStagingStatus.PENDING_CEO


# ================================================================
# 6. 冪等性テスト (dry_run で確認)
# ================================================================

class TestIdempotency:
    """同じ yahoo_lot_id を2回送っても問題ないことを確認する (dry_run)。"""

    def test_same_id_twice_no_error(self, mock_client):
        records = [
            {"yahoo_lot_id": "m001", "lot_title": "Coin A"},
            {"yahoo_lot_id": "m001", "lot_title": "Coin A (duplicate)"},
        ]
        result = upsert_staging_records(mock_client, records, dry_run=True)
        # dry_run では両方カウントされる (DB 側が on_conflict で解決)
        assert result.error_count == 0


# ================================================================
# 7. Integration テスト (DB 接続あり)
# ================================================================

@pytest.mark.integration
class TestIntegrationWithDB:
    """
    実際の Supabase に接続して動作を確認するテスト。
    SUPABASE_URL / SUPABASE_KEY が設定されている場合のみ実行する。
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_env(self):
        if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
            pytest.skip("SUPABASE_URL / SUPABASE_KEY 未設定のためスキップ")

    def test_upsert_and_idempotent(self):
        """upsert が冪等に動作することを実 DB で確認する。"""
        from scripts.supabase_client import get_client
        from db.yahoo_repo import get_already_synced_ids

        client = get_client()
        test_lot_id = "test_idempotent_001"

        records = [{
            "yahoo_lot_id":     test_lot_id,
            "lot_title":        "Integration Test Coin NGC MS63",
            "title_normalized": "Integration Test Coin NGC MS63",
            "sold_price_jpy":   99999,
            "sold_at":          "2024-01-01",
            "status":           YahooStagingStatus.PENDING_CEO,
            "parse_confidence": 0.65,
        }]

        # 1回目 upsert
        result1 = upsert_staging_records(client, records)
        assert result1.upserted_count == 1
        assert result1.error_count == 0

        # 2回目 upsert (同一 yahoo_lot_id)
        result2 = upsert_staging_records(client, records)
        assert result2.error_count == 0  # エラーなし (ON CONFLICT UPDATE)

        # get_already_synced_ids で確認
        existing = get_already_synced_ids(client, [test_lot_id])
        assert test_lot_id in existing

        # クリーンアップ
        try:
            client.table("yahoo_sold_lots_staging").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
        except Exception:
            pass  # クリーンアップ失敗は許容
