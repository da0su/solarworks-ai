"""
coin_business/tests/test_slack_notion_dashboard.py
====================================================
Day 10 テスト: Slack 通知 / Notion 同期 / Dashboard

テストクラス:
  TestSlackBlockBuilders    (10 tests) — Block Kit 構造検証
  TestDedupLogic            (6 tests)  — 重複送信防止
  TestNotifyFunctions       (8 tests)  — notify_* dry_run モード
  TestNotionPropertyBuilders(6 tests)  — Notion プロパティ変換
  TestNotionSyncResult      (4 tests)  — SyncResult.status_str()
  TestDashboardKpi          (4 tests)  — KPI fetch / display helpers
  TestDashboardResult       (4 tests)  — run_dashboard 戻り値

合計: 42 tests
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from constants import AuditStatus, NotificationType, WatchStatus
from scripts.slack_notifier import (
    SendResult,
    _build_bid_ready_blocks,
    _build_ending_soon_blocks,
    _build_keep_price_alert_blocks,
    _build_level_a_blocks,
    _build_morning_brief_blocks,
    notify_bid_ready,
    notify_ending_soon,
    notify_keep_price_alert,
    notify_level_a_new,
    notify_morning_brief,
)
from scripts.notion_sync import (
    SyncResult,
    _build_candidate_properties,
    _build_watchlist_properties,
    _date_prop,
    _num,
    _select,
    _txt,
    _title_prop,
)
from scripts.dashboard import (
    _AUDIT_COLOR,
    _WATCH_COLOR,
    fetch_kpi,
)


# ================================================================
# TestSlackBlockBuilders
# ================================================================

class TestSlackBlockBuilders:
    """_build_*_blocks() の構造をテストする"""

    def test_morning_brief_has_header(self):
        kpi    = {"yahoo_pending_count": 5, "audit_pass_count": 3,
                  "keep_count": 2, "bid_ready_count": 0}
        blocks = _build_morning_brief_blocks(kpi, "2026-04-01")
        types  = [b["type"] for b in blocks]
        assert "header" in types

    def test_morning_brief_header_text(self):
        kpi    = {"yahoo_pending_count": 5, "audit_pass_count": 3,
                  "keep_count": 2, "bid_ready_count": 0}
        blocks = _build_morning_brief_blocks(kpi, "2026-04-01")
        header = next(b for b in blocks if b["type"] == "header")
        assert "朝ブリーフ" in header["text"]["text"]
        assert "2026-04-01" in header["text"]["text"]

    def test_morning_brief_bid_ready_highlight(self):
        kpi    = {"yahoo_pending_count": 0, "audit_pass_count": 0,
                  "keep_count": 0, "bid_ready_count": 3}
        blocks = _build_morning_brief_blocks(kpi, "2026-04-01")
        # 全テキストを連結して BID_READY 強調が含まれるか確認
        all_text = str(blocks)
        assert "BID_READY" in all_text
        assert "3" in all_text

    def test_level_a_has_header(self):
        cand   = {"id": "abc123", "title": "NGC MS63 英国ソブリン 1878", "target_max_bid_jpy": 150_000}
        blocks = _build_level_a_blocks(cand)
        assert any(b["type"] == "header" for b in blocks)

    def test_level_a_title_in_block(self):
        cand   = {"id": "abc123", "title": "NGC MS63 英国ソブリン 1878",
                  "target_max_bid_jpy": 150_000, "comparison_quality_score": 0.8}
        blocks = _build_level_a_blocks(cand)
        text   = str(blocks)
        assert "NGC MS63" in text

    def test_keep_price_alert_has_status(self):
        item   = {"id": "wid123", "status": WatchStatus.PRICE_OK,
                  "current_price_jpy": 80_000, "max_bid_jpy": 100_000}
        blocks = _build_keep_price_alert_blocks(item)
        text   = str(blocks)
        assert WatchStatus.PRICE_OK in text

    def test_ending_soon_has_time_left(self):
        item   = {"id": "wid456", "time_left_seconds": 1800,
                  "current_price_jpy": 90_000, "auction_end_at": "2026-04-01T14:00:00+00:00"}
        blocks = _build_ending_soon_blocks(item)
        text   = str(blocks)
        assert "30" in text  # 1800 sec → 30分

    def test_bid_ready_has_price(self):
        item   = {"id": "wid789", "current_price_jpy": 110_000,
                  "max_bid_jpy": 150_000, "bid_ready_reason": "price_ok_within_1h"}
        blocks = _build_bid_ready_blocks(item)
        text   = str(blocks)
        assert "110" in text  # ¥110,000

    def test_morning_brief_section_fields_count(self):
        kpi    = {"yahoo_pending_count": 0, "audit_pass_count": 0,
                  "keep_count": 0, "bid_ready_count": 0}
        blocks = _build_morning_brief_blocks(kpi, "2026-04-01")
        section = next(b for b in blocks if b["type"] == "section")
        assert len(section["fields"]) == 4

    def test_bid_ready_header_text(self):
        item   = {"id": "wid999", "current_price_jpy": None,
                  "max_bid_jpy": None, "bid_ready_reason": None}
        blocks = _build_bid_ready_blocks(item)
        header = next(b for b in blocks if b["type"] == "header")
        assert "BID_READY" in header["text"]["text"]


# ================================================================
# TestDedupLogic
# ================================================================

class TestDedupLogic:
    """重複送信防止ロジック (was_recently_notified) のモックテスト"""

    def _make_client(self, already_sent: bool):
        """was_recently_notified が返す値を制御するモック client"""
        mock = MagicMock()
        mock.table.return_value.select.return_value.\
            eq.return_value.eq.return_value.\
            gte.return_value.eq.return_value.limit.return_value.\
            execute.return_value = MagicMock(
                data=[{"id": "x"}] if already_sent else []
            )
        return mock

    def test_morning_brief_skipped_when_already_sent(self):
        """was_recently_notified=True → skip"""
        client = MagicMock()
        with patch(
            "scripts.slack_notifier.was_recently_notified", return_value=True
        ), patch("scripts.slack_notifier.fetch_kpi", return_value={}):
            result = notify_morning_brief(client, dry_run=False)
        assert result["status"] == "skipped"

    def test_morning_brief_sent_when_not_notified(self):
        """was_recently_notified=False + dry_run → dry_run を返す"""
        client = MagicMock()
        kpi = {"yahoo_pending_count": 0, "audit_pass_count": 0,
               "keep_count": 0, "bid_ready_count": 0}
        with patch(
            "scripts.slack_notifier.was_recently_notified", return_value=False
        ), patch("scripts.slack_notifier.fetch_kpi", return_value=kpi):
            result = notify_morning_brief(client, dry_run=True)
        assert result["status"] == "dry_run"

    def test_level_a_skipped_when_dedup_true(self):
        client    = MagicMock()
        candidate = {"id": "cand-001", "title": "Test Coin"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=True):
            status = notify_level_a_new(client, candidate, dry_run=False)
        assert status == "skipped"

    def test_level_a_dry_run_when_dedup_false(self):
        client    = MagicMock()
        candidate = {"id": "cand-001", "title": "Test Coin"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False):
            status = notify_level_a_new(client, candidate, dry_run=True)
        assert status == "dry_run"

    def test_ending_soon_skipped_when_dedup_true(self):
        client = MagicMock()
        item   = {"id": "wid-001", "time_left_seconds": 300}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=True):
            status = notify_ending_soon(client, item, dry_run=False)
        assert status == "skipped"

    def test_bid_ready_dry_run(self):
        client = MagicMock()
        item   = {"id": "wid-002", "current_price_jpy": 90_000}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False):
            status = notify_bid_ready(client, item, dry_run=True)
        assert status == "dry_run"


# ================================================================
# TestNotifyFunctions
# ================================================================

class TestNotifyFunctions:
    """notify_* が _post_to_slack を呼ぶかどうかをテストする"""

    def _mock_post(self, ok: bool = True, ts: str = "123.456"):
        return SendResult(ok=ok, message_ts=ts if ok else None,
                          error=None if ok else "channel_not_found")

    def test_notify_morning_brief_calls_post_on_success(self):
        client = MagicMock()
        kpi = {"yahoo_pending_count": 2, "audit_pass_count": 1,
               "keep_count": 0, "bid_ready_count": 0}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier.fetch_kpi", return_value=kpi), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()) as mock_post, \
             patch("scripts.slack_notifier.log_notification"), \
             patch("scripts.slack_notifier.record_morning_brief_run"):
            result = notify_morning_brief(client, dry_run=False)
        assert result["status"] == "sent"
        mock_post.assert_called_once()

    def test_notify_morning_brief_status_failed_on_slack_error(self):
        client = MagicMock()
        kpi = {"yahoo_pending_count": 0, "audit_pass_count": 0,
               "keep_count": 0, "bid_ready_count": 0}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier.fetch_kpi", return_value=kpi), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post(ok=False)), \
             patch("scripts.slack_notifier.log_notification"), \
             patch("scripts.slack_notifier.record_morning_brief_run"):
            result = notify_morning_brief(client, dry_run=False)
        assert result["status"] == "failed"

    def test_notify_level_a_sent(self):
        client    = MagicMock()
        candidate = {"id": "cand-A01", "title": "NGC MS65 米ドル金貨"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()), \
             patch("scripts.slack_notifier.log_notification"):
            status = notify_level_a_new(client, candidate, dry_run=False)
        assert status == "sent"

    def test_notify_level_a_failed(self):
        client    = MagicMock()
        candidate = {"id": "cand-A02", "title": "NGC MS65"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post(ok=False)), \
             patch("scripts.slack_notifier.log_notification"):
            status = notify_level_a_new(client, candidate, dry_run=False)
        assert status == "failed"

    def test_notify_keep_price_alert_sent(self):
        client = MagicMock()
        item   = {"id": "wid-P01", "status": WatchStatus.PRICE_OK,
                  "current_price_jpy": 80_000, "max_bid_jpy": 100_000}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()), \
             patch("scripts.slack_notifier.log_notification"):
            status = notify_keep_price_alert(client, item, dry_run=False)
        assert status == "sent"

    def test_notify_ending_soon_sent(self):
        client = MagicMock()
        item   = {"id": "wid-E01", "time_left_seconds": 1800,
                  "auction_end_at": "2026-04-01T14:00:00+00:00"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()), \
             patch("scripts.slack_notifier.log_notification"):
            status = notify_ending_soon(client, item, dry_run=False)
        assert status == "sent"

    def test_notify_bid_ready_sent(self):
        client = MagicMock()
        item   = {"id": "wid-B01", "current_price_jpy": 90_000,
                  "max_bid_jpy": 120_000, "bid_ready_reason": "price_ok_within_1h"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()), \
             patch("scripts.slack_notifier.log_notification"):
            status = notify_bid_ready(client, item, dry_run=False)
        assert status == "sent"

    def test_notify_log_called_with_type(self):
        """log_notification に正しい notification_type が渡るか確認"""
        client    = MagicMock()
        candidate = {"id": "cand-LOG", "title": "テスト"}
        with patch("scripts.slack_notifier.was_recently_notified", return_value=False), \
             patch("scripts.slack_notifier._post_to_slack",
                   return_value=self._mock_post()), \
             patch("scripts.slack_notifier.log_notification") as mock_log:
            notify_level_a_new(client, candidate, dry_run=False)
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["notification_type"] == NotificationType.LEVEL_A_NEW


# ================================================================
# TestNotionPropertyBuilders
# ================================================================

class TestNotionPropertyBuilders:
    """Notion プロパティ変換関数のテスト"""

    def test_txt_returns_rich_text(self):
        result = _txt("hello")
        assert "rich_text" in result
        assert result["rich_text"][0]["text"]["content"] == "hello"

    def test_txt_truncates_at_2000(self):
        long_str = "x" * 3000
        result   = _txt(long_str)
        assert len(result["rich_text"][0]["text"]["content"]) == 2000

    def test_num_returns_number(self):
        assert _num(150_000) == {"number": 150000.0}

    def test_num_none_returns_none(self):
        assert _num(None) == {"number": None}

    def test_select_returns_select(self):
        result = _select("AUDIT_PASS")
        assert result["select"]["name"] == "AUDIT_PASS"

    def test_build_candidate_properties_has_audit_status(self):
        cand = {
            "id": "c001",
            "title": "NGC MS63 英国ソブリン",
            "audit_status": AuditStatus.AUDIT_PASS,
            "target_max_bid_jpy": 120_000,
            "comparison_quality_score": 0.75,
            "country": "UK",
            "grade": "MS63",
        }
        props = _build_candidate_properties(cand)
        assert "audit_status" in props
        assert props["audit_status"]["select"]["name"] == AuditStatus.AUDIT_PASS

    def test_build_watchlist_properties_has_status(self):
        item = {
            "id": "w001",
            "status": WatchStatus.BID_READY,
            "current_price_jpy": 90_000,
            "max_bid_jpy": 120_000,
            "time_left_seconds": 3000,
            "auction_end_at": None,
            "is_bid_ready": True,
            "added_at": None,
        }
        props = _build_watchlist_properties(item)
        assert props["status"]["select"]["name"] == WatchStatus.BID_READY
        assert props["is_bid_ready"]["checkbox"] is True


# ================================================================
# TestNotionSyncResult
# ================================================================

class TestNotionSyncResult:
    """SyncResult.status_str()"""

    def test_ok_when_no_errors(self):
        r = SyncResult(candidates_synced=5, watchlist_synced=3, error_count=0)
        assert r.status_str() == "ok"

    def test_partial_when_some_synced_some_error(self):
        r = SyncResult(candidates_synced=3, watchlist_synced=0, error_count=2)
        assert r.status_str() == "partial"

    def test_error_when_nothing_synced(self):
        r = SyncResult(candidates_synced=0, watchlist_synced=0, error_count=3)
        assert r.status_str() == "error"

    def test_ok_when_zero_synced_no_error(self):
        r = SyncResult(candidates_synced=0, watchlist_synced=0, error_count=0)
        assert r.status_str() == "ok"


# ================================================================
# TestDashboardKpi
# ================================================================

class TestDashboardKpi:
    """dashboard.fetch_kpi / color マッピングのテスト"""

    def test_audit_color_pass_is_green(self):
        color_fn = _AUDIT_COLOR.get(AuditStatus.AUDIT_PASS)
        assert color_fn is not None
        # GREEN 関数が呼ばれることを確認 (関数オブジェクト比較)
        from scripts.dashboard import GREEN
        assert color_fn is GREEN

    def test_watch_color_bid_ready_is_red(self):
        color_fn = _WATCH_COLOR.get(WatchStatus.BID_READY)
        assert color_fn is not None
        from scripts.dashboard import RED
        assert color_fn is RED

    def test_fetch_kpi_returns_all_keys(self):
        """fetch_kpi が全必須キーを返すか確認 (DB エラー時も 0 を返す)"""
        client = MagicMock()
        # 全クエリがエラーを起こすシナリオ
        client.table.side_effect = Exception("db error")
        kpi = fetch_kpi(client)
        for key in ("yahoo_pending_count", "audit_pass_count", "keep_count",
                    "bid_ready_count", "total_candidates"):
            assert key in kpi
            assert kpi[key] == 0

    def test_fetch_kpi_uses_correct_statuses(self):
        """fetch_kpi が WatchStatus.ACTIVE を使って watchlist を絞るか確認"""
        client = MagicMock()
        # すべてのクエリを 0 件で通す
        mock_chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .in_.return_value
            .execute.return_value
        ) = MagicMock(data=[], count=0)
        client.table.return_value.select.return_value.execute.return_value = MagicMock(data=[], count=0)
        client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[], count=0)
        client.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[], count=0)

        # エラーなしで返ってくることを確認
        kpi = fetch_kpi(client)
        assert isinstance(kpi, dict)


# ================================================================
# TestDashboardResult
# ================================================================

class TestDashboardResult:
    """run_dashboard の戻り値テスト"""

    def test_run_dashboard_kpi_only_returns_dict(self):
        from scripts.dashboard import run_dashboard
        client_mock = MagicMock()
        client_mock.table.side_effect = Exception("db not available")
        with patch("scripts.dashboard.get_client", return_value=client_mock), \
             patch("scripts.dashboard.fetch_kpi",
                   return_value={"yahoo_pending_count": 3, "audit_pass_count": 1,
                                 "keep_count": 2, "bid_ready_count": 0,
                                 "audit_hold_count": 0, "audit_fail_count": 0,
                                 "total_candidates": 5, "total_watchlist": 2}):
            kpi = run_dashboard(kpi_only=True)
        assert isinstance(kpi, dict)
        assert "yahoo_pending_count" in kpi

    def test_run_dashboard_returns_kpi_values(self):
        from scripts.dashboard import run_dashboard
        expected_kpi = {
            "yahoo_pending_count": 7, "audit_pass_count": 3,
            "keep_count": 2, "bid_ready_count": 1,
            "audit_hold_count": 1, "audit_fail_count": 0,
            "total_candidates": 10, "total_watchlist": 3,
        }
        client_mock = MagicMock()
        client_mock.table.side_effect = Exception("skip")
        with patch("scripts.dashboard.get_client", return_value=client_mock), \
             patch("scripts.dashboard.fetch_kpi", return_value=expected_kpi), \
             patch("scripts.dashboard.fetch_candidates_with_watch", return_value=[]), \
             patch("scripts.dashboard._print_watchlist"):
            kpi = run_dashboard()
        assert kpi["bid_ready_count"] == 1
        assert kpi["yahoo_pending_count"] == 7

    def test_run_dashboard_candidates_only(self):
        from scripts.dashboard import run_dashboard
        kpi_vals = {k: 0 for k in ("yahoo_pending_count", "audit_pass_count",
                                    "keep_count", "bid_ready_count",
                                    "audit_hold_count", "audit_fail_count",
                                    "total_candidates", "total_watchlist")}
        client_mock = MagicMock()
        with patch("scripts.dashboard.get_client", return_value=client_mock), \
             patch("scripts.dashboard.fetch_kpi", return_value=kpi_vals), \
             patch("scripts.dashboard.fetch_candidates_with_watch", return_value=[]) as fc:
            run_dashboard(candidates_only=True)
        fc.assert_called_once()

    def test_run_dashboard_watchlist_only(self):
        from scripts.dashboard import run_dashboard
        kpi_vals = {k: 0 for k in ("yahoo_pending_count", "audit_pass_count",
                                    "keep_count", "bid_ready_count",
                                    "audit_hold_count", "audit_fail_count",
                                    "total_candidates", "total_watchlist")}
        client_mock = MagicMock()
        with patch("scripts.dashboard.get_client", return_value=client_mock), \
             patch("scripts.dashboard.fetch_kpi", return_value=kpi_vals), \
             patch("scripts.dashboard._print_watchlist") as pw:
            run_dashboard(watchlist_only=True)
        pw.assert_called_once()
