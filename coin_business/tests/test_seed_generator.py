"""
tests/test_seed_generator.py
==============================
seeds/builder.py と scripts/seed_generator.py のユニットテスト。

テスト項目:
  1. build_seeds_for_lot -- CERT_EXACT seed 生成
  2. build_seeds_for_lot -- CERT_TITLE seed 生成
  3. build_seeds_for_lot -- TITLE_NORMALIZED seed 生成
  4. build_seeds_for_lot -- YEAR_DENOM_GRADE seed 生成
  5. build_seeds_for_lot -- cert なし (CERT_EXACT / CERT_TITLE は生成されない)
  6. build_seeds_for_lot -- 短すぎる title は TITLE_NORMALIZED を生成しない
  7. build_seeds_for_lot -- 生成 seed の seed_status は READY
  8. build_seeds_for_lot -- 生成 seed の priority_score が正しい
  9. build_seeds_for_lot -- staging leak テスト (yahoo_lot_id なし → 空リスト)
  10. build_search_query  -- search_query あり / なし
  11. seed_generator -- yahoo_sold_lots のみ参照 (staging 参照なし)
  12. seed_generator -- dry_run は DB を呼ばない
  13. SeedType.PRIORITY  -- 全種別に優先度が定義されている
  14. 統合テスト (DB接続あり, @pytest.mark.integration)

実行:
  cd coin_business
  python -m pytest tests/test_seed_generator.py -v -m "not integration"
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from seeds.builder import build_seeds_for_lot, build_search_query
from constants import SeedType, SeedStatus


# ================================================================
# fixtures
# ================================================================

FAKE_LOT_WITH_CERT = {
    "id":               "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
    "yahoo_lot_id":     "m99887766",
    "lot_title":        "1921 Morgan Dollar NGC MS63 #12345678",
    "title_normalized": "1921 Morgan Dollar NGC MS63 12345678",
    "year":             1921,
    "denomination":     "Morgan Dollar",
    "cert_company":     "NGC",
    "cert_number":      "12345678",
    "grade_text":       "MS63",
    "sold_price_jpy":   50000,
    "sold_date":        "2024-03-15",
    "parse_confidence": 0.90,
}

FAKE_LOT_NO_CERT = {
    "id":               "bbbbcccc-dddd-eeee-ffff-000011112222",
    "yahoo_lot_id":     "m11223344",
    "lot_title":        "1921 Morgan Silver Dollar old coin",
    "title_normalized": "1921 Morgan Silver Dollar old coin",
    "year":             1921,
    "denomination":     "Dollar",
    "cert_company":     None,
    "cert_number":      None,
    "grade_text":       None,
    "sold_price_jpy":   30000,
    "sold_date":        "2024-02-01",
    "parse_confidence": 0.20,
}

FAKE_LOT_SHORT_TITLE = {
    "id":               "ccccdddd-eeee-ffff-0000-111122223333",
    "yahoo_lot_id":     "m55667788",
    "lot_title":        "NGC MS63",   # 10文字未満
    "title_normalized": "NGC MS63",
    "year":             None,
    "denomination":     None,
    "cert_company":     "NGC",
    "cert_number":      "99999",
    "grade_text":       "MS63",
    "sold_price_jpy":   10000,
    "sold_date":        "2024-01-01",
    "parse_confidence": 0.45,
}


# ================================================================
# 1-4. build_seeds_for_lot -- seed 種別生成
# ================================================================

class TestBuildSeedsWithCert:
    """cert あり lot の seed 生成テスト。"""

    def test_generates_cert_exact(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.CERT_EXACT in types

    def test_generates_cert_title(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.CERT_TITLE in types

    def test_generates_title_normalized(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.TITLE_NORMALIZED in types

    def test_generates_year_denom_grade(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.YEAR_DENOM_GRADE in types

    def test_cert_exact_query_contains_cert_number(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        cert_exact = next(s for s in seeds if s["seed_type"] == SeedType.CERT_EXACT)
        assert "12345678" in cert_exact["search_query"]

    def test_cert_exact_query_contains_cert_company(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        cert_exact = next(s for s in seeds if s["seed_type"] == SeedType.CERT_EXACT)
        assert "NGC" in cert_exact["search_query"]

    def test_year_denom_grade_query_contains_year(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        ydg = next(s for s in seeds if s["seed_type"] == SeedType.YEAR_DENOM_GRADE)
        assert "1921" in ydg["search_query"]

    def test_year_denom_grade_query_contains_denomination(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        ydg = next(s for s in seeds if s["seed_type"] == SeedType.YEAR_DENOM_GRADE)
        assert "Morgan Dollar" in ydg["search_query"]

    def test_all_seeds_have_yahoo_lot_id(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        for s in seeds:
            assert s["yahoo_lot_id"] == "m99887766"


# ================================================================
# 5. cert なし lot の seed 生成
# ================================================================

class TestBuildSeedsNoCert:
    """cert_company / cert_number がない場合の動作確認。"""

    def test_no_cert_exact_without_cert(self):
        seeds = build_seeds_for_lot(FAKE_LOT_NO_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.CERT_EXACT not in types

    def test_no_cert_title_without_cert(self):
        seeds = build_seeds_for_lot(FAKE_LOT_NO_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.CERT_TITLE not in types

    def test_generates_title_normalized_without_cert(self):
        """cert なしでも TITLE_NORMALIZED は生成できる。"""
        seeds = build_seeds_for_lot(FAKE_LOT_NO_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.TITLE_NORMALIZED in types

    def test_generates_year_denom_without_cert(self):
        """year + denomination があれば YEAR_DENOM_GRADE は生成できる。"""
        seeds = build_seeds_for_lot(FAKE_LOT_NO_CERT)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.YEAR_DENOM_GRADE in types


# ================================================================
# 6. 短すぎる title は TITLE_NORMALIZED を生成しない
# ================================================================

class TestBuildSeedsShortTitle:
    def test_short_title_no_title_normalized(self):
        seeds = build_seeds_for_lot(FAKE_LOT_SHORT_TITLE)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.TITLE_NORMALIZED not in types

    def test_short_title_but_cert_exact_generated(self):
        """タイトルが短くても cert があれば CERT_EXACT は生成される。"""
        seeds = build_seeds_for_lot(FAKE_LOT_SHORT_TITLE)
        types = [s["seed_type"] for s in seeds]
        assert SeedType.CERT_EXACT in types


# ================================================================
# 7. seed_status は READY
# ================================================================

class TestSeedStatusReady:
    def test_all_seeds_status_is_ready(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        for s in seeds:
            assert s["seed_status"] == SeedStatus.READY, \
                f"seed_type={s['seed_type']} の seed_status が READY でない"


# ================================================================
# 8. priority_score
# ================================================================

class TestSeedPriorityScore:
    def test_cert_exact_highest_priority(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        cert_exact = next(s for s in seeds if s["seed_type"] == SeedType.CERT_EXACT)
        year_denom = next(s for s in seeds if s["seed_type"] == SeedType.YEAR_DENOM_GRADE)
        assert cert_exact["priority_score"] > year_denom["priority_score"]

    def test_cert_exact_priority_is_1_0(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        cert_exact = next(s for s in seeds if s["seed_type"] == SeedType.CERT_EXACT)
        assert cert_exact["priority_score"] == 1.0

    def test_all_priorities_between_0_and_1(self):
        seeds = build_seeds_for_lot(FAKE_LOT_WITH_CERT)
        for s in seeds:
            assert 0.0 <= s["priority_score"] <= 1.0, \
                f"seed_type={s['seed_type']} の priority_score={s['priority_score']} が範囲外"


# ================================================================
# 9. staging leak テスト -- yahoo_lot_id なし → 空リスト
# ================================================================

class TestSeedStagingLeakPrevention:
    def test_no_lot_id_returns_empty(self):
        """yahoo_lot_id がない場合は seed を生成しない (staging leak 防止)。"""
        lot = dict(FAKE_LOT_WITH_CERT)
        lot["yahoo_lot_id"] = None
        seeds = build_seeds_for_lot(lot)
        assert seeds == []

    def test_empty_lot_id_returns_empty(self):
        lot = dict(FAKE_LOT_WITH_CERT)
        lot["yahoo_lot_id"] = ""
        seeds = build_seeds_for_lot(lot)
        assert seeds == []

    def test_empty_lot_returns_empty(self):
        seeds = build_seeds_for_lot({})
        assert seeds == []


# ================================================================
# 10. build_search_query
# ================================================================

class TestBuildSearchQuery:
    def test_uses_existing_search_query(self):
        seed = {"search_query": "NGC 12345678", "seed_type": SeedType.CERT_EXACT}
        q = build_search_query(seed)
        assert q == "NGC 12345678"

    def test_builds_cert_exact_from_components(self):
        seed = {
            "search_query": "",
            "seed_type":    SeedType.CERT_EXACT,
            "cert_company": "PCGS",
            "cert_number":  "87654321",
        }
        q = build_search_query(seed)
        assert "PCGS" in q
        assert "87654321" in q

    def test_empty_seed_returns_empty_string(self):
        q = build_search_query({})
        assert q == ""


# ================================================================
# 11. seed_generator -- yahoo_sold_lots のみ参照 (staging 参照なし)
# ================================================================

class TestSeedGeneratorSourceTableGuard:
    """
    scripts/seed_generator.py が yahoo_sold_lots だけを参照し、
    yahoo_sold_lots_staging を参照しないことを確認する。
    """

    def test_load_main_lots_uses_yahoo_sold_lots(self):
        from scripts.seed_generator import load_main_lots
        from constants import Table

        client = MagicMock()
        resp = MagicMock()
        resp.data = []
        (client.table.return_value.select.return_value
         .order.return_value.range.return_value.execute.return_value) = resp

        load_main_lots(client)
        # table() が yahoo_sold_lots で呼ばれることを確認
        table_calls = [str(c) for c in client.table.call_args_list]
        assert any(Table.YAHOO_SOLD_LOTS in c for c in table_calls)

    def test_load_main_lots_does_not_use_staging(self):
        from scripts.seed_generator import load_main_lots
        from constants import Table

        client = MagicMock()
        resp = MagicMock()
        resp.data = []
        (client.table.return_value.select.return_value
         .order.return_value.range.return_value.execute.return_value) = resp

        load_main_lots(client)
        table_calls = [str(c) for c in client.table.call_args_list]
        # staging テーブルは呼ばれないこと
        assert not any(Table.YAHOO_SOLD_LOTS_STAGING in c for c in table_calls)


# ================================================================
# 12. seed_generator -- dry_run は DB に書かない
# ================================================================

class TestSeedGeneratorDryRun:
    def test_dry_run_does_not_call_upsert(self):
        from scripts.seed_generator import upsert_seeds

        client = MagicMock()
        seeds = [{"yahoo_lot_id": "m001", "seed_type": SeedType.CERT_EXACT}]
        count, errors = upsert_seeds(client, seeds, dry_run=True)

        client.table.assert_not_called()
        assert count == 1
        assert errors == 0

    def test_dry_run_returns_correct_count(self):
        from scripts.seed_generator import upsert_seeds

        client = MagicMock()
        seeds = [
            {"yahoo_lot_id": f"m{i:03d}", "seed_type": SeedType.CERT_EXACT}
            for i in range(5)
        ]
        count, errors = upsert_seeds(client, seeds, dry_run=True)
        assert count == 5
        assert errors == 0


# ================================================================
# 13. SeedType.PRIORITY -- 全種別に優先度が定義されている
# ================================================================

class TestSeedTypePriority:
    def test_all_seed_types_have_priority(self):
        for seed_type in SeedType.ALL:
            assert seed_type in SeedType.PRIORITY, \
                f"{seed_type} が SeedType.PRIORITY にない"

    def test_priorities_between_0_and_1(self):
        for seed_type, priority in SeedType.PRIORITY.items():
            assert 0.0 <= priority <= 1.0, \
                f"{seed_type} の priority={priority} が 0.0-1.0 範囲外"

    def test_cert_exact_highest(self):
        assert SeedType.PRIORITY[SeedType.CERT_EXACT] >= SeedType.PRIORITY[SeedType.CERT_TITLE]
        assert SeedType.PRIORITY[SeedType.CERT_TITLE] >= SeedType.PRIORITY[SeedType.TITLE_NORMALIZED]
        assert SeedType.PRIORITY[SeedType.TITLE_NORMALIZED] >= SeedType.PRIORITY[SeedType.YEAR_DENOM_GRADE]


# ================================================================
# 14. 統合テスト (DB 接続あり)
# ================================================================

@pytest.mark.integration
class TestIntegrationSeedGenerator:
    """
    実際の Supabase に接続して動作を確認するテスト。
    SUPABASE_URL / SUPABASE_KEY が設定されている場合のみ実行する。
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_env(self):
        import os
        if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
            pytest.skip("SUPABASE_URL / SUPABASE_KEY 未設定のためスキップ")

    def test_e2e_seed_generation(self):
        """
        yahoo_sold_lots にテストレコードを 1 件 upsert →
        seed を生成 → yahoo_coin_seeds に書かれること。
        """
        from scripts.supabase_client import get_client
        from scripts.seed_generator import run_seed_generator

        client = get_client()
        test_lot_id = "test_seed_e2e_001"

        # テスト用 yahoo_sold_lots レコードを用意
        client.table("yahoo_sold_lots").upsert({
            "yahoo_lot_id":     test_lot_id,
            "lot_title":        "E2E Test NGC MS63 12345678",
            "title_normalized": "E2E Test NGC MS63 12345678",
            "year":             1921,
            "denomination":     "Morgan Dollar",
            "cert_company":     "NGC",
            "cert_number":      "12345678",
            "grade_text":       "MS63",
            "sold_price_jpy":   50000,
            "sold_date":        "2024-01-01",
        }, on_conflict="yahoo_lot_id").execute()

        # seed 生成実行
        stats = run_seed_generator(dry_run=False, limit=10, new_only=False)
        assert stats["error_count"] == 0
        assert stats["seeds_generated"] >= 1

        # yahoo_coin_seeds に書かれていることを確認
        seeds = client.table("yahoo_coin_seeds").select("*").eq(
            "yahoo_lot_id", test_lot_id
        ).execute()
        assert len(seeds.data) >= 1

        # CERT_EXACT が生成されていること
        types = [s["seed_type"] for s in seeds.data]
        assert SeedType.CERT_EXACT in types

        # seed_status は READY
        for s in seeds.data:
            assert s["seed_status"] == SeedStatus.READY

        # クリーンアップ
        try:
            client.table("yahoo_coin_seeds").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
            client.table("yahoo_sold_lots").delete().eq(
                "yahoo_lot_id", test_lot_id
            ).execute()
        except Exception:
            pass
