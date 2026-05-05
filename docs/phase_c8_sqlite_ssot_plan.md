# Phase C-8: SQLite SSOT 統一 設計書

**作成日**: 2026-05-05 | **見積工数**: 2-3週間 | **優先度**: 中

## 目的

分散している heartbeat / log JSON / fail_stats / runtime_state を `room_bot_v5.db` に集約し、
patrol_hourly.py が DB 1ファイルだけ参照するシンプル構造にする。

## 現状の分散状態

```
data/
├── follow_rpa_log.json          (VM bot session ログ)
├── follow_heartbeat.json        (VM bot heartbeat)
├── follow_history.json          (フォロー履歴)
├── like_history.json            (いいね履歴)
├── follow_rpa_state.json        (VM bot state)
├── room_bot.db                  (post_queue + daily_summary)
├── room_bot_v5.db               (execution_log/follow_log/like_log)
state/
├── follow_runtime_state.json    (Phase 2-5 SSOT 試験版)
├── system_state.json            (タスク state)
├── heartbeat_<action>.json      (Phase C-2 統一 heartbeat)
```

合計 10+ のファイルが SSOT 候補として存在 → patrol が読み込み複雑化。

## 統合先設計

### `room_bot_v5.db` に新規テーブル追加

```sql
-- 統一 heartbeat キャッシュ (Phase C-2 の jsonl と同期)
CREATE TABLE heartbeat_cache (
    action TEXT PRIMARY KEY,            -- follow / post / like / followback / replenish
    schema_version INTEGER NOT NULL,
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
CREATE TABLE fail_stats_daily (
    date TEXT,
    action TEXT,
    fail_group TEXT,                     -- auth / env / rate / seed / verify
    count INTEGER,
    PRIMARY KEY (date, action, fail_group)
);

-- SLI/SLO 違反履歴
CREATE TABLE slo_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    function TEXT NOT NULL,
    sli_name TEXT NOT NULL,
    actual_value REAL,
    slo_threshold REAL,
    alert_level TEXT,                    -- CRITICAL / WARN / INFO
    resolved_at TEXT
);

-- runtime state SSOT (Phase 2-5 follow_runtime_state.json の DB 版)
CREATE TABLE runtime_state (
    function TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 移行戦略

- Phase 1 (1週間): heartbeat_writer.py → DB write 追加 (JSONとの dual write)
- Phase 2 (1週間): patrol_hourly.py を DB 経由参照に refactor
- Phase 3 (1週間): JSON ファイルを deprecation (削除前は同期 daemon で間隔放置)

## SQLite concurrent write 注意点

- WAL mode 有効化必須: `PRAGMA journal_mode=WAL`
- VM → HOST DB アクセスは shared folder 経由では不可 (SQLite は network FS NG)
  → VM は heartbeat_<action>.json ファイル書き込み、HOST 側 daemon が DB に取り込む

## 期待効果

- patrol_hourly.py の sloc 半減 (現 600+ → 300)
- 状態の一貫性保証 (transaction で atomic update)
- query で cross-action 集計が容易
- VACUUM, integrity check, FK 制約で healthy 維持

## 完了基準

- patrol_hourly.py が json 参照 0 で動作
- 4機能の runtime state が DB のみで再現可能
- 1週間連続運用で DB integrity OK
