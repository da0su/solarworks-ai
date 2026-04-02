-- ============================================================
-- 021: eBay seed scanner ジョブ記録テーブル (Phase 6 Day 6)
-- 目的: ebay_seed_scanner の実行履歴を管理する
-- ============================================================

-- seed scanner 実行記録テーブル
CREATE TABLE IF NOT EXISTS job_ebay_scanner_daily (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE        NOT NULL,
    status          TEXT        NOT NULL,   -- 'ok' | 'partial' | 'error'
    seeds_scanned   INTEGER     NOT NULL DEFAULT 0,
    hits_found      INTEGER     NOT NULL DEFAULT 0,  -- API から取得した件数
    hits_saved      INTEGER     NOT NULL DEFAULT 0,  -- ebay_seed_hits に保存した件数
    error_count     INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_job_scanner_run_date
    ON job_ebay_scanner_daily (run_date DESC);

-- ============================================================
-- ebay_seed_hits に matched_query / hit_rank / hit_reason カラムを追加
-- (migration 014 では match_details JSONB のみ定義されていたが、
--  Day 6 の要件として個別カラムとしても保持する)
-- ============================================================

-- matched_query: 実際に使われた検索クエリ
ALTER TABLE ebay_seed_hits
    ADD COLUMN IF NOT EXISTS matched_query TEXT;

-- hit_rank: 検索結果内での順位 (1-based)
ALTER TABLE ebay_seed_hits
    ADD COLUMN IF NOT EXISTS hit_rank INTEGER;

-- hit_reason: ヒット理由 ('cert_number_match' | 'cert_title_match' |
--                          'title_normalized' | 'year_denom_grade')
ALTER TABLE ebay_seed_hits
    ADD COLUMN IF NOT EXISTS hit_reason TEXT;

-- インデックス補強
CREATE INDEX IF NOT EXISTS idx_ebay_seed_hits_matched_query
    ON ebay_seed_hits (seed_id, matched_query)
    WHERE matched_query IS NOT NULL;
