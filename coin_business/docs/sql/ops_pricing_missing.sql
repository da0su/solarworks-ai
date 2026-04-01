-- ============================================================
-- B. pricing missing 150件 (Heritage 25 + Spink 125)
-- 価格データ未入力候補の一覧化・優先順付け
-- ============================================================

-- B-1. pricing missing 一覧（全件）
SELECT
  dc.id::text AS candidate_id,
  dc.source,
  dc.title,
  dc.lot_title,
  dc.grader,
  dc.cert_number,
  dc.evidence_count,
  dc.is_active,
  dc.is_sold,
  dc.last_status_checked_at,
  dc.auto_tier,
  dc.source_currency,
  dc.shipping_from_country,
  dc.projected_profit_jpy,
  dc.projected_roi,
  dc.recommended_max_bid_jpy
FROM daily_candidates dc
WHERE dc.projected_profit_jpy IS NULL
  AND dc.recommended_max_bid_jpy IS NULL
ORDER BY
  dc.source,
  COALESCE(dc.is_active, FALSE) DESC,
  COALESCE(dc.evidence_count, 0) DESC,
  dc.id DESC;


-- B-2. pricing missing の source別集計
SELECT
  dc.source,
  COUNT(*) AS missing_pricing_count
FROM daily_candidates dc
WHERE dc.projected_profit_jpy IS NULL
  AND dc.recommended_max_bid_jpy IS NULL
GROUP BY dc.source
ORDER BY missing_pricing_count DESC, dc.source;


-- B-3. pricing missing だが evidence は十分ある候補（手動価格入力優先候補）
SELECT
  dc.id::text AS candidate_id,
  dc.source,
  dc.title,
  dc.lot_title,
  dc.grader,
  dc.cert_number,
  dc.evidence_count,
  dc.is_active,
  dc.is_sold,
  dc.last_status_checked_at
FROM daily_candidates dc
WHERE dc.projected_profit_jpy IS NULL
  AND dc.recommended_max_bid_jpy IS NULL
  AND COALESCE(dc.evidence_count, 0) >= 3
ORDER BY
  COALESCE(dc.is_active, FALSE) DESC,
  COALESCE(dc.evidence_count, 0) DESC,
  dc.id DESC;


-- B-4. pricing missing を source × active 状態で分ける
SELECT
  dc.source,
  COALESCE(dc.is_active, FALSE) AS is_active,
  COUNT(*) AS cnt
FROM daily_candidates dc
WHERE dc.projected_profit_jpy IS NULL
  AND dc.recommended_max_bid_jpy IS NULL
GROUP BY dc.source, COALESCE(dc.is_active, FALSE)
ORDER BY dc.source, is_active DESC;
