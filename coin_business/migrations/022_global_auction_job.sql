-- ============================================================
-- 022: 世界オークション ジョブ記録テーブル + lot 補完カラム (Phase 6 Day 7)
-- ============================================================

-- ============================================================
-- global_auction_lots に不足カラムを追加
-- CEO Day 7 要件: currency / lot_end_at / grade_text
-- ============================================================

-- 通貨 (デフォルト USD)
ALTER TABLE global_auction_lots
    ADD COLUMN IF NOT EXISTS currency     TEXT DEFAULT 'USD';

-- lot 個別の終了日時 (複数セッション制オークション対応)
ALTER TABLE global_auction_lots
    ADD COLUMN IF NOT EXISTS lot_end_at   TIMESTAMPTZ;

-- grade_text: 元の grade 表記文字列 (grade カラムは正規化済み想定)
ALTER TABLE global_auction_lots
    ADD COLUMN IF NOT EXISTS grade_text   TEXT;

-- インデックス: lot_end_at で ending soon 検索
CREATE INDEX IF NOT EXISTS idx_global_lots_end_at
    ON global_auction_lots (lot_end_at ASC)
    WHERE lot_end_at IS NOT NULL AND status = 'active';


-- ============================================================
-- global_lot_price_snapshots に不足カラムを追加
-- ============================================================

-- 価格の前回比差分
ALTER TABLE global_lot_price_snapshots
    ADD COLUMN IF NOT EXISTS bid_delta NUMERIC(10, 2);


-- ============================================================
-- イベント sync ジョブ記録テーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS job_global_auction_sync_daily (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE        NOT NULL,
    status          TEXT        NOT NULL,   -- 'ok' | 'partial' | 'error'
    events_synced   INTEGER     NOT NULL DEFAULT 0,
    events_new      INTEGER     NOT NULL DEFAULT 0,
    error_count     INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_global_sync_run_date
    ON job_global_auction_sync_daily (run_date DESC);


-- ============================================================
-- lot ingest ジョブ記録テーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS job_global_lot_ingest_daily (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date         DATE        NOT NULL,
    status           TEXT        NOT NULL,
    events_processed INTEGER     NOT NULL DEFAULT 0,
    lots_fetched     INTEGER     NOT NULL DEFAULT 0,
    lots_saved       INTEGER     NOT NULL DEFAULT 0,
    snapshots_saved  INTEGER     NOT NULL DEFAULT 0,
    error_count      INTEGER     NOT NULL DEFAULT 0,
    error_message    TEXT,
    run_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_global_ingest_run_date
    ON job_global_lot_ingest_daily (run_date DESC);
