"""
coin_business/tests/test_global_auction.py
===========================================
世界オークション event / lot / snapshot / cadence のユニットテスト。

テスト構成:
  TestTMinusFromDaysUntil    ( 9) - TMinusStage.from_days_until() 境界値
  TestTMinusCadenceInterval  ( 7) - TMinusCadence.interval_hours() / 頻度階段
  TestUpsertEvent            ( 7) - upsert_event の必須フィールド / dedup
  TestUpsertLot              ( 8) - upsert_lot の必須フィールド / event_id 紐付け
  TestInsertLotSnapshot      ( 7) - snapshot INSERT / time_left / bid_delta
  TestLotSnapshotIdempotency ( 3) - snapshot は常に INSERT (upsert なし)
  TestSyncResultStatus       ( 4) - SyncResult.status_str() / ok
  TestIngestResultStatus     ( 4) - IngestResult.status_str() / ok
  TestSyncTMinusFilter       ( 6) - T-21 以遠はスキップ / include_all で保存
  TestIngestCadence          ( 5) - cadence フィルタ (due/not-due)

合計 60 テスト
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from constants import TMinusStage, TMinusCadence
from db.global_repo import insert_lot_snapshot
from scripts.global_auction_sync import SyncResult, run_sync, _compute_t_minus
from scripts.global_lot_ingest import IngestResult


# ================================================================
# Fixtures / helpers
# ================================================================

def _make_event(
    auction_house:     str  = "heritage",
    event_name:        str  = "Heritage World Coins 2026",
    event_id_external: str  = "hc-2026-001",
    auction_date:      str  = "",
    t_minus_stage:     int  = TMinusStage.T7,
    status:            str  = "upcoming",
    last_synced_at:    str | None = None,
    ev_id:             str  = "event-uuid-1",
) -> dict:
    ev = {
        "id":                ev_id,
        "auction_house":     auction_house,
        "event_name":        event_name,
        "event_id_external": event_id_external,
        "t_minus_stage":     t_minus_stage,
        "status":            status,
        "last_synced_at":    last_synced_at,
    }
    if auction_date:
        ev["auction_date"] = auction_date
    return ev


def _make_lot(
    lot_id_external:   str   = "lot-001",
    lot_number:        str   = "1001",
    lot_title:         str   = "1921 Morgan Dollar NGC MS63",
    year:              int   = 1921,
    denomination:      str   = "1 Dollar",
    grade_text:        str   = "MS 63",
    grader:            str   = "NGC",
    cert_company:      str   = "NGC",
    cert_number:       str   = "12345678",
    estimate_low_usd:  float = 200.0,
    estimate_high_usd: float = 300.0,
    current_bid_usd:   float | None = None,
    currency:          str   = "USD",
    lot_url:           str   = "https://coins.ha.com/itm/1001",
    image_url:         str   = "https://cdn.ha.com/img/1001.jpg",
    lot_end_at:        str | None = None,
    status:            str   = "active",
) -> dict:
    lot: dict = {
        "lot_id_external":   lot_id_external,
        "lot_number":        lot_number,
        "lot_title":         lot_title,
        "year":              year,
        "denomination":      denomination,
        "grade_text":        grade_text,
        "grader":            grader,
        "cert_company":      cert_company,
        "cert_number":       cert_number,
        "estimate_low_usd":  estimate_low_usd,
        "estimate_high_usd": estimate_high_usd,
        "currency":          currency,
        "lot_url":           lot_url,
        "image_url":         image_url,
        "status":            status,
    }
    if current_bid_usd is not None:
        lot["current_bid_usd"] = current_bid_usd
    if lot_end_at:
        lot["lot_end_at"] = lot_end_at
    return lot


# ================================================================
# TestTMinusFromDaysUntil (9)
# ================================================================

class TestTMinusFromDaysUntil:
    """TMinusStage.from_days_until() の境界値テスト。"""

    def test_21_days_until_returns_T21(self):
        assert TMinusStage.from_days_until(21) == TMinusStage.T21

    def test_22_days_until_returns_none(self):
        """T-21 より遠い = 監視対象外"""
        assert TMinusStage.from_days_until(22) is None

    def test_7_days_until_returns_T7(self):
        assert TMinusStage.from_days_until(7) == TMinusStage.T7

    def test_8_days_until_returns_T21(self):
        """8 日後は T-21 ウィンドウ内"""
        assert TMinusStage.from_days_until(8) == TMinusStage.T21

    def test_3_days_until_returns_T3(self):
        assert TMinusStage.from_days_until(3) == TMinusStage.T3

    def test_4_days_until_returns_T7(self):
        assert TMinusStage.from_days_until(4) == TMinusStage.T7

    def test_1_day_until_returns_T1(self):
        assert TMinusStage.from_days_until(1) == TMinusStage.T1

    def test_0_days_until_returns_T1(self):
        """当日は T-1 と同扱い (最高頻度)"""
        assert TMinusStage.from_days_until(0) == TMinusStage.T1

    def test_negative_days_returns_T0(self):
        """終了後 (負の日数) は T0"""
        assert TMinusStage.from_days_until(-1) == TMinusStage.T0
        assert TMinusStage.from_days_until(-10) == TMinusStage.T0


# ================================================================
# TestTMinusCadenceInterval (7)
# ================================================================

class TestTMinusCadenceInterval:
    def test_T21_cadence_is_24h(self):
        assert TMinusCadence.interval_hours(TMinusStage.T21) == 24

    def test_T7_cadence_is_12h(self):
        assert TMinusCadence.interval_hours(TMinusStage.T7) == 12

    def test_T3_cadence_is_6h(self):
        assert TMinusCadence.interval_hours(TMinusStage.T3) == 6

    def test_T1_cadence_is_1h(self):
        assert TMinusCadence.interval_hours(TMinusStage.T1) == 1

    def test_cadence_increases_as_t_minus_decreases(self):
        """T-minus が小さいほど頻度が高い (間隔が短い)。"""
        assert (
            TMinusCadence.interval_hours(TMinusStage.T1)
            < TMinusCadence.interval_hours(TMinusStage.T3)
            < TMinusCadence.interval_hours(TMinusStage.T7)
            < TMinusCadence.interval_hours(TMinusStage.T21)
        )

    def test_none_stage_returns_T21_hours(self):
        """T-minus が None (監視外) は最低頻度"""
        assert TMinusCadence.interval_hours(None) == TMinusCadence.T21_HOURS

    def test_T0_returns_T21_hours(self):
        """T0 (終了済み) は最低頻度"""
        assert TMinusCadence.interval_hours(TMinusStage.T0) == TMinusCadence.T21_HOURS


# ================================================================
# TestUpsertEvent (7)
# ================================================================

class TestUpsertEvent:
    def _mock_client(self, return_id="event-uuid-1"):
        c = MagicMock()
        c.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": return_id}]
        )
        return c

    def test_returns_uuid_on_success(self):
        from db.global_repo import upsert_event
        c = self._mock_client("ev-uuid-123")
        result = upsert_event(c, _make_event())
        assert result == "ev-uuid-123"

    def test_upsert_called_with_auction_house(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        upsert_event(c, _make_event(auction_house="spink"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["auction_house"] == "spink"

    def test_upsert_called_with_event_name(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        upsert_event(c, _make_event(event_name="Spink Sale 2026"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["event_name"] == "Spink Sale 2026"

    def test_upsert_called_with_event_id_external(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        upsert_event(c, _make_event(event_id_external="SPINK-2026-05"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["event_id_external"] == "SPINK-2026-05"

    def test_upsert_uses_conflict_key(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        upsert_event(c, _make_event())
        kwargs = c.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "auction_house,event_id_external"

    def test_returns_none_when_auction_house_empty(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        result = upsert_event(c, _make_event(auction_house=""))
        assert result is None
        c.table.assert_not_called()

    def test_returns_none_when_event_id_external_empty(self):
        from db.global_repo import upsert_event
        c = self._mock_client()
        result = upsert_event(c, _make_event(event_id_external=""))
        assert result is None
        c.table.assert_not_called()


# ================================================================
# TestUpsertLot (8)
# ================================================================

class TestUpsertLot:
    def _mock_client(self, return_id="lot-uuid-1"):
        c = MagicMock()
        c.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": return_id}]
        )
        return c

    def test_returns_uuid_on_success(self):
        from db.global_repo import upsert_lot
        c = self._mock_client("lot-uuid-999")
        result = upsert_lot(c, "event-uuid-1", _make_lot())
        assert result == "lot-uuid-999"

    def test_upsert_called_with_event_id(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-uuid-abc", _make_lot())
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["event_id"] == "ev-uuid-abc"

    def test_upsert_called_with_lot_title(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-1", _make_lot(lot_title="1921 Morgan MS63"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["lot_title"] == "1921 Morgan MS63"

    def test_upsert_called_with_cert_fields(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-1", _make_lot(cert_company="NGC", cert_number="12345678"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["cert_company"] == "NGC"
        assert rec["cert_number"]  == "12345678"

    def test_upsert_called_with_estimate(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-1", _make_lot(estimate_low_usd=150.0, estimate_high_usd=250.0))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["estimate_low_usd"]  == 150.0
        assert rec["estimate_high_usd"] == 250.0

    def test_upsert_called_with_currency(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-1", _make_lot(currency="GBP"))
        rec = c.table.return_value.upsert.call_args[0][0]
        assert rec["currency"] == "GBP"

    def test_upsert_uses_conflict_key(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        upsert_lot(c, "ev-1", _make_lot())
        kwargs = c.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "event_id,lot_id_external"

    def test_returns_none_when_lot_id_external_empty(self):
        from db.global_repo import upsert_lot
        c = self._mock_client()
        result = upsert_lot(c, "ev-1", _make_lot(lot_id_external=""))
        assert result is None
        c.table.assert_not_called()


# ================================================================
# TestInsertLotSnapshot (7)
# ================================================================

class TestInsertLotSnapshot:
    def _mock_client(self):
        c = MagicMock()
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return c

    def test_returns_true_on_success(self):
        c = self._mock_client()
        result = insert_lot_snapshot(c, "lot-uuid", 250.0, 3, None)
        assert result is True

    def test_snapshot_includes_lot_id(self):
        c = self._mock_client()
        insert_lot_snapshot(c, "my-lot-uuid", 250.0, 3, None)
        snap = c.table.return_value.insert.call_args[0][0]
        assert snap["lot_id"] == "my-lot-uuid"

    def test_snapshot_includes_current_bid(self):
        c = self._mock_client()
        insert_lot_snapshot(c, "lot-1", 350.0, 5, None)
        snap = c.table.return_value.insert.call_args[0][0]
        assert snap["current_bid_usd"] == 350.0

    def test_snapshot_includes_bid_count(self):
        c = self._mock_client()
        insert_lot_snapshot(c, "lot-1", 200.0, 7, None)
        snap = c.table.return_value.insert.call_args[0][0]
        assert snap["bid_count"] == 7

    def test_time_left_hours_from_future_lot_end_at(self):
        from datetime import datetime, timezone, timedelta
        c = self._mock_client()
        future = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        insert_lot_snapshot(c, "lot-1", 100.0, 0, future)
        snap = c.table.return_value.insert.call_args[0][0]
        # 3 時間 ± 0.1 時間
        assert 2.9 <= snap["time_left_hours"] <= 3.1

    def test_time_left_zero_for_past_lot_end_at(self):
        c = self._mock_client()
        insert_lot_snapshot(c, "lot-1", 100.0, 0, "2020-01-01T00:00:00.000Z")
        snap = c.table.return_value.insert.call_args[0][0]
        assert snap["time_left_hours"] == 0.0

    def test_bid_delta_calculated_when_prev_bid_provided(self):
        c = self._mock_client()
        insert_lot_snapshot(c, "lot-1", 300.0, 0, None, prev_bid_usd=250.0)
        snap = c.table.return_value.insert.call_args[0][0]
        assert snap["bid_delta"] == pytest.approx(50.0)


# ================================================================
# TestLotSnapshotIdempotency (3)
# ================================================================

class TestLotSnapshotIdempotency:
    """snapshot は UPSERT ではなく常に INSERT。"""

    def test_uses_insert_not_upsert(self):
        c = MagicMock()
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        insert_lot_snapshot(c, "lot-1", 100.0, 0, None)
        c.table.return_value.insert.assert_called_once()
        c.table.return_value.upsert.assert_not_called()

    def test_multiple_calls_create_multiple_snapshots(self):
        """同じ lot_id で 2 回呼ぶと 2 回 insert される。"""
        c = MagicMock()
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        insert_lot_snapshot(c, "lot-1", 100.0, 0, None)
        insert_lot_snapshot(c, "lot-1", 110.0, 1, None)
        assert c.table.return_value.insert.call_count == 2

    def test_no_bid_delta_when_prev_not_provided(self):
        c = MagicMock()
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        insert_lot_snapshot(c, "lot-1", 100.0, 0, None, prev_bid_usd=None)
        snap = c.table.return_value.insert.call_args[0][0]
        assert "bid_delta" not in snap


# ================================================================
# TestSyncResultStatus (4)
# ================================================================

class TestSyncResultStatus:
    def test_ok_when_no_errors(self):
        r = SyncResult(events_synced=3)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_synced(self):
        r = SyncResult(events_synced=2, error_count=1)
        assert r.status_str() == "partial"

    def test_error_when_nothing_synced(self):
        r = SyncResult(events_synced=0, error_count=1)
        assert r.status_str() == "error"

    def test_ok_false_when_error_count_positive(self):
        r = SyncResult(error_count=1)
        assert r.ok is False


# ================================================================
# TestIngestResultStatus (4)
# ================================================================

class TestIngestResultStatus:
    def test_ok_when_no_errors(self):
        r = IngestResult(lots_saved=5)
        assert r.ok is True
        assert r.status_str() == "ok"

    def test_partial_when_errors_and_some_saved(self):
        r = IngestResult(lots_saved=3, error_count=1)
        assert r.status_str() == "partial"

    def test_error_when_nothing_saved(self):
        r = IngestResult(lots_saved=0, error_count=1)
        assert r.status_str() == "error"

    def test_ok_false_when_error_count_positive(self):
        r = IngestResult(error_count=2)
        assert r.ok is False


# ================================================================
# TestSyncTMinusFilter (6)
# ================================================================

class TestSyncTMinusFilter:
    """T-21 ウィンドウ外のイベントはスキップされる。"""

    def _run_sync_with_events(self, events, include_all=False):
        """fetcher がダミーイベントを返す状況で run_sync を実行する。"""
        mock_fetcher = MagicMock()
        mock_fetcher.auction_house = "heritage"
        mock_fetcher.fetch_events.return_value = events

        with (
            patch("scripts.global_auction_sync.ALL_FETCHERS", [mock_fetcher]),
            patch("scripts.global_auction_sync.get_client"),
            patch("scripts.global_auction_sync.upsert_event", return_value="ev-uuid"),
        ):
            return run_sync(dry_run=True, include_all=include_all)

    def _event_days_away(self, days: int) -> dict:
        auction_date = (date.today() + timedelta(days=days)).isoformat()
        return _make_event(auction_date=auction_date)

    def test_event_22_days_away_is_skipped(self):
        result = self._run_sync_with_events([self._event_days_away(22)])
        assert result.events_skipped == 1
        assert result.events_synced  == 0

    def test_event_21_days_away_is_synced(self):
        result = self._run_sync_with_events([self._event_days_away(21)])
        assert result.events_skipped == 0
        assert result.events_synced  == 1

    def test_event_1_day_away_is_synced(self):
        result = self._run_sync_with_events([self._event_days_away(1)])
        assert result.events_synced == 1

    def test_event_0_days_away_is_synced(self):
        result = self._run_sync_with_events([self._event_days_away(0)])
        assert result.events_synced == 1

    def test_include_all_syncs_distant_event(self):
        """include_all=True なら T-21 以遠も保存する。"""
        result = self._run_sync_with_events(
            [self._event_days_away(30)], include_all=True
        )
        assert result.events_skipped == 0
        assert result.events_synced  == 1

    def test_t_minus_stage_computed_correctly(self):
        """7 日後の event は T7 ステージ。"""
        ev = self._event_days_away(7)
        t_minus = _compute_t_minus(ev)
        assert t_minus == TMinusStage.T7


# ================================================================
# TestIngestCadence (5)
# ================================================================

class TestIngestCadence:
    """cadence フィルタで due/not-due を正しく判別する。"""

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _hours_ago_iso(self, hours: float) -> str:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        return dt.isoformat()

    def test_event_never_synced_is_due(self):
        """last_synced_at が None の event は常に due。"""
        from db.global_repo import load_events_due_for_ingest
        mock_client = MagicMock()
        events = [_make_event(t_minus_stage=TMinusStage.T7, last_synced_at=None)]
        mock_client.table.return_value.select.return_value.in_.return_value\
            .not_.is_.return_value\
            .order.return_value.limit.return_value.execute.return_value = MagicMock(
                data=events
            )
        result = load_events_due_for_ingest(mock_client, limit=10)
        assert len(result) == 1

    def test_event_synced_recently_is_not_due(self):
        """T7 cadence (12h) で 6h 前に同期済みなら due ではない。"""
        from db.global_repo import load_events_due_for_ingest
        mock_client = MagicMock()
        # 6h 前に同期 → T7 の 12h cadence に達していない
        events = [_make_event(
            t_minus_stage=TMinusStage.T7,
            last_synced_at=self._hours_ago_iso(6),
        )]
        mock_client.table.return_value.select.return_value.in_.return_value\
            .not_.is_.return_value\
            .order.return_value.limit.return_value.execute.return_value = MagicMock(
                data=events
            )
        result = load_events_due_for_ingest(mock_client, limit=10)
        assert len(result) == 0

    def test_event_synced_13h_ago_with_T7_is_due(self):
        """T7 cadence (12h) で 13h 前に同期済みなら due。"""
        from db.global_repo import load_events_due_for_ingest
        mock_client = MagicMock()
        events = [_make_event(
            t_minus_stage=TMinusStage.T7,
            last_synced_at=self._hours_ago_iso(13),
        )]
        mock_client.table.return_value.select.return_value.in_.return_value\
            .not_.is_.return_value\
            .order.return_value.limit.return_value.execute.return_value = MagicMock(
                data=events
            )
        result = load_events_due_for_ingest(mock_client, limit=10)
        assert len(result) == 1

    def test_T1_event_synced_2h_ago_is_due(self):
        """T1 cadence (1h) で 2h 前に同期済みなら due。"""
        from db.global_repo import load_events_due_for_ingest
        mock_client = MagicMock()
        events = [_make_event(
            t_minus_stage=TMinusStage.T1,
            last_synced_at=self._hours_ago_iso(2),
        )]
        mock_client.table.return_value.select.return_value.in_.return_value\
            .not_.is_.return_value\
            .order.return_value.limit.return_value.execute.return_value = MagicMock(
                data=events
            )
        result = load_events_due_for_ingest(mock_client, limit=10)
        assert len(result) == 1

    def test_T1_cadence_is_more_frequent_than_T21(self):
        """T1 (1h) < T21 (24h) — 間隔の大小関係。"""
        assert TMinusCadence.interval_hours(TMinusStage.T1) < \
               TMinusCadence.interval_hours(TMinusStage.T21)
