# coin_business Phase 1〜10 Runbook

> 作成: Day 10 実装完了時点
> 対象: サイバーさん (本番機) の日次運用オペレーター (CAP / CEOの代理実行)

---

## 0. 概要 — 日次自動化フロー

```
06:00 yahoo_sold_sync        Yahoo staging 差分取得
06:30 yahoo_promoter         APPROVED → yahoo_sold_lots 昇格
07:00 seed_generator         yahoo_sold_lots → yahoo_coin_seeds
07:30 global_auction_sync    世界オークション event 同期
08:00 ebay_seed_scanner(1)   Yahoo seed 起点 eBay スキャン (1回目)
08:00 morning_brief          朝ブリーフ Slack 送信 ← Day 10
08:30 global_lot_ingest(1)   世界オークション lot 収集 (1回目)
09:00 match_engine           eBay/lot × seed 照合
09:30 cap_audit              Level A audit gate → daily_candidates 昇格
11:00 pricing_engine         AUDIT_PASS 候補に target_max_bid_jpy 計算
11:30 notion_sync            Notion 候補台帳・KEEP監視台帳 同期 ← Day 10
14:00 ebay_seed_scanner(2)   eBay スキャン (2回目)
14:30 global_lot_ingest(2)   global lot 収集 (2回目)
20:00 ebay_seed_scanner(3)   eBay スキャン (3回目)
*/10  keep_watch_refresher   KEEP watchlist 状態更新
```

---

## 1. 起動手順 (サイバーさん)

```bat
# 全サービス起動 (startup_all.bat)
cd C:\Users\砂田　紘幸\solarworks-ai
.\startup_all.bat
```

スケジューラーが起動済みか確認:
```bat
python ops/scheduler/scheduler.py status
```

---

## 2. コマンドリファレンス

### 2.1 個別コマンド (cd coin_business して実行)

```bash
# ─ Yahoo ─────────────────────────────────────────────────────────
python run.py yahoo-sync              # staging 差分取得
python run.py yahoo-promote           # 承認済みを昇格
python run.py seed-generate           # seed 生成

# ─ eBay / Global ─────────────────────────────────────────────────
python run.py ebay-scan               # eBay seed スキャン
python run.py ebay-ingest             # eBay listing API 取得
python run.py global-sync             # 世界オークション event 同期
python run.py global-ingest           # 世界 lot 収集

# ─ マッチング / 監査 ────────────────────────────────────────────
python run.py match-engine            # Level A/B/C 照合
python run.py cap-audit               # AUDIT_PASS/HOLD/FAIL 判定

# ─ Pricing / Watch ────────────────────────────────────────────────
python run.py run-pricing             # target_max_bid_jpy 計算
python run.py keep-watch              # watchlist 状態更新

# ─ 通知 / 同期 / Dashboard ─────────────────────────────────────
python run.py slack-notify morning-brief        # 朝ブリーフ
python run.py slack-notify ending-soon          # 終了間近通知
python run.py slack-notify bid-ready            # BID_READY 通知
python run.py notion-sync                       # Notion 台帳同期
python run.py dashboard                         # Dashboard 表示
python run.py dashboard --kpi-only              # KPI のみ

# ─ E2E 確認 ──────────────────────────────────────────────────────
python scripts/e2e_dry_run.py                   # 全ステージ dry run
python scripts/e2e_dry_run.py --stage 12        # Slack のみ dry run
```

### 2.2 ジョブ登録確認
```bash
python scripts/register_jobs.py
```

---

## 3. ステータス確認

### 3.1 Dashboard (推奨)
```bash
python run.py dashboard
```

### 3.2 KPI のみ (高速)
```bash
python run.py dashboard --kpi-only
```

### 3.3 重要テーブル件数
```bash
python run.py count
```

---

## 4. トラブルシューティング

### 4.1 Morning Brief が送信されない
1. `.env` に `SLACK_BOT_TOKEN` が設定されているか確認
2. `SLACK_CHANNEL` が正しい channel ID か確認
3. `notification_log` を確認:
   ```sql
   SELECT * FROM notification_log
   WHERE notification_type = 'morning_brief'
   ORDER BY sent_at DESC LIMIT 5;
   ```

### 4.2 Notion 同期が失敗する
1. `.env` に `NOTION_TOKEN` / `NOTION_CANDIDATE_DB_ID` / `NOTION_WATCHLIST_DB_ID` を設定
2. Integration が対象ページに Connect されているか確認
3. dry run で確認:
   ```bash
   python run.py notion-sync --dry-run
   ```

### 4.3 match_engine が 0 件マッチする
1. `yahoo_coin_seeds` に `is_active=True` の seed があるか:
   ```bash
   python run.py seed-generate --dry-run
   ```
2. `ebay_listings_raw` / `global_auction_lots` に active データがあるか:
   ```bash
   python run.py count
   ```

### 4.4 AUDIT_PASS が 0 件
1. Level A match が `candidate_match_results` に入っているか:
   ```sql
   SELECT candidate_level_bot, count(*) FROM candidate_match_results
   GROUP BY candidate_level_bot;
   ```
2. cap-audit を dry run:
   ```bash
   python run.py cap-audit --dry-run --limit 10
   ```

### 4.5 pricing が計算されない
- `daily_candidates` の `audit_status = 'AUDIT_PASS'` かつ `target_max_bid_jpy IS NULL` のレコードが対象
- `reference_price_jpy` または `market_price_jpy` が必要:
  ```sql
  SELECT id, audit_status, reference_price_jpy, target_max_bid_jpy
  FROM daily_candidates WHERE audit_status = 'AUDIT_PASS' LIMIT 10;
  ```

---

## 5. DB マイグレーション適用順

Supabase SQL Editor で以下の順に実行:

```
012_yahoo_staging.sql
013_yahoo_seeds.sql
014_ebay_listing_tables.sql
015_global_auction_tables.sql
016_match_audit_watch.sql
017_notifications_negotiate.sql
018_yahoo_staging_add_columns.sql
019_yahoo_sold_lots_and_seed_columns.sql
020_ebay_listings_add_columns.sql
021_seed_scanner_job.sql
022_global_auction_job.sql
023_match_engine_job.sql
024_pricing_watch_job.sql
025_morning_brief_notion_job.sql   ← Day 10
```

---

## 6. 環境変数チェックリスト

| 変数名                   | 用途                     | 必須   |
|--------------------------|--------------------------|--------|
| SUPABASE_URL             | Supabase 接続            | ✅必須 |
| SUPABASE_KEY             | Supabase service role    | ✅必須 |
| SLACK_BOT_TOKEN          | Slack Bot 通知           | ✅必須 |
| SLACK_CHANNEL            | Slack チャンネル ID      | オプション (デフォルト: #ceo-room) |
| EBAY_CLIENT_ID           | eBay API                 | ✅必須 |
| EBAY_CLIENT_SECRET       | eBay API                 | ✅必須 |
| NOTION_TOKEN             | Notion API               | 同期機能を使う場合 |
| NOTION_CANDIDATE_DB_ID   | Notion 候補台帳 DB ID    | 同期機能を使う場合 |
| NOTION_WATCHLIST_DB_ID   | Notion KEEP監視台帳 DB ID| 同期機能を使う場合 |
