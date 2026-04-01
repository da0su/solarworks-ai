-- ============================================================
-- 015: 世界オークション event / lot テーブル (Phase 6)
-- 目的: Heritage / Spink / Stack's Bowers / Noble 等の
--       オークションを T-minus 運用で事前追跡する
-- ============================================================

-- ============================================================
-- 世界オークション event 台帳
-- ============================================================

CREATE TABLE IF NOT EXISTS global_auction_events (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,

    -- オークションハウス識別
    auction_house       TEXT        NOT NULL,
    -- 'heritage' | 'spink' | 'stacks_bowers' | 'noble' | 'cgb' | 'gorny'

    event_name          TEXT        NOT NULL,       -- イベント名
    event_url           TEXT,                       -- 公式イベントページURL
    event_id_external   TEXT,                       -- オークションハウス側のID

    -- 日程
    auction_date        DATE,                       -- 開催日（UTC日付）
    auction_start_at    TIMESTAMPTZ,                -- 開始日時
    auction_end_at      TIMESTAMPTZ,                -- 終了日時（複数日の場合は最終日）

    -- 開催情報
    location            TEXT,                       -- 開催地
    is_online           BOOLEAN     DEFAULT TRUE,   -- オンライン/現地

    -- コレクション情報
    coin_lot_count      INTEGER,                    -- コイン lot 数（推定）
    total_lot_count     INTEGER,                    -- 全 lot 数

    -- T-minus 監視状態
    t_minus_stage       INTEGER,
    -- NULL: 未監視, 21: T-21, 7: T-7, 3: T-3, 1: T-1, 0: 終了

    -- ステータス
    status              TEXT        NOT NULL DEFAULT 'upcoming',
    -- 'upcoming' | 'active' | 'ended' | 'cancelled'

    -- メタ
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (auction_house, event_id_external)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_global_events_upcoming
    ON global_auction_events (auction_date ASC)
    WHERE status = 'upcoming';

CREATE INDEX IF NOT EXISTS idx_global_events_house
    ON global_auction_events (auction_house, status);

CREATE INDEX IF NOT EXISTS idx_global_events_t_minus
    ON global_auction_events (t_minus_stage, auction_date)
    WHERE status = 'upcoming';

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_global_events_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_global_events_updated_at ON global_auction_events;
CREATE TRIGGER trg_global_events_updated_at
    BEFORE UPDATE ON global_auction_events
    FOR EACH ROW EXECUTE FUNCTION update_global_events_updated_at();


-- ============================================================
-- 世界オークション lot 台帳
-- event の公開 lot を事前に収集・追跡する
-- ============================================================

CREATE TABLE IF NOT EXISTS global_auction_lots (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    event_id            UUID        NOT NULL REFERENCES global_auction_events(id)
                                    ON DELETE CASCADE,

    -- lot 識別
    lot_number          TEXT,                       -- lot番号
    lot_url             TEXT,                       -- lot ページURL
    lot_id_external     TEXT,                       -- オークションハウス側のID

    -- コイン情報
    lot_title           TEXT        NOT NULL,
    year                INTEGER,
    country             TEXT,
    denomination        TEXT,
    grade               TEXT,
    grader              TEXT,
    cert_company        TEXT,
    cert_number         TEXT,

    -- 価格情報
    estimate_low_usd    NUMERIC(10, 2),             -- 推定落札額 下限
    estimate_high_usd   NUMERIC(10, 2),             -- 推定落札額 上限
    current_bid_usd     NUMERIC(10, 2),             -- 現在の入札額（ライブ時）
    final_price_usd     NUMERIC(10, 2),             -- 落札価格（終了後）

    -- 画像
    image_url           TEXT,
    thumbnail_url       TEXT,

    -- T-minus 監視
    watch_priority      INTEGER     DEFAULT 0,
    -- 0=通常, 1=注目候補, 2=Level A 照合済み, 3=KEEP登録済み

    -- 照合・候補化
    seed_match_id       UUID,                       -- ebay_seed_hits の id
    candidate_id        UUID,                       -- daily_candidates の id（候補化済みの場合）

    -- ステータス
    status              TEXT        NOT NULL DEFAULT 'active',
    -- 'active' | 'sold' | 'passed' | 'withdrawn'

    -- メタ
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (event_id, lot_id_external)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_global_lots_event_id
    ON global_auction_lots (event_id, lot_number);

CREATE INDEX IF NOT EXISTS idx_global_lots_watch_priority
    ON global_auction_lots (watch_priority DESC, last_refreshed_at ASC);

CREATE INDEX IF NOT EXISTS idx_global_lots_cert
    ON global_auction_lots (cert_company, cert_number)
    WHERE cert_company IS NOT NULL AND cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_global_lots_status
    ON global_auction_lots (status);

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION update_global_lots_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_global_lots_updated_at ON global_auction_lots;
CREATE TRIGGER trg_global_lots_updated_at
    BEFORE UPDATE ON global_auction_lots
    FOR EACH ROW EXECUTE FUNCTION update_global_lots_updated_at();


-- ============================================================
-- 世界オークション lot 価格追跡 snapshot
-- ============================================================

CREATE TABLE IF NOT EXISTS global_lot_price_snapshots (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    lot_id              UUID        NOT NULL REFERENCES global_auction_lots(id)
                                    ON DELETE CASCADE,

    current_bid_usd     NUMERIC(10, 2),
    bid_count           INTEGER,
    time_left_hours     NUMERIC(8, 2),
    snapped_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_global_lot_snapshots_lot_id
    ON global_lot_price_snapshots (lot_id, snapped_at DESC);
