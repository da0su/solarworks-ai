-- ============================================================
-- 014: eBay listing テーブル群 (Phase 5)
-- 目的: eBay API 正式連携による listing の継続取得・時系列管理
-- ============================================================

-- eBay listing raw テーブル
-- eBay API の Finding API / Browse API から取得した listing の最新状態
CREATE TABLE IF NOT EXISTS ebay_listings_raw (
    id                      UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- eBay識別
    ebay_item_id            TEXT        NOT NULL UNIQUE,    -- eBay itemId
    listing_url             TEXT,

    -- コイン基本情報
    title                   TEXT        NOT NULL,
    description_snippet     TEXT,
    year                    INTEGER,
    country                 TEXT,
    denomination            TEXT,
    grade                   TEXT,
    grader                  TEXT,
    cert_number             TEXT,

    -- 出品情報
    seller_id               TEXT,
    seller_feedback_score   INTEGER,
    shipping_from_country   TEXT,
    shipping_cost_usd       NUMERIC(10, 2),
    ships_to                TEXT[],                         -- 配送先国リスト

    -- 価格
    current_price_usd       NUMERIC(10, 2),
    buy_it_now_price_usd    NUMERIC(10, 2),
    starting_price_usd      NUMERIC(10, 2),
    currency                TEXT        DEFAULT 'USD',

    -- オークション情報
    listing_type            TEXT,                           -- 'Auction' | 'FixedPrice' | 'AuctionWithBIN'
    bid_count               INTEGER     DEFAULT 0,
    end_time                TIMESTAMPTZ,
    time_left_seconds       INTEGER,

    -- 状態
    is_active               BOOLEAN     DEFAULT TRUE,
    is_sold                 BOOLEAN     DEFAULT FALSE,
    condition               TEXT,                           -- 'New' | 'Used' 等

    -- 画像
    image_url               TEXT,
    thumbnail_url           TEXT,

    -- 処理状態
    match_status            TEXT        DEFAULT 'pending',
    -- 'pending' | 'matched' | 'no_match' | 'audit_pass' | 'audit_fail'

    -- メタ
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_fetched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_ebay_raw_active
    ON ebay_listings_raw (is_active, end_time ASC)
    WHERE is_active = TRUE AND is_sold = FALSE;

CREATE INDEX IF NOT EXISTS idx_ebay_raw_match_status
    ON ebay_listings_raw (match_status, last_fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_ebay_raw_end_time
    ON ebay_listings_raw (end_time ASC)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_ebay_raw_cert
    ON ebay_listings_raw (cert_number)
    WHERE cert_number IS NOT NULL;

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_ebay_raw_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ebay_raw_updated_at ON ebay_listings_raw;
CREATE TRIGGER trg_ebay_raw_updated_at
    BEFORE UPDATE ON ebay_listings_raw
    FOR EACH ROW EXECUTE FUNCTION update_ebay_raw_updated_at();


-- ============================================================
-- eBay listing snapshot テーブル
-- 価格・入札数・残時間の時系列追跡
-- ============================================================

CREATE TABLE IF NOT EXISTS ebay_listing_snapshots (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    listing_id          UUID        NOT NULL REFERENCES ebay_listings_raw(id)
                                    ON DELETE CASCADE,
    ebay_item_id        TEXT        NOT NULL,

    -- スナップショット値
    price_usd           NUMERIC(10, 2),
    bid_count           INTEGER,
    time_left_seconds   INTEGER,
    is_active           BOOLEAN,
    is_sold             BOOLEAN     DEFAULT FALSE,

    -- 前回比較
    price_delta_usd     NUMERIC(10, 2),     -- 前回との価格差
    bid_delta           INTEGER,             -- 前回との入札数差

    -- メタ
    snapped_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_ebay_snapshots_listing_id
    ON ebay_listing_snapshots (listing_id, snapped_at DESC);

CREATE INDEX IF NOT EXISTS idx_ebay_snapshots_item_id
    ON ebay_listing_snapshots (ebay_item_id, snapped_at DESC);


-- ============================================================
-- eBay seed hits テーブル
-- seed と listing のマッチ記録
-- ============================================================

CREATE TABLE IF NOT EXISTS ebay_seed_hits (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    seed_id         UUID        NOT NULL REFERENCES yahoo_coin_seeds(id)
                                ON DELETE CASCADE,
    listing_id      UUID        NOT NULL REFERENCES ebay_listings_raw(id)
                                ON DELETE CASCADE,
    ebay_item_id    TEXT        NOT NULL,

    -- マッチ情報
    match_score     NUMERIC(5, 3),          -- 0.0〜1.0
    match_type      TEXT,                   -- 'cert_exact' | 'title_fuzzy' | 'year_grade'
    match_details   JSONB,                  -- 照合の詳細

    -- 候補評価
    candidate_level TEXT,                   -- 'A' | 'B' | 'C'
    is_promoted     BOOLEAN     DEFAULT FALSE,  -- daily_candidates に昇格済み

    -- メタ
    first_hit_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 重複防止
    UNIQUE (seed_id, listing_id)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_ebay_seed_hits_seed_id
    ON ebay_seed_hits (seed_id, first_hit_at DESC);

CREATE INDEX IF NOT EXISTS idx_ebay_seed_hits_level
    ON ebay_seed_hits (candidate_level, is_promoted);

CREATE INDEX IF NOT EXISTS idx_ebay_seed_hits_listing_id
    ON ebay_seed_hits (listing_id);
