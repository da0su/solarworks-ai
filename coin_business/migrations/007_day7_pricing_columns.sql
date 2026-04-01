-- 007_day7_pricing_columns.sql
-- pricing_engine が書き込む列を daily_candidates に追加

ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS expected_sale_price_jpy    NUMERIC(18,2);

ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS recency_bucket_summary     JSONB DEFAULT '{}'::JSONB;

-- candidate_pricing_snapshots の追加列 (Day7)
ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS recent_3m_median_jpy       NUMERIC(18,2);

ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS recent_3_6m_median_jpy     NUMERIC(18,2);

ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS recent_6_12m_median_jpy    NUMERIC(18,2);

ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS older_12m_plus_median_jpy  NUMERIC(18,2);

ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS total_cost_jpy             NUMERIC(18,2);

ALTER TABLE candidate_pricing_snapshots
    ADD COLUMN IF NOT EXISTS pricing_notes_json         JSONB DEFAULT '[]'::JSONB;
