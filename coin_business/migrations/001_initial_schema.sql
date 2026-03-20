-- ============================================================
-- 001_initial_schema.sql
-- コイン仕入判断支援システム — 初期テーブル定義
-- 設計: 100万件以上前提、入口(eBay/海外)×出口(ヤフオク/自社)統合
-- ============================================================

-- pg_trgm: タイトル部分一致検索用
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. coin_master — コイン基本情報マスター
-- ============================================================
CREATE TABLE IF NOT EXISTS coin_master (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin_id         TEXT UNIQUE NOT NULL,       -- 人間可読ID: "UK-2025-SOV-1OZ-AG-PF70"
    country         TEXT NOT NULL,              -- 国
    year            SMALLINT,                   -- 年号 (数値: 範囲検索用)
    denomination    TEXT,                       -- 額面
    material        TEXT,                       -- 素材
    weight_g        DECIMAL(8,2),               -- 重量(g)
    diameter_mm     DECIMAL(6,2),               -- 直径(mm)
    grader          TEXT,                       -- 鑑定会社 (NGC, PCGS)
    grade           TEXT,                       -- グレード (PF70 Ultra Cameo)
    series          TEXT,                       -- シリーズ名
    tags            TEXT[] DEFAULT '{}',        -- 特徴タグ配列
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 2. sellers — 出品者情報
-- ============================================================
CREATE TABLE IF NOT EXISTS sellers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source              TEXT NOT NULL,              -- 'yahoo', 'ebay', 'heritage'
    seller_name         TEXT NOT NULL,
    seller_url          TEXT,
    reliability_score   DECIMAL(3,2),               -- 0.00 - 1.00
    total_transactions  INTEGER DEFAULT 0,
    avg_price_jpy       INTEGER,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(source, seller_name)
);

-- ============================================================
-- 3. market_transactions — 全市場取引統合テーブル（メイン）
--    Yahoo/eBay/海外オークション/自社販売を1テーブルに統合
--    100万件以上を想定した設計
-- ============================================================
CREATE TABLE IF NOT EXISTS market_transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL,              -- 'yahoo', 'ebay', 'heritage', 'stacks', 'own_sale'
    source_item_id  TEXT,                       -- ソース固有ID (ヤフオクID, eBay item#)
    coin_master_id  UUID REFERENCES coin_master(id),  -- NULL可 (未マッチング)
    title           TEXT NOT NULL,              -- 出品タイトル (raw)
    price           INTEGER NOT NULL,           -- 落札/販売価格 (元通貨の最小単位)
    currency        TEXT NOT NULL DEFAULT 'JPY',-- ISO 4217
    price_jpy       INTEGER,                   -- JPY換算額
    fx_rate         DECIMAL(10,4),             -- 使用為替レート
    fx_date         DATE,                      -- 為替レート日付
    fx_source       TEXT,                      -- 為替レート出典
    exchange_rate   DECIMAL(10,4),             -- (後方互換: fx_rateと同義)
    sold_date       DATE NOT NULL,             -- 落札日/販売日
    listed_date     DATE,                      -- 出品日 (回転率分析用、取得可能な場合のみ)
    seller_id       UUID REFERENCES sellers(id),
    seller_name     TEXT,                      -- デノーマライズ (seller未登録時用)
    url             TEXT,
    -- デノーマライズフィールド (1M行でJOINコスト回避)
    country         TEXT,                      -- 国
    year            SMALLINT,                  -- 年号
    denomination    TEXT,                      -- 額面
    grader          TEXT,                      -- 鑑定会社
    grade           TEXT,                      -- グレード
    cert_number     TEXT,                      -- 鑑定番号
    slab_text       TEXT,                      -- スラブ表記そのまま
    material        TEXT,                      -- 素材
    series          TEXT,                      -- シリーズ名
    tags            TEXT[] DEFAULT '{}',        -- 特徴タグ
    rotation_days   SMALLINT,                  -- 回転日数
    notes           TEXT,                      -- 備考
    raw_data        JSONB,                     -- 元データ全体 (再解析用)
    dedup_key       TEXT UNIQUE NOT NULL,       -- 重複判定キー
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 4. cost_rules — コストルール（手数料・送料・関税率等）
-- ============================================================
CREATE TABLE IF NOT EXISTS cost_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name       TEXT UNIQUE NOT NULL,       -- 'yahoo_fee', 'domestic_shipping', etc.
    source          TEXT,                       -- NULL=全市場共通, 'ebay', 'heritage'
    rate            DECIMAL(8,4),               -- 料率 (0.10 = 10%)
    fixed_amount    INTEGER,                   -- 固定額 (JPY)
    currency        TEXT DEFAULT 'JPY',
    description     TEXT,
    effective_from  DATE DEFAULT CURRENT_DATE,
    effective_to    DATE,                      -- NULL = 現行有効
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 5. sourcing_records — 仕入実績
-- ============================================================
CREATE TABLE IF NOT EXISTS sourcing_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin_master_id  UUID REFERENCES coin_master(id),
    source          TEXT NOT NULL,              -- 'ebay', 'heritage', 'ma-shops'
    purchase_price  DECIMAL(12,2) NOT NULL,     -- 元通貨
    currency        TEXT NOT NULL DEFAULT 'USD',
    exchange_rate   DECIMAL(10,4),
    price_jpy       INTEGER,                   -- JPY換算
    shipping_foreign DECIMAL(12,2),            -- 海外送料 (元通貨)
    shipping_jpy    INTEGER,
    buyer_premium   DECIMAL(12,2),             -- オークション手数料
    consumption_tax INTEGER,                   -- 消費税
    tariff_jpy      INTEGER,                   -- 関税
    warehouse_fee   INTEGER,                   -- 海外倉庫費
    total_cost_jpy  INTEGER NOT NULL,          -- 合計仕入原価 JPY
    purchase_date   DATE,
    url             TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 6. listing_records — 出品実績
-- ============================================================
CREATE TABLE IF NOT EXISTS listing_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin_master_id  UUID REFERENCES coin_master(id),
    platform        TEXT NOT NULL DEFAULT 'yahoo', -- 'yahoo', 'mercari', 'own_site'
    title           TEXT,
    description     TEXT,
    start_price     INTEGER,
    buynow_price    INTEGER,
    sold_price      INTEGER,
    listing_date    DATE,
    sold_date       DATE,
    platform_fee    INTEGER,                   -- プラットフォーム手数料
    shipping_cost   INTEGER,                   -- 送料
    net_revenue     INTEGER,                   -- 純売上
    status          TEXT DEFAULT 'active',      -- 'active', 'sold', 'cancelled', 'expired'
    url             TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 7. profit_analysis — 利益計算結果（期間別・市場別・シナリオ別に複数行可）
-- ============================================================
CREATE TABLE IF NOT EXISTS profit_analysis (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin_master_id  UUID REFERENCES coin_master(id),
    analysis_date   DATE NOT NULL DEFAULT CURRENT_DATE,  -- 分析日
    analysis_type   TEXT NOT NULL DEFAULT 'standard',    -- 'standard', 'yahoo_only', 'ebay_only', 'scenario_xxx'
    yahoo_avg_price INTEGER,                   -- ヤフオク平均相場
    yahoo_median_price INTEGER,                -- 中央値
    yahoo_max_price INTEGER,
    yahoo_min_price INTEGER,
    yahoo_txn_count INTEGER,                   -- 取引件数
    purchase_est    INTEGER,                   -- 仕入予想額
    overseas_shipping INTEGER,
    tariff          INTEGER,
    domestic_shipping INTEGER,
    yahoo_fee       INTEGER,                   -- ヤフオク手数料
    warehouse_fee   INTEGER,                   -- 海外倉庫費
    consumption_tax INTEGER,                   -- 消費税
    total_cost      INTEGER,                   -- 合計コスト
    expected_profit INTEGER,                   -- 想定利益
    profit_rate     DECIMAL(5,4),              -- 利益率 (0.2500 = 25%)
    gross_rank      TEXT,                      -- S/A/B/C/D/E
    priority        TEXT,                      -- 最優先/優先/通常/条件付き/見送り寄り/見送り
    judgment        TEXT,                      -- 仕入OK / NG
    ai_comment      TEXT,
    risk_factors    TEXT,                      -- リスク要因
    calculated_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE(coin_master_id, analysis_date, analysis_type)
);

-- ============================================================
-- 8. daily_candidates — CEO向け日次おすすめ候補
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date     DATE NOT NULL,
    rank_position   INTEGER NOT NULL,          -- 1-500
    tier            TEXT NOT NULL,              -- 'top_100', 'top_500'
    coin_master_id  UUID REFERENCES coin_master(id),
    market_txn_id   UUID REFERENCES market_transactions(id),
    source          TEXT,                      -- 市場
    current_price   INTEGER,                   -- 現在価格/予想落札価格
    estimated_buy_price INTEGER,               -- 入札上限価格
    estimated_sell_price INTEGER,              -- ヤフオク想定売価
    expected_profit INTEGER,
    profit_rate     DECIMAL(5,4),
    gross_rank      TEXT,
    decision_factors JSONB,                    -- 判断材料
    ai_comment      TEXT,
    risk_factors    TEXT,
    ceo_override_price INTEGER,                -- CEO手入力の判断価格
    ceo_decision    TEXT DEFAULT 'pending',     -- 'approved', 'rejected', 'pending'
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(report_date, rank_position)
);

-- ============================================================
-- 9. inventory — 在庫管理
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin_master_id  UUID REFERENCES coin_master(id),
    status          TEXT NOT NULL DEFAULT '保有中', -- '保有中', '出品中', '売却済', '発送中'
    quantity        INTEGER DEFAULT 1,
    acquisition_cost INTEGER,                  -- 取得原価
    location        TEXT,                      -- 保管場所
    acquired_date   DATE,
    sold_date       DATE,
    sourcing_id     UUID REFERENCES sourcing_records(id),
    listing_id      UUID REFERENCES listing_records(id),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 10. exchange_rates — 為替レート履歴
-- ============================================================
CREATE TABLE IF NOT EXISTS exchange_rates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE NOT NULL,
    from_currency   TEXT NOT NULL DEFAULT 'USD',
    to_currency     TEXT NOT NULL DEFAULT 'JPY',
    rate            DECIMAL(10,4) NOT NULL,
    source          TEXT,                      -- 'manual', 'api', 'xe.com'
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(date, from_currency, to_currency)
);

-- ============================================================
-- 11. inventory_snapshots — 月末棚卸スナップショット
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date   DATE NOT NULL,
    coin_master_id  UUID REFERENCES coin_master(id),
    quantity        INTEGER,
    book_value      INTEGER,
    estimated_sell  INTEGER,
    eval_memo       TEXT,
    verified_by     TEXT,
    verified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(snapshot_date, coin_master_id)
);
