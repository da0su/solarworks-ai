-- 011_cert_columns.sql
-- 鑑定会社・鑑定番号 カラム追加 (daily_candidates)
-- 実行場所: Supabase SQL Editor
-- 目的: CEOが NGC/PCGS 鑑定番号で外部検証できる状態にする

-- ════════════════════════════════════════════════════════════
-- 1. daily_candidates に cert カラムを追加
-- ════════════════════════════════════════════════════════════

ALTER TABLE daily_candidates
  ADD COLUMN IF NOT EXISTS grading_company TEXT,    -- 'NGC' / 'PCGS'
  ADD COLUMN IF NOT EXISTS cert_number     TEXT;    -- 例: 4053419-001, 40935845

-- インデックス (CEO確認クエリで cert_number IS NOT NULL フィルタを使用)
CREATE INDEX IF NOT EXISTS idx_dc_cert_number
  ON daily_candidates (cert_number)
  WHERE cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dc_grading_company
  ON daily_candidates (grading_company)
  WHERE grading_company IS NOT NULL;

-- ════════════════════════════════════════════════════════════
-- 2. 確認クエリ
-- ════════════════════════════════════════════════════════════

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'daily_candidates'
  AND column_name IN ('grading_company', 'cert_number')
ORDER BY column_name;
