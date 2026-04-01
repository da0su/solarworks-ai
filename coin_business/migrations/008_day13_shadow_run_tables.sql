-- 008_day13_shadow_run_tables.sql
-- shadow_runner が使うレポートテーブル

CREATE TABLE IF NOT EXISTS shadow_run_reports (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_candidates INTEGER    NOT NULL DEFAULT 0,
    decided_count   INTEGER     NOT NULL DEFAULT 0,
    agree_count     INTEGER     NOT NULL DEFAULT 0,
    disagree_count  INTEGER     NOT NULL DEFAULT 0,
    precision_rate  NUMERIC(6,4),
    summary_json    JSONB       NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shadow_run_items (
    id                    UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    shadow_run_report_id  UUID        NOT NULL REFERENCES shadow_run_reports(id) ON DELETE CASCADE,
    candidate_id          TEXT        NOT NULL,
    ceo_decision          TEXT,
    system_tier           TEXT,
    agreement             TEXT,           -- AGREE_PASS / AGREE_REJECT / DISAGREE_FP / DISAGREE_FN / REVIEW_AGREE / REVIEW_NG / PENDING
    hard_fail_codes       JSONB       DEFAULT '[]'::JSONB,
    warning_codes         JSONB       DEFAULT '[]'::JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shadow_run_items_report_id
    ON shadow_run_items(shadow_run_report_id);

CREATE INDEX IF NOT EXISTS idx_shadow_run_items_agreement
    ON shadow_run_items(agreement);

CREATE INDEX IF NOT EXISTS idx_shadow_run_items_candidate_id
    ON shadow_run_items(candidate_id);
