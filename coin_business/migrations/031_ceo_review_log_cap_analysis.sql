-- Migration 031: ceo_review_log へ CAP分析カラム追加
-- 実行: Supabase SQL Editor で手動適用
-- CHG-028: CEO全面差し戻し対応 - CAP審査票フィールド追加

-- ① CAP分析カラム追加
ALTER TABLE ceo_review_log
  ADD COLUMN IF NOT EXISTS comparison_type     TEXT,        -- 'EXACT'/'YEAR_DELTA'/'GRADE_DELTA'/'TYPE_ONLY'/'NONE'
  ADD COLUMN IF NOT EXISTS yahoo_ref_id        TEXT,        -- market_transactions.id (参照レコード)
  ADD COLUMN IF NOT EXISTS yahoo_ref_title     TEXT,        -- Yahoo参照タイトル（証跡）
  ADD COLUMN IF NOT EXISTS yahoo_ref_price_jpy INTEGER,     -- Yahoo参照価格（円）
  ADD COLUMN IF NOT EXISTS yahoo_ref_date      DATE,        -- Yahoo参照落札日
  ADD COLUMN IF NOT EXISTS yahoo_ref_grade     TEXT,        -- Yahoo参照グレード
  ADD COLUMN IF NOT EXISTS cap_bid_limit_jpy   INTEGER,     -- CAP計算仕入上限（円）
  ADD COLUMN IF NOT EXISTS cap_bid_limit_usd   NUMERIC(10,2), -- CAP計算仕入上限（USD換算）
  ADD COLUMN IF NOT EXISTS estimated_sell_price_jpy INTEGER, -- 想定ヤフオク売価（円）
  ADD COLUMN IF NOT EXISTS total_cost_jpy      INTEGER,     -- 想定仕入総コスト（円）
  ADD COLUMN IF NOT EXISTS expected_profit_jpy INTEGER,     -- 想定利益（円）
  ADD COLUMN IF NOT EXISTS expected_roi_pct    NUMERIC(5,2), -- 想定ROI（%）
  ADD COLUMN IF NOT EXISTS cap_judgment        TEXT,        -- 'CAP_BUY'/'CAP_HOLD'/'CAP_NG'
  ADD COLUMN IF NOT EXISTS cap_comment         TEXT,        -- CAPコメント（1-3文）
  ADD COLUMN IF NOT EXISTS image_url           TEXT,        -- コイン画像URL（WORLDは必須）
  ADD COLUMN IF NOT EXISTS evidence_status     TEXT,        -- '画像確認済'/'スラブ未確認'/'要確認'
  ADD COLUMN IF NOT EXISTS category            TEXT DEFAULT 'PENDING_ENRICHMENT';
                                                             -- 'CEO_REVIEW'/'INVESTIGATION'/'OBSERVATION'/'RETURNED'/'PENDING_ENRICHMENT'

-- ② 既存196件を全件 RETURNED に変更（CEO全面差し戻し）
UPDATE ceo_review_log
SET
  category    = 'RETURNED',
  reason_text = COALESCE(reason_text, '') || ' [2026-04-03差し戻し: URL貼り付けのみ。Yahoo比較/利益計算/CAP判断なし]',
  updated_at  = NOW()
WHERE category IS NULL
   OR category = 'PENDING_ENRICHMENT';

-- ③ 確認クエリ
SELECT
  category,
  source_group,
  COUNT(*) AS cnt
FROM ceo_review_log
GROUP BY category, source_group
ORDER BY category, source_group;
