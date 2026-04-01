-- ============================================================
-- A. REVIEW_NG 優先キュー
-- 旧NGだが現システムがAUTO_REVIEWと判定した候補を優先順で抽出
-- Supabase SQL Editor に貼って実行
-- ============================================================

-- A-1. REVIEW_NG 優先キュー一覧（全件）
WITH latest_decision AS (
  SELECT DISTINCT ON (candidate_id)
    candidate_id::text,
    decision,
    reason_code,
    decision_note,
    decided_at,
    created_at
  FROM candidate_decisions
  ORDER BY candidate_id, decided_at DESC NULLS LAST, created_at DESC
),
base AS (
  SELECT
    dc.id::text AS candidate_id,
    dc.source,
    dc.title,
    dc.lot_title,
    dc.grader,
    dc.cert_number,
    dc.auto_tier,
    dc.eligibility_status,
    dc.evidence_count,
    dc.projected_profit_jpy,
    dc.projected_roi,
    dc.recommended_max_bid_jpy,
    dc.comparison_quality_score,
    dc.is_active,
    dc.is_sold,
    dc.last_status_checked_at,
    dc.shipping_from_country,
    dc.source_currency,
    COALESCE(dc.decision_status, dc.ceo_decision, ld.decision) AS latest_decision_raw,
    ld.reason_code AS latest_reason_code,
    ld.decided_at AS latest_decided_at
  FROM daily_candidates dc
  LEFT JOIN latest_decision ld ON dc.id::text = ld.candidate_id
),
scored AS (
  SELECT
    *,
    CASE
      WHEN LOWER(COALESCE(latest_decision_raw, '')) IN ('ng', 'rejected') THEN TRUE
      ELSE FALSE
    END AS is_legacy_ng,
    CASE
      WHEN last_status_checked_at IS NULL THEN TRUE
      WHEN last_status_checked_at < NOW() - INTERVAL '6 hours' THEN TRUE
      ELSE FALSE
    END AS is_stale,
    (
      (CASE WHEN COALESCE(is_active, FALSE) THEN 100 ELSE 0 END) +
      (CASE WHEN COALESCE(is_sold, FALSE) THEN -200 ELSE 0 END) +
      (CASE WHEN COALESCE(last_status_checked_at >= NOW() - INTERVAL '6 hours', FALSE) THEN 30 ELSE 0 END) +
      COALESCE(projected_profit_jpy, 0) / 1000.0 +
      COALESCE(evidence_count, 0) * 5 +
      COALESCE(comparison_quality_score, 0) * 100
    ) AS priority_score
  FROM base
)
SELECT
  candidate_id,
  source,
  title,
  grader,
  cert_number,
  evidence_count,
  projected_profit_jpy,
  projected_roi,
  recommended_max_bid_jpy,
  comparison_quality_score,
  is_active,
  is_sold,
  is_stale,
  shipping_from_country,
  source_currency,
  latest_decision_raw,
  latest_reason_code,
  latest_decided_at,
  ROUND(priority_score::numeric, 2) AS priority_score
FROM scored
WHERE auto_tier = 'AUTO_REVIEW'
  AND is_legacy_ng = TRUE
ORDER BY
  is_active DESC,
  is_sold ASC,
  is_stale ASC,
  priority_score DESC,
  projected_profit_jpy DESC NULLS LAST,
  evidence_count DESC,
  candidate_id DESC;


-- A-2. REVIEW_NG 件数を source別に把握
WITH latest_decision AS (
  SELECT DISTINCT ON (candidate_id)
    candidate_id::text,
    decision
  FROM candidate_decisions
  ORDER BY candidate_id, decided_at DESC NULLS LAST, created_at DESC
)
SELECT
  dc.source,
  COUNT(*) AS review_ng_count
FROM daily_candidates dc
LEFT JOIN latest_decision ld ON dc.id::text = ld.candidate_id
WHERE dc.auto_tier = 'AUTO_REVIEW'
  AND LOWER(COALESCE(dc.decision_status, dc.ceo_decision, ld.decision, '')) IN ('ng', 'rejected')
GROUP BY dc.source
ORDER BY review_ng_count DESC, dc.source;


-- A-3. 今日レビューすべき上位20件
WITH latest_decision AS (
  SELECT DISTINCT ON (candidate_id)
    candidate_id::text,
    decision
  FROM candidate_decisions
  ORDER BY candidate_id, decided_at DESC NULLS LAST, created_at DESC
)
SELECT
  dc.id::text AS candidate_id,
  dc.source,
  dc.title,
  dc.grader,
  dc.cert_number,
  dc.evidence_count,
  dc.projected_profit_jpy,
  dc.projected_roi,
  dc.recommended_max_bid_jpy,
  dc.comparison_quality_score,
  dc.is_active,
  dc.is_sold,
  dc.last_status_checked_at
FROM daily_candidates dc
LEFT JOIN latest_decision ld ON dc.id::text = ld.candidate_id
WHERE dc.auto_tier = 'AUTO_REVIEW'
  AND LOWER(COALESCE(dc.decision_status, dc.ceo_decision, ld.decision, '')) IN ('ng', 'rejected')
ORDER BY
  COALESCE(dc.is_active, FALSE) DESC,
  CASE WHEN dc.last_status_checked_at >= NOW() - INTERVAL '6 hours' THEN 0 ELSE 1 END ASC,
  COALESCE(dc.projected_profit_jpy, 0) DESC,
  COALESCE(dc.evidence_count, 0) DESC,
  COALESCE(dc.comparison_quality_score, 0) DESC
LIMIT 20;
