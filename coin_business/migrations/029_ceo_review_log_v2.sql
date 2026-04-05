-- ================================================================
-- Migration 029: ceo_review_log v2 (migration 028 の完全置換版)
-- 目的: CEO日次レビュー + 重複禁止管理の統合台帳
-- 承認者: CEO指示 (2026-04-03)
-- 注意: migration 028 を先に適用した場合は 028 のテーブルを DROP してから実行
-- ================================================================

-- 既存テーブルがあれば削除（migration 028 が未適用なら不要だがべき等に動作）
DROP TABLE IF EXISTS ceo_review_log;

CREATE TABLE IF NOT EXISTS ceo_review_log (
  -- ─── Primary Key ───
  id                       UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

  -- ─── 候補識別 ───
  marketplace              TEXT        NOT NULL DEFAULT 'eBay',  -- 'eBay' | 'Heritage' | 'Spink'
  item_id                  TEXT        NOT NULL,                 -- eBay itemId 等
  url                      TEXT,

  -- ─── コイン属性スナップショット（提出時点） ───
  title_snapshot           TEXT,
  cert_company             TEXT,                     -- 'NGC' | 'PCGS'
  cert_number              TEXT,                     -- 鑑定番号（あれば）
  grade                    TEXT,                     -- 'MS64' 等
  country                  TEXT,                     -- 'US' | 'GB' etc.
  year                     INTEGER,
  denomination             TEXT,
  material                 TEXT,                     -- 'gold' | 'silver' | 'platinum'
  bid_count_snapshot       INTEGER DEFAULT 0,
  price_snapshot_usd       NUMERIC(10,2),            -- 提出時価格 (USD)
  price_snapshot_jpy       INTEGER,                  -- 提出時価格 (JPY換算)

  -- ─── スコアリングスナップショット ───
  yahoo_ref_price          INTEGER,                  -- ヤフオク参照価格 (JPY)
  profit_estimate          INTEGER,                  -- 利益見込み (JPY)
  db_similarity            TEXT,                     -- 'あり' | '近傍のみ' | 'なし'
  db_ref_id                TEXT,                     -- DB参照 staging_id
  snapshot_score           INTEGER,                  -- 100点満点スコア

  -- ─── スキャン文脈 ───
  scan_date                DATE        NOT NULL,
  review_bucket            TEXT        NOT NULL,     -- 'Top20' | 'Top50' | 'Top100' | 'BID' | 'WATCH'

  -- ─── 提出・重複管理 ───
  first_seen_at            TIMESTAMPTZ DEFAULT NOW(),  -- 初回検出日時
  submitted_to_ceo_at      TIMESTAMPTZ,               -- CEO提出日時（NULLなら未提出/ブロック）
  submit_count             INTEGER     DEFAULT 0,     -- 通算提出回数
  duplicate_status         TEXT        DEFAULT 'NEW', -- 'NEW' | 'DUPLICATE_BLOCKED' | 'RESUBMITTED'
  resubmit_reason          TEXT,                      -- 再提出理由（例: price_drop_10pct）

  -- ─── CEO判断 ───
  ceo_decision             TEXT,                     -- 'OK' | 'NG' | 'HOLD'
  reason_code              TEXT,                     -- 理由コード（下記参照）
  reason_text              TEXT,                     -- CEO自由コメント
  reviewed_at              TIMESTAMPTZ,
  reviewed_by              TEXT DEFAULT 'CEO',

  -- ─── メタデータ ───
  created_at               TIMESTAMPTZ DEFAULT NOW(),
  updated_at               TIMESTAMPTZ DEFAULT NOW()
);

-- ─── UNIQUE制約（ON CONFLICT upsert用） ───
ALTER TABLE ceo_review_log
  ADD CONSTRAINT uq_crl_marketplace_item_date
  UNIQUE (marketplace, item_id, scan_date);

-- ─── インデックス ───
CREATE INDEX IF NOT EXISTS idx_crl_scan_date
  ON ceo_review_log (scan_date DESC);

CREATE INDEX IF NOT EXISTS idx_crl_marketplace_item
  ON ceo_review_log (marketplace, item_id);

CREATE INDEX IF NOT EXISTS idx_crl_decision
  ON ceo_review_log (ceo_decision);

CREATE INDEX IF NOT EXISTS idx_crl_submitted_at
  ON ceo_review_log (submitted_to_ceo_at DESC);

CREATE INDEX IF NOT EXISTS idx_crl_duplicate_status
  ON ceo_review_log (duplicate_status);

-- ─── reason_code 固定コード一覧 ───
/*
PRICE_TOO_HIGH          現在価格が買い上限を超えている
PROFIT_TOO_LOW          利益率が基準 (15%) 未満
DB_NO_MATCH             DBに類似コインがなく相場不明
TYPE_MISMATCH           コインの種別が当社スコープ外
SIZE_MISMATCH           サイズ/重量が想定と合わない
CERT_NEEDS_CHECK        鑑定会社・グレードに疑義あり
TOO_HOT_BIDDING         入札競争が激しすぎて落札困難
NOT_INTERESTING         市場性はあるが当社優先度低
WATCH_ONLY              WATCH対象 (価格未確定・まだ判断できない)
NEED_MORE_EVIDENCE      追加情報が必要で判断保留
*/

-- ─── 重複判定キー一覧 (参照用) ───
/*
優先順:
1. marketplace + item_id          (最優先: eBay なら itemId がユニーク)
2. 正規化 URL                    (URLパラメータ違いの同一ページ対応)
3. cert_company + cert_number    (世界で1つの鑑定番号による同一確認)
4. country+denomination+year+grade+material のソフト一致 (補助)
*/

-- ─── 再提出許可条件一覧 (resubmit_reason 値) ───
/*
price_drop_10pct        価格が前回比10%以上下落
bids_plus_5             入札数が前回比+5以上増加
ending_24h              終了まで24時間以内
cert_verified           cert確認が新たに取れた
db_match_found          DB類似が新たに発見された
ceo_hold_recheck        CEOがHOLDとして再確認を指示
cap_manual_resubmit     CAPが理由付きで再提出価値ありと判断
*/

-- ─── CEO判断別の再提出ルール (参照用) ───
/*
ceo_decision = OK:   同一案件の再提出禁止。入札進行管理へ移す。
ceo_decision = NG:   原則30日再提出禁止。価格急落・重大更新のみ例外。
ceo_decision = HOLD: 再提出可。ただし「何が変わったか」を必ず記録。
*/

COMMENT ON TABLE ceo_review_log IS 'CEO日次レビュー台帳 v2.0 (Migration 029, 2026-04-03) - Yahoo歴史DB外の別管理領域';
