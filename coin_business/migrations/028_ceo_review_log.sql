-- ================================================================
-- Migration 028: ceo_review_log テーブル作成
-- 目的: CEO日次レビューの判断を記録する台帳
-- 承認者: CEO指示 (2026-04-03)
-- ================================================================

CREATE TABLE IF NOT EXISTS ceo_review_log (
  id                       UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

  -- 候補識別
  ebay_item_id             TEXT        NOT NULL,     -- eBay itemId (例: v1|123456|0)
  url                      TEXT,                     -- eBay商品URL

  -- スキャン文脈
  scan_date                DATE        NOT NULL,     -- スキャン実施日
  review_bucket            TEXT        NOT NULL,     -- 'Top20' | 'Top50' | 'Top100' | 'BID'

  -- CEO判断
  ceo_decision             TEXT,                     -- 'BUY' | 'SKIP' | 'HOLD' | 'WATCH'
  reason_code              TEXT,                     -- 固定コード（下記参照）
  reason_text              TEXT,                     -- CEO自由コメント
  reviewed_at              TIMESTAMPTZ,              -- 判断日時
  reviewed_by              TEXT DEFAULT 'CEO',       -- 'CEO' | 'Cap' | 'auto'

  -- スナップショット（判断時点の情報）
  snapshot_score           INTEGER,                  -- 100点満点スコア
  snapshot_ref_price       INTEGER,                  -- ヤフオク参照価格(円)
  snapshot_profit_estimate INTEGER,                  -- 利益見込み(円)
  snapshot_title           TEXT,                     -- eBayタイトル（記録用）
  snapshot_cert_grade      TEXT,                     -- 'NGC MS65' など

  -- メタデータ
  created_at               TIMESTAMPTZ DEFAULT NOW(),
  updated_at               TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_ceo_review_log_scan_date
  ON ceo_review_log (scan_date DESC);

CREATE INDEX IF NOT EXISTS idx_ceo_review_log_decision
  ON ceo_review_log (ceo_decision);

CREATE INDEX IF NOT EXISTS idx_ceo_review_log_ebay_item
  ON ceo_review_log (ebay_item_id);

-- reason_code の固定コード一覧（コメント参照）
/*
PRICE_TOO_HIGH      現在価格が買い上限を超えている
LOW_MARGIN          利益率が基準(15%)未満
DB_NO_MATCH         DBに類似コインがなく相場不明
TYPE_MISMATCH       コインの種別が当社スコープ外
SIZE_MISMATCH       サイズ/重量が想定と合わない
CERT_DOUBT          鑑定会社・グレードに疑義あり
TOO_CHEAP_FOR_SCOPE 価格帯が低すぎて転売旨みなし
BID_TOO_HOT         入札競争が激しすぎて落札困難
NOT_INTERESTING     市場性はあるが当社優先度低
NEED_MORE_EVIDENCE  追加情報が必要で判断保留
*/

COMMENT ON TABLE ceo_review_log IS 'CEO日次レビュー判断台帳 v1.0 (Migration 028, 2026-04-03)';
