-- ================================================================
-- Migration 030: ceo_review_log に source_group / auction_house 追加
-- 目的: eBay vs 世界オークション会場別管理
-- 承認者: CEO指示 (2026-04-03)
-- ================================================================

-- source_group: 'EBAY' | 'WORLD'
ALTER TABLE ceo_review_log
  ADD COLUMN IF NOT EXISTS source_group  TEXT DEFAULT 'EBAY';

-- auction_house: 'EBAY' | 'HERITAGE' | 'STACKS_BOWERS' | 'GREATCOLLECTIONS'
--                        | 'SIXBID' | 'CATAWIKI' | 'MA_SHOPS' | 'OTHER'
ALTER TABLE ceo_review_log
  ADD COLUMN IF NOT EXISTS auction_house TEXT DEFAULT 'EBAY';

-- 既存 eBay 行を更新
UPDATE ceo_review_log
  SET source_group = 'EBAY', auction_house = 'EBAY'
  WHERE marketplace = 'eBay'
    AND (source_group IS NULL OR source_group = '');

-- インデックス
CREATE INDEX IF NOT EXISTS idx_crl_source_group
  ON ceo_review_log (source_group);

CREATE INDEX IF NOT EXISTS idx_crl_auction_house
  ON ceo_review_log (auction_house);

COMMENT ON COLUMN ceo_review_log.source_group  IS 'EBAY | WORLD';
COMMENT ON COLUMN ceo_review_log.auction_house IS 'EBAY | HERITAGE | STACKS_BOWERS | GREATCOLLECTIONS | SIXBID | CATAWIKI | MA_SHOPS | OTHER';
