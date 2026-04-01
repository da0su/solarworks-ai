-- ============================================================
-- 013: Yahoo!落札履歴 seed テーブル (Phase 4)
-- 目的: 承認済み yahoo_sold_lots から探索クエリ(seed)を生成する
-- ※ staging データは絶対に使わない。yahoo_sold_lots のみ入力。
-- ============================================================

CREATE TABLE IF NOT EXISTS yahoo_coin_seeds (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 元データ参照
    yahoo_lot_id    TEXT        NOT NULL,   -- yahoo_sold_lots の lot_id
    source_row_id   UUID,                  -- yahoo_sold_lots の id (外部キー不要)

    -- seed の種別
    seed_type       TEXT        NOT NULL,
    -- 'cert_exact'   : cert_company + cert_number 完全一致
    -- 'title_fuzzy'  : タイトル類似マッチ
    -- 'year_grade'   : 年号±5年 + グレード以上
    -- 'country_denom': 国+額面の組み合わせ

    -- seed のクエリパラメータ
    search_query    TEXT,                  -- eBay API 検索クエリ文字列
    cert_company    TEXT,
    cert_number     TEXT,
    year_min        INTEGER,
    year_max        INTEGER,
    country         TEXT,
    denomination    TEXT,
    grade_min       TEXT,                  -- このグレード以上を対象
    grader          TEXT,                  -- NGC / PCGS / BOTH

    -- 元の Yahoo!落札参考価格
    ref_price_jpy   INTEGER,               -- 元落札価格（円）
    ref_sold_date   DATE,                  -- 元落札日

    -- seed ステータス
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    last_scanned_at TIMESTAMPTZ,           -- 最終スキャン日時
    scan_count      INTEGER     DEFAULT 0,
    hit_count       INTEGER     DEFAULT 0, -- eBay ヒット数累計

    -- メタ
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_active
    ON yahoo_coin_seeds (is_active, last_scanned_at ASC NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_cert
    ON yahoo_coin_seeds (cert_company, cert_number)
    WHERE cert_company IS NOT NULL AND cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_type
    ON yahoo_coin_seeds (seed_type);

CREATE INDEX IF NOT EXISTS idx_yahoo_seeds_lot_id
    ON yahoo_coin_seeds (yahoo_lot_id);

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_yahoo_seeds_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_yahoo_seeds_updated_at ON yahoo_coin_seeds;
CREATE TRIGGER trg_yahoo_seeds_updated_at
    BEFORE UPDATE ON yahoo_coin_seeds
    FOR EACH ROW EXECUTE FUNCTION update_yahoo_seeds_updated_at();
