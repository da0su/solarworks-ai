-- daily_rates: 為替・地金価格の日次履歴テーブル
-- サイバーが毎朝7時に書き込み、キャップが読み取る
-- CEO操作不要（COOがscriptで実行）

CREATE TABLE IF NOT EXISTS daily_rates (
    id SERIAL PRIMARY KEY,
    rate_date DATE NOT NULL UNIQUE,
    usd_jpy_raw NUMERIC(10,4) NOT NULL,
    usd_jpy_calc NUMERIC(10,2) NOT NULL,
    gbp_jpy_raw NUMERIC(10,4),
    gbp_jpy_calc NUMERIC(10,2),
    eur_jpy_raw NUMERIC(10,4),
    eur_jpy_calc NUMERIC(10,2),
    gold_jpy_per_g NUMERIC(12,4),
    silver_jpy_per_g NUMERIC(12,4),
    platinum_jpy_per_g NUMERIC(12,4),
    source VARCHAR(50) DEFAULT 'manual_or_api',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by VARCHAR(20) DEFAULT 'coo'
);

CREATE INDEX IF NOT EXISTS idx_daily_rates_date
ON daily_rates(rate_date DESC);
