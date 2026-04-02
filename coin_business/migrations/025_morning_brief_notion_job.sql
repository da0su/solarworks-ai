-- ============================================================
-- 025: morning brief + notion sync job tables (Day 10)
-- ============================================================

-- ============================================================
-- job_morning_brief_daily — 朝ブリーフ Slack 通知履歴
-- ============================================================
CREATE TABLE IF NOT EXISTS job_morning_brief_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date            DATE    NOT NULL,
    status              TEXT,
    yahoo_pending_count INTEGER DEFAULT 0,
    audit_pass_count    INTEGER DEFAULT 0,
    keep_count          INTEGER DEFAULT 0,
    bid_ready_count     INTEGER DEFAULT 0,
    slack_message_ts    TEXT,                  -- Slack API が返す message timestamp
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_morning_brief_run_date
    ON job_morning_brief_daily (run_date DESC);


-- ============================================================
-- job_notion_sync_daily — Notion 台帳同期履歴
-- ============================================================
CREATE TABLE IF NOT EXISTS job_notion_sync_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date            DATE    NOT NULL,
    status              TEXT,
    candidates_synced   INTEGER DEFAULT 0,
    watchlist_synced    INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_notion_sync_run_date
    ON job_notion_sync_daily (run_date DESC);
