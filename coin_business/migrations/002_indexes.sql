-- ============================================================
-- 002_indexes.sql
-- 100万件以上対応インデックス設計
-- ============================================================

-- market_transactions: CEO検索パターン (国×年号×グレード)
CREATE INDEX IF NOT EXISTS idx_mt_country_year_grade
    ON market_transactions(country, year, grader, grade);

-- market_transactions: 価格帯×日付
CREATE INDEX IF NOT EXISTS idx_mt_price_date
    ON market_transactions(price_jpy, sold_date DESC);

-- market_transactions: 市場別×日付
CREATE INDEX IF NOT EXISTS idx_mt_source_date
    ON market_transactions(source, sold_date DESC);

-- market_transactions: タグ検索 (GIN配列)
CREATE INDEX IF NOT EXISTS idx_mt_tags
    ON market_transactions USING GIN(tags);

-- market_transactions: タイトル部分一致検索 (pg_trgm)
CREATE INDEX IF NOT EXISTS idx_mt_title_trgm
    ON market_transactions USING GIN(title gin_trgm_ops);

-- market_transactions: 出品者別
CREATE INDEX IF NOT EXISTS idx_mt_seller_date
    ON market_transactions(seller_id, sold_date DESC);

-- market_transactions: coin_master JOIN用
CREATE INDEX IF NOT EXISTS idx_mt_coin_master
    ON market_transactions(coin_master_id);

-- market_transactions: 出品日 (回転率分析)
CREATE INDEX IF NOT EXISTS idx_mt_listed_date
    ON market_transactions(listed_date)
    WHERE listed_date IS NOT NULL;

-- coin_master: 国×年号
CREATE INDEX IF NOT EXISTS idx_cm_country_year
    ON coin_master(country, year);

-- coin_master: 鑑定会社×グレード
CREATE INDEX IF NOT EXISTS idx_cm_grader_grade
    ON coin_master(grader, grade);

-- coin_master: タグ検索 (GIN配列)
CREATE INDEX IF NOT EXISTS idx_cm_tags
    ON coin_master USING GIN(tags);

-- profit_analysis: ランキング用
CREATE INDEX IF NOT EXISTS idx_pa_rank_rate
    ON profit_analysis(gross_rank, profit_rate DESC);

-- profit_analysis: 分析日×タイプ
CREATE INDEX IF NOT EXISTS idx_pa_date_type
    ON profit_analysis(analysis_date DESC, analysis_type);

-- daily_candidates: CEO日次閲覧用
CREATE INDEX IF NOT EXISTS idx_dc_date_rank
    ON daily_candidates(report_date DESC, rank_position);

-- exchange_rates: 為替レート検索
CREATE INDEX IF NOT EXISTS idx_er_date_cur
    ON exchange_rates(date, from_currency, to_currency);

-- sellers: ソース×名前検索
CREATE INDEX IF NOT EXISTS idx_sellers_source
    ON sellers(source, seller_name);
