-- ============================================================
-- 003_seed_cost_rules.sql
-- コストルール初期データ
-- ============================================================

INSERT INTO cost_rules (rule_name, source, rate, fixed_amount, description) VALUES
    ('yahoo_fee',              NULL,       0.1000, NULL,  'ヤフオク落札手数料 10%'),
    ('domestic_shipping',      NULL,       NULL,   1000,  '国内送料 1000円'),
    ('tariff_rate',            NULL,       0.0550, NULL,  '関税率 5.5%'),
    ('consumption_tax',        NULL,       0.1000, NULL,  '消費税 10%'),
    ('ebay_buyer_premium',     'ebay',     0.0000, NULL,  'eBay購入者手数料 (通常0%)'),
    ('heritage_buyer_premium', 'heritage', 0.2000, NULL,  'Heritage Auctions バイヤーズプレミアム 20%'),
    ('stacks_buyer_premium',   'stacks',   0.2000, NULL,  'Stacks バイヤーズプレミアム 20%'),
    ('overseas_shipping',      NULL,       NULL,   4000,  '海外送料デフォルト 4000円'),
    ('warehouse_fee',          NULL,       NULL,   2000,  '海外倉庫保管ピッキング費 2000円')
ON CONFLICT (rule_name) DO NOTHING;
