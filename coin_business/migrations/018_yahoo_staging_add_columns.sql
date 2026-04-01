-- ============================================================
-- 018: yahoo_sold_lots_staging 拡張カラム追加 (Day 3)
-- 追加背景:
--   Day 2 の yahoo_sold_sync.py で保存が必要と判明したカラムを追加。
--   既存テーブルへの追加のみ。データは破壊しない。
-- ============================================================

-- title_normalized: ノイズ除去済みタイトル (全角→半角, 送料/即決等除去)
ALTER TABLE yahoo_sold_lots_staging
    ADD COLUMN IF NOT EXISTS title_normalized TEXT;

-- grade_text: パーサー抽出グレード文字列 (例: MS63, PF69 UC)
--   既存の grade カラムとは別途管理。CEO 要求仕様の命名に合わせる。
ALTER TABLE yahoo_sold_lots_staging
    ADD COLUMN IF NOT EXISTS grade_text TEXT;

-- parse_confidence: パーサーの信頼スコア (0.00 〜 1.00)
--   cert/year/grade/denomination が取れた度合い。CEO確認画面で表示。
ALTER TABLE yahoo_sold_lots_staging
    ADD COLUMN IF NOT EXISTS parse_confidence NUMERIC(4, 3)
    CHECK (parse_confidence IS NULL OR (parse_confidence >= 0 AND parse_confidence <= 1));

-- インデックス: parse_confidence でのフィルタに備える
CREATE INDEX IF NOT EXISTS idx_yahoo_staging_confidence
    ON yahoo_sold_lots_staging (parse_confidence)
    WHERE parse_confidence IS NOT NULL;

-- インデックス: grade_text でのフィルタ
CREATE INDEX IF NOT EXISTS idx_yahoo_staging_grade_text
    ON yahoo_sold_lots_staging (grade_text)
    WHERE grade_text IS NOT NULL;
