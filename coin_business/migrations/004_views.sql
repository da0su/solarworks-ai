-- ============================================================
-- 004_views.sql
-- 集計ビュー（分析用）
-- ============================================================

-- コイン別×市場別 価格統計
CREATE OR REPLACE VIEW v_coin_price_stats AS
SELECT
    coin_master_id,
    source,
    COUNT(*)                                                    AS txn_count,
    AVG(price_jpy)::INTEGER                                     AS avg_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_jpy)::INTEGER AS median_price,
    MAX(price_jpy)                                              AS max_price,
    MIN(price_jpy)                                              AS min_price,
    MAX(sold_date)                                              AS last_sold,
    AVG(rotation_days)::INTEGER                                 AS avg_rotation
FROM market_transactions
WHERE price_jpy > 0
  AND coin_master_id IS NOT NULL
GROUP BY coin_master_id, source;

-- クロスマーケット価格比較
CREATE OR REPLACE VIEW v_cross_market_prices AS
SELECT
    cm.id           AS coin_master_id,
    cm.coin_id,
    cm.country,
    cm.year,
    cm.grader,
    cm.grade,
    yahoo.txn_count AS yahoo_count,
    yahoo.avg_price AS yahoo_avg,
    yahoo.median_price AS yahoo_median,
    ebay.txn_count  AS ebay_count,
    ebay.avg_price  AS ebay_avg,
    ebay.median_price AS ebay_median,
    CASE
        WHEN yahoo.avg_price > 0 AND ebay.avg_price > 0
        THEN ROUND((yahoo.avg_price - ebay.avg_price)::NUMERIC / yahoo.avg_price * 100, 1)
        ELSE NULL
    END AS margin_pct
FROM coin_master cm
LEFT JOIN v_coin_price_stats yahoo
    ON yahoo.coin_master_id = cm.id AND yahoo.source = 'yahoo'
LEFT JOIN v_coin_price_stats ebay
    ON ebay.coin_master_id = cm.id AND ebay.source = 'ebay';

-- 市場別月次サマリー（トレンド分析用）
CREATE OR REPLACE VIEW v_monthly_market_summary AS
SELECT
    source,
    DATE_TRUNC('month', sold_date)::DATE AS month,
    country,
    COUNT(*)                              AS txn_count,
    AVG(price_jpy)::INTEGER               AS avg_price,
    MAX(price_jpy)                        AS max_price,
    MIN(price_jpy)                        AS min_price,
    SUM(price_jpy)                        AS total_volume
FROM market_transactions
WHERE price_jpy > 0
GROUP BY source, DATE_TRUNC('month', sold_date), country;
