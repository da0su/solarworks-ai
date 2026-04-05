-- DB再構築: インデックス設計
-- CEO指示: DB再構築の初期タスクとしてインデックスを設計
-- CTO指示: 再設計段階で最低限入れておくべき

-- 単一カラムインデックス
CREATE INDEX IF NOT EXISTS idx_mt_source ON market_transactions(source);
CREATE INDEX IF NOT EXISTS idx_mt_sold_date ON market_transactions(sold_date DESC);
CREATE INDEX IF NOT EXISTS idx_mt_grader ON market_transactions(grader);
CREATE INDEX IF NOT EXISTS idx_mt_year ON market_transactions(year);
CREATE INDEX IF NOT EXISTS idx_mt_country ON market_transactions(country);

-- 新規カラム用（Phase 1で追加予定）
-- CREATE INDEX IF NOT EXISTS idx_mt_coin_id ON market_transactions(coin_id);
-- CREATE INDEX IF NOT EXISTS idx_mt_slab_text ON market_transactions(slab_text);
-- CREATE INDEX IF NOT EXISTS idx_mt_premium_price ON market_transactions(premium_standard_price);
-- CREATE INDEX IF NOT EXISTS idx_mt_url_valid ON market_transactions(url_valid);

-- 複合インデックス（よく使う検索パターン）
CREATE INDEX IF NOT EXISTS idx_mt_source_grader_year ON market_transactions(source, grader, year);
-- CREATE INDEX IF NOT EXISTS idx_mt_coin_id_sold_date ON market_transactions(coin_id, sold_date DESC);

-- BRIN インデックス（日付の範囲検索用・大量データに有効）
-- CREATE INDEX IF NOT EXISTS idx_mt_sold_date_brin ON market_transactions USING brin(sold_date);
