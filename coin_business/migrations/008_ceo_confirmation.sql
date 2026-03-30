-- 008_ceo_confirmation.sql
-- CEO確認画面 + 入札実績DB に必要なスキーマ追加
-- 実行場所: Supabase SQL Editor

-- ════════════════════════════════════════════════════════════
-- 1. daily_candidates に CEO判断用カラムを追加
-- ════════════════════════════════════════════════════════════

ALTER TABLE daily_candidates
  ADD COLUMN IF NOT EXISTS ceo_ng_reason   TEXT,
  ADD COLUMN IF NOT EXISTS ceo_comment     TEXT,
  ADD COLUMN IF NOT EXISTS ceo_decided_at  TIMESTAMPTZ;

-- ════════════════════════════════════════════════════════════
-- 2. bid_history テーブル（入札実績DB）
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bid_history (
  id               UUID         DEFAULT gen_random_uuid() PRIMARY KEY,

  -- 案件情報
  lot_title        TEXT         NOT NULL,
  auction_house    TEXT,                        -- 'eBay', 'Heritage', 'Spink', 'NumisBids' 等
  lot_url          TEXT,                        -- オークションページURL
  lot_number       TEXT,                        -- eBay item# / Lot#
  management_no    TEXT,                        -- coin_slab_data の管理番号（任意）

  -- 入札情報
  bid_date         DATE,                        -- 入札日
  auction_end_at   TIMESTAMPTZ,                 -- 締切日時
  our_bid_usd      NUMERIC(10,2),               -- 入札額（USD）
  our_bid_jpy      INTEGER,                     -- 入札額（円換算）

  -- 結果
  result           TEXT         DEFAULT 'scheduled'
                   CHECK (result IN ('scheduled','win','lose','cancelled')),
  final_price_usd  NUMERIC(10,2),               -- 落札価格（USD）
  final_price_jpy  INTEGER,                     -- 落札価格（円換算）

  -- 実績・収支
  actual_cost_jpy  INTEGER,                     -- 実際の仕入れコスト（送料/手数料込）
  resell_price_jpy INTEGER,                     -- 売却額（将来記入）
  actual_profit_jpy INTEGER,                    -- 実利益

  -- エビデンス
  screenshot_path  TEXT,                        -- スクリーンショット保存パス
  notes            TEXT,                        -- 備考・コメント

  -- メタ
  recommended_by   TEXT         DEFAULT 'cap',  -- 推薦者: 'cap'/'cyber'/'auto'
  created_at       TIMESTAMPTZ  DEFAULT NOW(),
  updated_at       TIMESTAMPTZ  DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_bid_history_auction_house ON bid_history (auction_house);
CREATE INDEX IF NOT EXISTS idx_bid_history_result        ON bid_history (result);
CREATE INDEX IF NOT EXISTS idx_bid_history_bid_date      ON bid_history (bid_date DESC);
CREATE INDEX IF NOT EXISTS idx_bid_history_management_no ON bid_history (management_no);

-- ════════════════════════════════════════════════════════════
-- 3. 確認クエリ
-- ════════════════════════════════════════════════════════════

-- ALTER結果確認
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'daily_candidates'
  AND column_name IN ('ceo_ng_reason', 'ceo_comment', 'ceo_decided_at');

-- テーブル存在確認
SELECT table_name FROM information_schema.tables
WHERE table_name = 'bid_history';
