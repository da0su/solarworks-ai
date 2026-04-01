-- ============================================================
-- 012: Yahoo!履歴 staging テーブル (Phase 2)
-- 目的: Yahoo!落札履歴を直接本DBに入れず、安全な受け皿で受ける
-- ============================================================

-- Yahoo!落札履歴 staging テーブル
-- 自動取得の受け皿。本DBの yahoo_sold_lots には直接書かない。
CREATE TABLE IF NOT EXISTS yahoo_sold_lots_staging (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 識別キー
    yahoo_lot_id        TEXT        UNIQUE,           -- Yahoo!オークションID
    source_url          TEXT,                         -- 落札ページURL

    -- コイン情報
    lot_title           TEXT        NOT NULL,
    year                INTEGER,
    country             TEXT,
    denomination        TEXT,
    grade               TEXT,
    grader              TEXT,                         -- NGC / PCGS / RAW 等
    cert_company        TEXT,
    cert_number         TEXT,

    -- 落札情報
    sold_price_jpy      INTEGER,
    sold_price_usd      NUMERIC(10, 2),
    sold_date           DATE,
    seller_id           TEXT,

    -- 画像
    image_url           TEXT,
    thumbnail_url       TEXT,

    -- ステータス
    status              TEXT        NOT NULL DEFAULT 'PENDING_CEO',
    -- PENDING_CEO    : CEO確認待ち（最初の10日間はここで止まる）
    -- APPROVED_TO_MAIN: CEO/CAP 承認済み。昇格処理待ち
    -- PROMOTED       : yahoo_sold_lots へ昇格完了
    -- REJECTED       : 却下
    -- HELD           : 保留中

    -- メタ
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_yahoo_staging_status
    ON yahoo_sold_lots_staging (status);

CREATE INDEX IF NOT EXISTS idx_yahoo_staging_sold_date
    ON yahoo_sold_lots_staging (sold_date DESC);

CREATE INDEX IF NOT EXISTS idx_yahoo_staging_cert
    ON yahoo_sold_lots_staging (cert_company, cert_number)
    WHERE cert_company IS NOT NULL AND cert_number IS NOT NULL;

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_yahoo_staging_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_yahoo_staging_updated_at ON yahoo_sold_lots_staging;
CREATE TRIGGER trg_yahoo_staging_updated_at
    BEFORE UPDATE ON yahoo_sold_lots_staging
    FOR EACH ROW EXECUTE FUNCTION update_yahoo_staging_updated_at();


-- ============================================================
-- Yahoo!落札履歴 レビュー記録テーブル
-- CEO / CAP の審査記録を残す
-- ============================================================

CREATE TABLE IF NOT EXISTS yahoo_sold_lot_reviews (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    staging_id      UUID        NOT NULL REFERENCES yahoo_sold_lots_staging(id)
                                ON DELETE CASCADE,

    -- 審査内容
    decision        TEXT        NOT NULL,
    -- 'approved' | 'rejected' | 'held'

    reason          TEXT,                   -- 却下/保留の理由
    reviewer        TEXT        NOT NULL DEFAULT 'ceo',
    -- 'ceo' | 'cap' | 'auto'

    review_note     TEXT,                   -- 自由メモ

    -- メタ
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_yahoo_reviews_staging_id
    ON yahoo_sold_lot_reviews (staging_id);

CREATE INDEX IF NOT EXISTS idx_yahoo_reviews_decision
    ON yahoo_sold_lot_reviews (decision, reviewed_at DESC);


-- ============================================================
-- ジョブ管理テーブル (Yahoo!同期)
-- ============================================================

CREATE TABLE IF NOT EXISTS job_yahoo_sold_sync_daily (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date        DATE        NOT NULL UNIQUE,       -- 実行日
    status          TEXT        NOT NULL DEFAULT 'pending',
    -- 'pending' | 'running' | 'done' | 'error'

    fetched_count   INTEGER     DEFAULT 0,
    inserted_count  INTEGER     DEFAULT 0,
    skipped_count   INTEGER     DEFAULT 0,
    error_count     INTEGER     DEFAULT 0,

    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_yahoo_sync_run_date
    ON job_yahoo_sold_sync_daily (run_date DESC);
