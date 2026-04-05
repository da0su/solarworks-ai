-- 005: 地金価値・プレミアム計算用カラム追加
-- 目的: コイン固有プレミアムを地金価値から分離し、過去3年データの比較を可能にする

-- 重量・純度（パーサーで抽出）
ALTER TABLE market_transactions ADD COLUMN IF NOT EXISTS weight_oz REAL;
ALTER TABLE market_transactions ADD COLUMN IF NOT EXISTS purity REAL;

-- 地金価値・プレミアム（計算値）
ALTER TABLE market_transactions ADD COLUMN IF NOT EXISTS melt_value_jpy INTEGER;
ALTER TABLE market_transactions ADD COLUMN IF NOT EXISTS premium_jpy INTEGER;
ALTER TABLE market_transactions ADD COLUMN IF NOT EXISTS premium_ratio REAL;

-- インデックス（プレミアム分析用）
CREATE INDEX IF NOT EXISTS idx_mt_premium_ratio ON market_transactions(premium_ratio);
CREATE INDEX IF NOT EXISTS idx_mt_melt_value ON market_transactions(melt_value_jpy);
