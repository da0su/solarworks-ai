-- ============================================================
-- 017: 通知ログ + BitNow 交渉保存テーブル (Phase 9)
-- 目的: Slack 通知の送信履歴 + BitNow NEGOTIATE_LATER 管理
-- ============================================================

-- ============================================================
-- notification_log — Slack 通知の送信履歴
-- ============================================================

CREATE TABLE IF NOT EXISTS notification_log (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 通知種別
    notification_type TEXT      NOT NULL,
    -- 'morning_brief'    : 朝ブリーフ
    -- 'level_a_new'      : Level A 新規候補
    -- 'keep_price_alert' : KEEP 候補価格変化
    -- 'ending_soon'      : 終了間近 (1時間以内)
    -- 'bid_ready'        : BID_READY 状態
    -- 'global_lot_alert' : 世界オークション注目 lot
    -- 'bid_result'       : 入札結果
    -- 'nightly_summary'  : 夜次サマリー

    -- 対象参照 (どのレコードに関する通知か)
    candidate_id    UUID,
    watchlist_id    UUID,
    bid_record_id   UUID,
    event_id        UUID,
    lot_id          UUID,

    -- 送信先
    channel         TEXT        NOT NULL DEFAULT 'slack',
    -- 'slack' | 'notion' | 'dashboard'

    -- 通知内容
    message_summary TEXT,                   -- 通知の要約テキスト
    payload         JSONB,                  -- 送信した Block Kit JSON 等

    -- 送信結果
    status          TEXT        NOT NULL DEFAULT 'sent',
    -- 'sent' | 'failed' | 'skipped'
    error_message   TEXT,

    -- メタ
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_notif_log_type
    ON notification_log (notification_type, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_notif_log_candidate
    ON notification_log (candidate_id, sent_at DESC)
    WHERE candidate_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_notif_log_watchlist
    ON notification_log (watchlist_id, sent_at DESC)
    WHERE watchlist_id IS NOT NULL;


-- ============================================================
-- negotiate_later — BitNow / seller 直接交渉用保存テーブル
-- 原則除外だが将来の交渉候補として保存だけしておく
-- ※ 自動交渉・自動送信は未実装。保存のみ。
-- ============================================================

CREATE TABLE IF NOT EXISTS negotiate_later (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 元候補参照
    candidate_id    UUID,                   -- daily_candidates の id (任意)
    source_type     TEXT        NOT NULL,
    -- 'bitnow' | 'private_sale' | 'direct_seller' | 'other'

    -- 出品情報
    title           TEXT        NOT NULL,
    seller_id       TEXT,
    seller_contact  TEXT,                   -- メール / URL 等
    listing_url     TEXT,

    -- コイン情報
    year            INTEGER,
    country         TEXT,
    denomination    TEXT,
    grade           TEXT,
    grader          TEXT,
    cert_company    TEXT,
    cert_number     TEXT,

    -- 価格
    listed_price_usd    NUMERIC(10, 2),
    listed_price_jpy    INTEGER,
    target_price_jpy    INTEGER,            -- 交渉目標価格

    -- 理由・メモ
    save_reason     TEXT,                   -- なぜ保存したか
    notes           TEXT,

    -- ステータス
    status          TEXT        NOT NULL DEFAULT 'saved',
    -- 'saved'       : 保存のみ
    -- 'interested'  : 将来検討
    -- 'contacted'   : 連絡済み
    -- 'negotiating' : 交渉中
    -- 'acquired'    : 取得済み
    -- 'passed'      : 見送り

    -- メタ
    saved_by        TEXT        DEFAULT 'cap',
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_negotiate_later_status
    ON negotiate_later (status, saved_at DESC);

CREATE INDEX IF NOT EXISTS idx_negotiate_later_source
    ON negotiate_later (source_type, status);

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_negotiate_later_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_negotiate_later_updated_at ON negotiate_later;
CREATE TRIGGER trg_negotiate_later_updated_at
    BEFORE UPDATE ON negotiate_later
    FOR EACH ROW EXECUTE FUNCTION update_negotiate_later_updated_at();


-- ============================================================
-- 参照整合性 (Supabase では ALTER TABLE で後付け)
-- ============================================================

-- candidate_watchlist → daily_candidates
-- ALTER TABLE candidate_watchlist
--   ADD CONSTRAINT fk_watchlist_candidate
--   FOREIGN KEY (candidate_id) REFERENCES daily_candidates(id);

-- candidate_match_results → daily_candidates
-- ALTER TABLE candidate_match_results
--   ADD CONSTRAINT fk_match_promoted
--   FOREIGN KEY (promoted_candidate_id) REFERENCES daily_candidates(id);

-- notification_log → bid_records (既存)
-- ALTER TABLE notification_log
--   ADD CONSTRAINT fk_notif_bid_record
--   FOREIGN KEY (bid_record_id) REFERENCES bid_records(id);

-- NOTE: 上記はコメントアウト。Supabase SQL Editor で個別実行すること。
--       daily_candidates / bid_records が先にある前提。
