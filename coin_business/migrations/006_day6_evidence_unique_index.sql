-- 006_day6_evidence_unique_index.sql
-- candidate_evidence の upsert 用ユニークインデックス

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_evidence_upsert
    ON candidate_evidence (candidate_id, evidence_type, evidence_url);

-- auto_tier カラム追加 (eligibility_rules の結果を保存)
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS auto_tier TEXT;

-- ceo_decided_at / ceo_ng_reason / ceo_comment (Day6 補完)
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS ceo_decided_at   TIMESTAMPTZ;

ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS ceo_ng_reason    TEXT;

ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS ceo_comment      TEXT;
