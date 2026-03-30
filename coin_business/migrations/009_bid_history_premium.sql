-- 009_bid_history_premium.sql
-- 入札実績DB に落札手数料・総コスト・利益差分カラムを追加
-- 実行場所: Supabase SQL Editor

-- ════════════════════════════════════════════════════════════
-- 1. bid_history に手数料・総コスト列を追加
-- ════════════════════════════════════════════════════════════

ALTER TABLE bid_history
  ADD COLUMN IF NOT EXISTS buyer_premium_pct  NUMERIC(5,2),    -- 落札手数料率 % (例: 20.00)
  ADD COLUMN IF NOT EXISTS buyer_premium_jpy  INTEGER,         -- 落札手数料額（円）
  ADD COLUMN IF NOT EXISTS total_cost_jpy     INTEGER,         -- 総仕入コスト（落札+手数料+送料）
  ADD COLUMN IF NOT EXISTS usd_jpy_rate       NUMERIC(8,2),    -- 適用為替レート
  ADD COLUMN IF NOT EXISTS profit_diff_jpy    INTEGER;         -- 予想利益との差分（実績 - 予想）

-- ════════════════════════════════════════════════════════════
-- 2. daily_candidates に ng_category カラムを追加（TASK3連携）
-- ════════════════════════════════════════════════════════════

ALTER TABLE daily_candidates
  ADD COLUMN IF NOT EXISTS ceo_ng_category   TEXT;   -- NG分類（価格オーバー/グレード不足 等）

-- ════════════════════════════════════════════════════════════
-- 3. 確認クエリ
-- ════════════════════════════════════════════════════════════

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'bid_history'
  AND column_name IN (
    'buyer_premium_pct', 'buyer_premium_jpy',
    'total_cost_jpy', 'usd_jpy_rate', 'profit_diff_jpy'
  )
ORDER BY column_name;

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'daily_candidates'
  AND column_name = 'ceo_ng_category';
