-- ============================================================
-- 020: ebay_listings_raw カラム追加 (Phase 5 Day 5)
--
-- 変更内容:
--   1. ebay_listings_raw に raw_payload / start_time / seller_username 追加
--   2. yahoo_coin_seeds.seed_status の CHECK 制約更新
--      (RUNNING→SCANNING, DONE→COOLDOWN, PAUSED→DISABLED)
-- ============================================================

-- ============================================================
-- 1. ebay_listings_raw カラム追加
-- ============================================================

-- eBay API の生レスポンス (JSONB)
-- 再解析・監査・デバッグ時に必ず必要
ALTER TABLE ebay_listings_raw
    ADD COLUMN IF NOT EXISTS raw_payload    JSONB;

-- 出品開始日時
ALTER TABLE ebay_listings_raw
    ADD COLUMN IF NOT EXISTS start_time     TIMESTAMPTZ;

-- 出品者ユーザー名 (seller_id と重複するが Browse API は username を返す)
ALTER TABLE ebay_listings_raw
    ADD COLUMN IF NOT EXISTS seller_username TEXT;

-- インデックス: raw_payload は GIN で検索可能に
CREATE INDEX IF NOT EXISTS idx_ebay_raw_raw_payload
    ON ebay_listings_raw USING GIN (raw_payload)
    WHERE raw_payload IS NOT NULL;

-- ============================================================
-- 2. yahoo_coin_seeds: seed_status の既存値を新しい enum に移行
--    RUNNING → SCANNING, DONE → COOLDOWN, PAUSED → DISABLED
-- ============================================================

UPDATE yahoo_coin_seeds SET seed_status = 'SCANNING'
    WHERE seed_status = 'RUNNING';

UPDATE yahoo_coin_seeds SET seed_status = 'COOLDOWN'
    WHERE seed_status = 'DONE';

UPDATE yahoo_coin_seeds SET seed_status = 'DISABLED'
    WHERE seed_status = 'PAUSED';

-- ============================================================
-- 3. yahoo_coin_seeds に next_scan_at を初期設定
--    (seed 生成時に next_scan_at が NULL の場合は今すぐスキャン可能に)
-- ============================================================

-- next_scan_at が NULL の READY seed は今すぐスキャン可能とする (NULL = 即時)
-- アプリケーション側でも NULL or <= NOW() を READY 判定として使う

-- ============================================================
-- 4. ジョブ管理: job_ebay_ingest_daily
-- ============================================================

CREATE TABLE IF NOT EXISTS job_ebay_ingest_daily (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date         DATE        NOT NULL UNIQUE,
    status           TEXT        NOT NULL DEFAULT 'pending',
    seeds_scanned    INTEGER     DEFAULT 0,
    listings_fetched INTEGER     DEFAULT 0,
    listings_saved   INTEGER     DEFAULT 0,
    snapshots_saved  INTEGER     DEFAULT 0,
    error_count      INTEGER     DEFAULT 0,
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_ebay_ingest_run_date
    ON job_ebay_ingest_daily (run_date DESC);
