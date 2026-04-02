-- ============================================================
-- 024: pricing engine / keep-watch job tables (Day 9)
-- ============================================================

-- daily_candidates に pricing 列を追加
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS target_max_bid_jpy       INTEGER,
    ADD COLUMN IF NOT EXISTS comparison_quality_score NUMERIC(5, 3);

-- ============================================================
-- job_pricing_engine_daily — pricing engine 実行履歴
-- ============================================================
CREATE TABLE IF NOT EXISTS job_pricing_engine_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date            DATE    NOT NULL,
    status              TEXT,
    candidates_found    INTEGER DEFAULT 0,
    candidates_priced   INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_pricing_run_date
    ON job_pricing_engine_daily (run_date DESC);

-- ============================================================
-- job_keep_watch_daily — keep-watch refresher 実行履歴
-- ============================================================
CREATE TABLE IF NOT EXISTS job_keep_watch_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status              TEXT,
    items_checked       INTEGER DEFAULT 0,
    items_updated       INTEGER DEFAULT 0,
    bid_ready_count     INTEGER DEFAULT 0,
    ended_count         INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_keep_watch_run_at
    ON job_keep_watch_daily (run_at DESC);
