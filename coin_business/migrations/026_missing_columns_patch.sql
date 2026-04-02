-- ============================================================
-- 026: スキーマ警告解消パッチ
-- 目的: E2E dry run で出ていた schema 警告を根本解消する
--
-- 追加内容:
--   1. daily_candidates.audit_status
--      candidate_pricer / dashboard / slack_notifier が参照
--   2. daily_candidates.candidate_level
--      cap_audit_runner._promote_to_candidates が格納
--   3. daily_candidates.match_type
--      cap_audit_runner._promote_to_candidates が格納
--   4. market_transactions.item_id
--      yahoo_sold_sync / import_yahoo_history が格納
--   5. yahoo_coin_seeds.scan_count / hit_count
--      ebay_api_ingest が更新 (019 未追加分の補完)
-- ============================================================

-- ============================================================
-- 1. daily_candidates — audit_status
-- ============================================================
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS audit_status TEXT;
-- NULL    : 未審査 (昇格直後)
-- 'AUDIT_PASS' : CAP監査通過 → pricing 対象
-- 'AUDIT_HOLD' : 条件未達 → 人間確認待ち
-- 'AUDIT_FAIL' : 除外

COMMENT ON COLUMN daily_candidates.audit_status IS
  'CAP監査結果: NULL=未審査 / AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL';

CREATE INDEX IF NOT EXISTS idx_daily_candidates_audit_status
    ON daily_candidates (audit_status)
    WHERE audit_status IS NOT NULL;


-- ============================================================
-- 2. daily_candidates — candidate_level
-- ============================================================
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS candidate_level TEXT;
-- 'A' / 'B' / 'C' — match_engine の BOT 判定レベル

COMMENT ON COLUMN daily_candidates.candidate_level IS
  'match_engine BOT判定レベル: A/B/C';


-- ============================================================
-- 3. daily_candidates — match_type
-- ============================================================
ALTER TABLE daily_candidates
    ADD COLUMN IF NOT EXISTS match_type TEXT;
-- 'cert_exact' | 'year_grade' | 'grade_advantage' 等

COMMENT ON COLUMN daily_candidates.match_type IS
  'match_engine の照合種別: cert_exact / year_grade 等';


-- ============================================================
-- 4. market_transactions — item_id
-- ============================================================
ALTER TABLE market_transactions
    ADD COLUMN IF NOT EXISTS item_id TEXT;
-- Yahoo: auctionID / eBay: itemId
-- source_item_id と同じデータだが、スクリプトが item_id で参照する

COMMENT ON COLUMN market_transactions.item_id IS
  'ソース固有ID (Yahoo auctionID / eBay itemId)。source_item_id の別名として運用。';

CREATE INDEX IF NOT EXISTS idx_market_txn_item_id
    ON market_transactions (item_id)
    WHERE item_id IS NOT NULL;


-- ============================================================
-- 5. yahoo_coin_seeds — scan_count / hit_count (019 補完)
-- ============================================================
ALTER TABLE yahoo_coin_seeds
    ADD COLUMN IF NOT EXISTS scan_count  INTEGER DEFAULT 0;
ALTER TABLE yahoo_coin_seeds
    ADD COLUMN IF NOT EXISTS hit_count   INTEGER DEFAULT 0;

COMMENT ON COLUMN yahoo_coin_seeds.scan_count IS 'スキャン実行回数';
COMMENT ON COLUMN yahoo_coin_seeds.hit_count  IS 'eBay/global lot ヒット件数の累計';
