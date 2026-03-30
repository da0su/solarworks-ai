-- 010_priority_score.sql
-- daily_candidates に優先度スコア列を追加（TASK6）
-- 実行場所: Supabase SQL Editor

-- ════════════════════════════════════════════════════════════
-- 1. priority_score カラム追加
-- ════════════════════════════════════════════════════════════

ALTER TABLE daily_candidates
  ADD COLUMN IF NOT EXISTS priority_score  SMALLINT DEFAULT 0;  -- 0-100 スコア

COMMENT ON COLUMN daily_candidates.priority_score IS
  '優先度スコア (0-100): judgment×40 + 時間切迫×20 + match_score×20 + 利益率×20';

-- ════════════════════════════════════════════════════════════
-- 2. インデックス追加
-- ════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_daily_candidates_priority_score
  ON daily_candidates (priority_score DESC);

-- ════════════════════════════════════════════════════════════
-- 3. 既存レコードのスコア仮計算（バックフィル）
-- ════════════════════════════════════════════════════════════

UPDATE daily_candidates
SET priority_score = LEAST(100, GREATEST(0,
  -- judgment ベーススコア
  CASE judgment
    WHEN 'OK'     THEN 60
    WHEN 'CEO判断' THEN 50
    WHEN 'REVIEW' THEN 35
    ELSE 10
  END
  -- match_score ボーナス (0-20)
  + COALESCE(LEAST(20, (match_score * 20)::INTEGER), 0)
  -- 利益率ボーナス (0-20): margin_pct / 2 = 最大40%で20点
  + COALESCE(LEAST(20, (estimated_margin_pct / 2)::INTEGER), 0)
))
WHERE priority_score = 0 OR priority_score IS NULL;

-- ════════════════════════════════════════════════════════════
-- 4. 確認クエリ
-- ════════════════════════════════════════════════════════════

SELECT
  judgment,
  COUNT(*)            AS cnt,
  AVG(priority_score) AS avg_score,
  MAX(priority_score) AS max_score,
  MIN(priority_score) AS min_score
FROM daily_candidates
GROUP BY judgment
ORDER BY avg_score DESC;
