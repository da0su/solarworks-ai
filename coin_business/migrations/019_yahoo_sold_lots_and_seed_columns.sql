-- ============================================================
-- 019: yahoo_sold_lots (本DB) + yahoo_coin_seeds カラム追加
--      (Phase 4 Day 4)
--
-- 変更内容:
--   1. yahoo_sold_lots テーブル新規作成
--      - staging から昇格した CEO承認済みレコードを格納
--      - staging は絶対に直接書かない
--   2. yahoo_coin_seeds に seed_status / priority_score / next_scan_at を追加
-- ============================================================

-- ============================================================
-- 1. yahoo_sold_lots (本DB)
-- ============================================================

CREATE TABLE IF NOT EXISTS yahoo_sold_lots (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 識別キー (dedup)
    yahoo_lot_id        TEXT        NOT NULL UNIQUE,

    -- 昇格元 staging の参照
    source_staging_id   UUID,               -- yahoo_sold_lots_staging.id

    -- コイン情報
    lot_title           TEXT        NOT NULL,
    title_normalized    TEXT,
    year                INTEGER,
    denomination        TEXT,
    cert_company        TEXT,
    cert_number         TEXT,
    grade_text          TEXT,

    -- 落札情報
    sold_price_jpy      INTEGER,
    sold_date           DATE,

    -- ソース
    source_url          TEXT,
    image_url           TEXT,

    -- パース品質
    parse_confidence    NUMERIC(4, 3)
        CHECK (parse_confidence >= 0.0 AND parse_confidence <= 1.0),

    -- 承認情報
    approved_by         TEXT,               -- 'ceo' | 'cap' | 'auto'
    approved_at         TIMESTAMPTZ,        -- 承認日時

    -- メタ
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_yahoo_sold_lots_cert
    ON yahoo_sold_lots (cert_company, cert_number)
    WHERE cert_company IS NOT NULL AND cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_yahoo_sold_lots_sold_date
    ON yahoo_sold_lots (sold_date DESC);

CREATE INDEX IF NOT EXISTS idx_yahoo_sold_lots_year_denom
    ON yahoo_sold_lots (year, denomination)
    WHERE year IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_yahoo_sold_lots_confidence
    ON yahoo_sold_lots (parse_confidence DESC)
    WHERE parse_confidence IS NOT NULL;

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_yahoo_sold_lots_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_yahoo_sold_lots_updated_at ON yahoo_sold_lots;
CREATE TRIGGER trg_yahoo_sold_lots_updated_at
    BEFORE UPDATE ON yahoo_sold_lots
    FOR EACH ROW EXECUTE FUNCTION update_yahoo_sold_lots_updated_at();


-- ============================================================
-- 2. yahoo_coin_seeds カラム追加
-- ============================================================

-- seed のライフサイクル状態
ALTER TABLE yahoo_coin_seeds
    ADD COLUMN IF NOT EXISTS seed_status    TEXT    NOT NULL DEFAULT 'READY';
--  'READY'   : 次回スキャン待ち (初期値)
--  'RUNNING' : スキャン中
--  'DONE'    : このサイクル完了
--  'PAUSED'  : 一時停止

-- スキャン優先度 (高いほど先にスキャン)
ALTER TABLE yahoo_coin_seeds
    ADD COLUMN IF NOT EXISTS priority_score NUMERIC(5, 3);

-- 次回スキャン予定日時
ALTER TABLE yahoo_coin_seeds
    ADD COLUMN IF NOT EXISTS next_scan_at   TIMESTAMPTZ;

-- インデックス追加
CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_status_next
    ON yahoo_coin_seeds (seed_status, next_scan_at ASC NULLS FIRST)
    WHERE seed_status = 'READY';

CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_priority
    ON yahoo_coin_seeds (priority_score DESC NULLS LAST)
    WHERE seed_status = 'READY';


-- ============================================================
-- 3. ジョブ管理テーブル (promoter)
-- ============================================================

CREATE TABLE IF NOT EXISTS job_yahoo_promoter_daily (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date        DATE        NOT NULL UNIQUE,
    status          TEXT        NOT NULL DEFAULT 'pending',
    promoted_count  INTEGER     DEFAULT 0,
    skipped_count   INTEGER     DEFAULT 0,
    error_count     INTEGER     DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_promoter_run_date
    ON job_yahoo_promoter_daily (run_date DESC);


-- ============================================================
-- 4. ジョブ管理テーブル (seed generator)
-- ============================================================

CREATE TABLE IF NOT EXISTS job_seed_generator_daily (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date        DATE        NOT NULL UNIQUE,
    status          TEXT        NOT NULL DEFAULT 'pending',
    generated_count INTEGER     DEFAULT 0,
    skipped_count   INTEGER     DEFAULT 0,
    error_count     INTEGER     DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_seed_gen_run_date
    ON job_seed_generator_daily (run_date DESC);
