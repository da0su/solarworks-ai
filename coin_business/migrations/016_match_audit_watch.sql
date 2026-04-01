-- ============================================================
-- 016: マッチング・監査・KEEP監視テーブル (Phase 7 / Phase 8)
-- 目的: BOT抽出+CAP監査の二重チェック基盤と KEEP watchlist
-- ============================================================

-- ============================================================
-- candidate_match_results — BOT抽出 + CAP監査ログ
-- 1段目(機械照合) と 2段目(CAP監査) の両方の記録を保持
-- ============================================================

CREATE TABLE IF NOT EXISTS candidate_match_results (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- 照合元
    source_type         TEXT        NOT NULL,
    -- 'ebay_listing' | 'global_lot'
    ebay_listing_id     UUID        REFERENCES ebay_listings_raw(id),
    global_lot_id       UUID        REFERENCES global_auction_lots(id),

    -- 照合先
    seed_id             UUID        REFERENCES yahoo_coin_seeds(id),

    -- 1段目: 機械照合結果
    match_score         NUMERIC(5, 3),          -- 0.0〜1.0
    match_type          TEXT,                   -- 'cert_exact' | 'year_grade' 等
    candidate_level_bot TEXT,                   -- BOT が判定した Level (A/B/C)
    bot_match_details   JSONB,                  -- 照合詳細 (score内訳等)
    bot_matched_at      TIMESTAMPTZ,

    -- 2段目: CAP監査結果
    audit_status        TEXT,
    -- NULL: 未審査
    -- 'AUDIT_PASS'  : 全チェック通過 → daily_candidates 昇格
    -- 'AUDIT_HOLD'  : 一部条件未達 → 人間確認待ち
    -- 'AUDIT_FAIL'  : 除外条件あり → 候補化しない

    -- 監査チェック項目ごとの結果 (JSONB)
    audit_check_results JSONB,
    -- 例: {"cert_validity": "pass", "title_consistency": "pass",
    --      "grade_delta": "pass", "profit_condition": "fail",
    --      "shipping_valid": "pass", "lot_size_single": "pass",
    --      "not_stale": "pass", "not_sold": "pass", "not_ended": "pass"}

    audit_fail_reasons  TEXT[],                 -- FAIL の理由リスト
    audited_at          TIMESTAMPTZ,

    -- 候補化
    promoted_candidate_id   UUID,               -- daily_candidates に昇格した場合のID
    promoted_at             TIMESTAMPTZ,

    -- メタ
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_match_results_audit_status
    ON candidate_match_results (audit_status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_match_results_source_type
    ON candidate_match_results (source_type, audit_status);

CREATE INDEX IF NOT EXISTS idx_match_results_ebay_listing
    ON candidate_match_results (ebay_listing_id)
    WHERE ebay_listing_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_match_results_global_lot
    ON candidate_match_results (global_lot_id)
    WHERE global_lot_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_match_results_promoted
    ON candidate_match_results (promoted_candidate_id)
    WHERE promoted_candidate_id IS NOT NULL;

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_match_results_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_match_results_updated_at ON candidate_match_results;
CREATE TRIGGER trg_match_results_updated_at
    BEFORE UPDATE ON candidate_match_results
    FOR EACH ROW EXECUTE FUNCTION update_match_results_updated_at();


-- ============================================================
-- candidate_watchlist — CEO KEEP 後の自動監視リスト
-- CEO が KEEP ボタンを押した瞬間に登録される
-- ============================================================

CREATE TABLE IF NOT EXISTS candidate_watchlist (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    candidate_id        UUID        NOT NULL,       -- daily_candidates.id
    -- NOTE: Supabase 側で外部キー制約は別途 ALTER TABLE で追加する

    -- 元の listing 参照
    ebay_item_id        TEXT,
    global_lot_id       UUID,

    -- 監視設定
    watch_mode          TEXT        NOT NULL DEFAULT 'auto',
    -- 'auto': 残時間ベースで自動監視
    -- 'manual': 手動 refresh のみ

    -- 入札上限
    max_bid_jpy         INTEGER,
    max_bid_usd         NUMERIC(10, 2),

    -- 現在状態 (refresh のたびに更新)
    current_price_jpy   INTEGER,
    current_price_usd   NUMERIC(10, 2),
    bid_count           INTEGER,
    auction_end_at      TIMESTAMPTZ,
    time_left_seconds   INTEGER,

    -- BID_READY 判定
    is_bid_ready        BOOLEAN     DEFAULT FALSE,
    bid_ready_reason    TEXT,                       -- BID_READY になった理由

    -- 監視状態
    status              TEXT        NOT NULL DEFAULT 'watching',
    -- 'watching' | 'bid_queued' | 'ended' | 'cancelled'

    -- 最終 refresh 情報
    last_refreshed_at   TIMESTAMPTZ,
    next_refresh_at     TIMESTAMPTZ,
    refresh_interval_seconds INTEGER,
    refresh_count       INTEGER     DEFAULT 0,

    -- アラート送信済みフラグ
    alert_ending_sent   BOOLEAN     DEFAULT FALSE,  -- 終了間近アラート送信済み
    alert_price_sent    BOOLEAN     DEFAULT FALSE,  -- 価格変動アラート送信済み

    -- メタ
    added_by            TEXT        DEFAULT 'ceo',  -- 'ceo' | 'auto'
    added_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_watchlist_status
    ON candidate_watchlist (status, next_refresh_at ASC)
    WHERE status = 'watching';

CREATE INDEX IF NOT EXISTS idx_watchlist_candidate_id
    ON candidate_watchlist (candidate_id);

CREATE INDEX IF NOT EXISTS idx_watchlist_bid_ready
    ON candidate_watchlist (is_bid_ready)
    WHERE is_bid_ready = TRUE AND status = 'watching';

CREATE INDEX IF NOT EXISTS idx_watchlist_end_time
    ON candidate_watchlist (auction_end_at ASC)
    WHERE status = 'watching';

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_watchlist_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_watchlist_updated_at ON candidate_watchlist;
CREATE TRIGGER trg_watchlist_updated_at
    BEFORE UPDATE ON candidate_watchlist
    FOR EACH ROW EXECUTE FUNCTION update_watchlist_updated_at();


-- ============================================================
-- watchlist snapshot — 監視中の価格変動を時系列保存
-- ============================================================

CREATE TABLE IF NOT EXISTS watchlist_snapshots (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    watchlist_id        UUID        NOT NULL REFERENCES candidate_watchlist(id)
                                    ON DELETE CASCADE,

    price_jpy           INTEGER,
    price_usd           NUMERIC(10, 2),
    bid_count           INTEGER,
    time_left_seconds   INTEGER,
    is_active           BOOLEAN,

    snapped_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_snapshots_id
    ON watchlist_snapshots (watchlist_id, snapped_at DESC);
