-- ============================================================
-- D. stale候補 (last_status_checked_at > 6時間前)
-- ============================================================

-- D-1. stale候補一覧（全件）
SELECT
  dc.id::text AS candidate_id,
  dc.source,
  dc.title,
  dc.auto_tier,
  dc.evidence_count,
  dc.projected_profit_jpy,
  dc.projected_roi,
  dc.recommended_max_bid_jpy,
  dc.is_active,
  dc.is_sold,
  dc.last_status_checked_at,
  CASE
    WHEN dc.last_status_checked_at IS NULL THEN TRUE
    WHEN dc.last_status_checked_at < NOW() - INTERVAL '6 hours' THEN TRUE
    ELSE FALSE
  END AS is_stale
FROM daily_candidates dc
WHERE
  dc.last_status_checked_at IS NULL
  OR dc.last_status_checked_at < NOW() - INTERVAL '6 hours'
ORDER BY
  COALESCE(dc.is_active, FALSE) DESC,
  COALESCE(dc.projected_profit_jpy, 0) DESC,
  dc.last_status_checked_at ASC NULLS FIRST,
  dc.id DESC;


-- D-2. stale かつ active な候補（最優先 refresh 対象）
SELECT
  dc.id::text AS candidate_id,
  dc.source,
  dc.title,
  dc.auto_tier,
  dc.projected_profit_jpy,
  dc.evidence_count,
  dc.last_status_checked_at
FROM daily_candidates dc
WHERE COALESCE(dc.is_active, FALSE) = TRUE
  AND COALESCE(dc.is_sold, FALSE) = FALSE
  AND (
    dc.last_status_checked_at IS NULL
    OR dc.last_status_checked_at < NOW() - INTERVAL '6 hours'
  )
ORDER BY
  COALESCE(dc.projected_profit_jpy, 0) DESC,
  COALESCE(dc.evidence_count, 0) DESC,
  dc.last_status_checked_at ASC NULLS FIRST;


-- D-3. stale件数の source別集計
SELECT
  dc.source,
  COUNT(*) AS stale_count
FROM daily_candidates dc
WHERE
  dc.last_status_checked_at IS NULL
  OR dc.last_status_checked_at < NOW() - INTERVAL '6 hours'
GROUP BY dc.source
ORDER BY stale_count DESC, dc.source;


-- E. 朝イチ確認用まとめSQL（毎朝1回）
SELECT
  COUNT(*) AS total_candidates,
  COUNT(*) FILTER (WHERE evidence_count > 0) AS has_evidence,
  COUNT(*) FILTER (WHERE projected_profit_jpy IS NOT NULL) AS has_pricing,
  COUNT(*) FILTER (WHERE auto_tier = 'AUTO_PASS') AS auto_pass,
  COUNT(*) FILTER (WHERE auto_tier = 'AUTO_REVIEW') AS auto_review,
  COUNT(*) FILTER (WHERE auto_tier = 'AUTO_REJECT') AS auto_reject,
  COUNT(*) FILTER (
    WHERE last_status_checked_at IS NULL
       OR last_status_checked_at < NOW() - INTERVAL '6 hours'
  ) AS stale_count
FROM daily_candidates;
