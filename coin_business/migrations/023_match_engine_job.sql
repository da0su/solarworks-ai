-- ============================================================
-- 023: match_engine 追加カラム + ジョブテーブル (Day 8)
-- 目的: candidate_match_results に照合フラグ/利益列を追加し
--       match_engine / cap_audit_runner の実行記録テーブルを作成
-- ============================================================

-- ============================================================
-- candidate_match_results に照合フラグ列を追加
-- ============================================================

ALTER TABLE candidate_match_results
    ADD COLUMN IF NOT EXISTS match_reason          TEXT,
    ADD COLUMN IF NOT EXISTS cert_match_flag       BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS grade_advantage_flag  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS year_tolerance_flag   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS projected_profit_jpy  INTEGER;


-- ============================================================
-- job_match_engine_daily — match_engine 実行記録
-- ============================================================

CREATE TABLE IF NOT EXISTS job_match_engine_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date            DATE    NOT NULL,
    status              TEXT,                       -- 'ok' | 'partial' | 'error'
    listings_scanned    INTEGER DEFAULT 0,          -- 処理した eBay listing 件数
    lots_scanned        INTEGER DEFAULT 0,          -- 処理した global lot 件数
    matches_created     INTEGER DEFAULT 0,          -- 新規 match_results 件数
    level_a_count       INTEGER DEFAULT 0,          -- Level A 件数
    level_b_count       INTEGER DEFAULT 0,          -- Level B 件数
    level_c_count       INTEGER DEFAULT 0,          -- Level C 件数
    error_count         INTEGER DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_match_engine_run_date
    ON job_match_engine_daily (run_date DESC);


-- ============================================================
-- job_cap_audit_daily — cap_audit_runner 実行記録
-- ============================================================

CREATE TABLE IF NOT EXISTS job_cap_audit_daily (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date            DATE    NOT NULL,
    status              TEXT,                       -- 'ok' | 'partial' | 'error'
    audited_count       INTEGER DEFAULT 0,          -- 審査件数
    audit_pass_count    INTEGER DEFAULT 0,
    audit_hold_count    INTEGER DEFAULT 0,
    audit_fail_count    INTEGER DEFAULT 0,
    promoted_count      INTEGER DEFAULT 0,          -- daily_candidates 昇格件数
    error_count         INTEGER DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_cap_audit_run_date
    ON job_cap_audit_daily (run_date DESC);
