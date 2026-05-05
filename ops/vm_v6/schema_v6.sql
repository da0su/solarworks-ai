-- Plan v4 P4: SQLite SSOT 統一 schema (room_bot_v6.db)
-- 既存 room_bot_v5.db の 拡張 + heartbeat / fail_stats / SLO 集約

-- 統一 heartbeat キャッシュ (Phase C-2 の jsonl と同期)
CREATE TABLE IF NOT EXISTS heartbeat_cache (
    action TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 2,
    ts TEXT NOT NULL,
    pid INTEGER,
    phase TEXT,
    current_target TEXT,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    skip_count INTEGER DEFAULT 0,
    extra_json TEXT
);

-- fail_reason の group taxonomy 集計 (Phase C-1 の base)
CREATE TABLE IF NOT EXISTS fail_stats_daily (
    date TEXT,
    action TEXT,
    fail_group TEXT,
    fail_reason TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (date, action, fail_group, fail_reason)
);

-- SLI/SLO 違反履歴
CREATE TABLE IF NOT EXISTS slo_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    function TEXT NOT NULL,
    sli_name TEXT NOT NULL,
    actual_value REAL,
    slo_threshold REAL,
    alert_level TEXT,
    auto_recover_action TEXT,
    resolved_at TEXT
);

-- runtime state SSOT (Phase 2-5 follow_runtime_state.json の DB 版)
CREATE TABLE IF NOT EXISTS runtime_state (
    function TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- patrol log
CREATE TABLE IF NOT EXISTS patrol_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    layer TEXT,
    status TEXT,
    alerts_json TEXT,
    actions_taken_json TEXT
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_fail_daily_date ON fail_stats_daily(date);
CREATE INDEX IF NOT EXISTS idx_slo_detected ON slo_violations(detected_at);
CREATE INDEX IF NOT EXISTS idx_patrol_ts ON patrol_log(ts);
