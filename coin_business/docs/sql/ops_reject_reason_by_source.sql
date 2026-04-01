-- ============================================================
-- C. source別除外理由集計 / AUTO_REJECT分析
-- hard_fail_codesがDBに保存されていない場合の推定理由SQL
-- ============================================================

-- C-1. source別 AUTO_REJECT 推定理由集計
WITH classified AS (
  SELECT
    dc.id::text AS candidate_id,
    dc.source,
    dc.title,
    dc.lot_title,
    dc.grader,
    dc.cert_number,
    dc.source_currency,
    dc.shipping_from_country,
    dc.is_active,
    dc.is_sold,
    dc.lot_size,
    dc.projected_profit_jpy,
    dc.auto_tier,
    CASE
      WHEN dc.auto_tier <> 'AUTO_REJECT' THEN NULL

      WHEN COALESCE(dc.grader, '') NOT IN ('NGC', 'PCGS')
           AND COALESCE(dc.title, '') !~* '(NGC|PCGS)'
           AND COALESCE(dc.lot_title, '') !~* '(NGC|PCGS)'
        THEN 'non_ngc_pcgs'

      WHEN COALESCE(dc.cert_number, '') = ''
           AND COALESCE(dc.title, '') !~* '\m\d{6,9}\M'
           AND COALESCE(dc.lot_title, '') !~* '\m\d{6,9}\M'
        THEN 'missing_cert'

      WHEN LOWER(COALESCE(dc.source, '')) = 'ebay'
           AND UPPER(COALESCE(dc.source_currency, '')) <> 'USD'
        THEN 'ebay_currency_not_usd'

      WHEN LOWER(COALESCE(dc.source, '')) = 'ebay'
           AND UPPER(COALESCE(dc.shipping_from_country, '')) NOT IN ('US', 'UK')
        THEN 'ship_from_invalid'

      WHEN COALESCE(dc.is_sold, FALSE) = TRUE
        THEN 'already_sold'

      WHEN COALESCE(dc.lot_size, 1) <> 1
        THEN 'multi_lot'

      WHEN dc.projected_profit_jpy IS NOT NULL
           AND dc.projected_profit_jpy <= 0
        THEN 'profit_thin_or_negative'

      ELSE 'other_or_combined'
    END AS reject_reason
  FROM daily_candidates dc
)
SELECT
  source,
  reject_reason,
  COUNT(*) AS cnt
FROM classified
WHERE reject_reason IS NOT NULL
GROUP BY source, reject_reason
ORDER BY source, cnt DESC, reject_reason;


-- C-2. source別 AUTO_REJECT 比率
SELECT
  source,
  COUNT(*) AS total_candidates,
  COUNT(*) FILTER (WHERE auto_tier = 'AUTO_REJECT') AS reject_count,
  ROUND(
    COUNT(*) FILTER (WHERE auto_tier = 'AUTO_REJECT')::numeric
    / NULLIF(COUNT(*), 0) * 100,
    1
  ) AS reject_rate_pct
FROM daily_candidates
GROUP BY source
ORDER BY reject_rate_pct DESC NULLS LAST, total_candidates DESC;


-- C-3. 除外理由ごとの具体例を5件ずつ見る
WITH classified AS (
  SELECT
    dc.id::text AS candidate_id,
    dc.source,
    dc.title,
    dc.lot_title,
    dc.grader,
    dc.cert_number,
    dc.source_currency,
    dc.shipping_from_country,
    dc.is_active,
    dc.is_sold,
    dc.lot_size,
    dc.projected_profit_jpy,
    CASE
      WHEN COALESCE(dc.grader, '') NOT IN ('NGC', 'PCGS')
           AND COALESCE(dc.title, '') !~* '(NGC|PCGS)'
           AND COALESCE(dc.lot_title, '') !~* '(NGC|PCGS)'
        THEN 'non_ngc_pcgs'
      WHEN COALESCE(dc.cert_number, '') = ''
           AND COALESCE(dc.title, '') !~* '\m\d{6,9}\M'
           AND COALESCE(dc.lot_title, '') !~* '\m\d{6,9}\M'
        THEN 'missing_cert'
      WHEN LOWER(COALESCE(dc.source, '')) = 'ebay'
           AND UPPER(COALESCE(dc.source_currency, '')) <> 'USD'
        THEN 'ebay_currency_not_usd'
      WHEN LOWER(COALESCE(dc.source, '')) = 'ebay'
           AND UPPER(COALESCE(dc.shipping_from_country, '')) NOT IN ('US', 'UK')
        THEN 'ship_from_invalid'
      WHEN COALESCE(dc.is_sold, FALSE) = TRUE
        THEN 'already_sold'
      WHEN COALESCE(dc.lot_size, 1) <> 1
        THEN 'multi_lot'
      WHEN dc.projected_profit_jpy IS NOT NULL
           AND dc.projected_profit_jpy <= 0
        THEN 'profit_thin_or_negative'
      ELSE 'other_or_combined'
    END AS reject_reason
  FROM daily_candidates dc
  WHERE dc.auto_tier = 'AUTO_REJECT'
),
ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY source, reject_reason
      ORDER BY candidate_id DESC
    ) AS rn
  FROM classified
)
SELECT
  source,
  reject_reason,
  candidate_id,
  title,
  lot_title,
  grader,
  cert_number,
  source_currency,
  shipping_from_country,
  is_sold,
  lot_size,
  projected_profit_jpy
FROM ranked
WHERE rn <= 5
ORDER BY source, reject_reason, rn;
