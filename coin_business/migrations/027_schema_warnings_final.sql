-- ============================================================
-- 027: E2E schema 警告 最終解消パッチ
-- 対象:
--   1. market_transactions.thumbnail_url  (yahoo_sold_sync が参照)
--   2. daily_rates.usd_jpy               (candidate_pricer / keep_watch が参照)
--                                         ← 実体は usd_jpy_calc と同値
-- ============================================================

-- ============================================================
-- 1. market_transactions.thumbnail_url
-- ============================================================
ALTER TABLE market_transactions
    ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;

COMMENT ON COLUMN market_transactions.thumbnail_url IS
  'Yahoo/eBay の商品サムネイル画像URL';


-- ============================================================
-- 2. daily_rates.usd_jpy
--    既存列 usd_jpy_calc を代替として usd_jpy を追加。
--    スクリプトは usd_jpy を参照するため、運用上は usd_jpy_calc と同値を設定する。
-- ============================================================
ALTER TABLE daily_rates
    ADD COLUMN IF NOT EXISTS usd_jpy NUMERIC(10, 4);

COMMENT ON COLUMN daily_rates.usd_jpy IS
  'USD/JPY レート（参照用）。usd_jpy_calc と同値で運用。';

-- 既存行に usd_jpy_calc の値をコピー (NULL でない行のみ)
UPDATE daily_rates
SET usd_jpy = usd_jpy_calc
WHERE usd_jpy IS NULL AND usd_jpy_calc IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_daily_rates_rate_date_usd
    ON daily_rates (rate_date DESC)
    WHERE usd_jpy IS NOT NULL;
