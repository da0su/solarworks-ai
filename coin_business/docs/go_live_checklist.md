# coin_business Go-Live Checklist (Phase 1〜10)

> 本番稼働前に全項目を確認すること。
> ✅ = 完了, ⬜ = 未確認, ❌ = NG

---

## A. 環境・接続

- [ ] A-1  `.env` に `SUPABASE_URL` / `SUPABASE_KEY` が設定されている
- [ ] A-2  Supabase 接続テスト: `python run.py count` が正常終了する
- [ ] A-3  `.env` に `SLACK_BOT_TOKEN` が設定されている
- [ ] A-4  Slack Bot が `#ceo-room` チャンネルに参加済み
- [ ] A-5  `.env` に `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` が設定されている
- [ ] A-6  eBay API トークン取得テスト: `python scripts/ebay_api_client.py` が成功
- [ ] A-7  (任意) `.env` に `NOTION_TOKEN` / `NOTION_CANDIDATE_DB_ID` / `NOTION_WATCHLIST_DB_ID`

---

## B. DB マイグレーション

- [ ] B-1  `012_yahoo_staging.sql` 適用済み
- [ ] B-2  `013_yahoo_seeds.sql` 適用済み
- [ ] B-3  `014_ebay_listing_tables.sql` 適用済み
- [ ] B-4  `015_global_auction_tables.sql` 適用済み
- [ ] B-5  `016_match_audit_watch.sql` 適用済み
- [ ] B-6  `017_notifications_negotiate.sql` 適用済み
- [ ] B-7  `018_yahoo_staging_add_columns.sql` 適用済み
- [ ] B-8  `019_yahoo_sold_lots_and_seed_columns.sql` 適用済み
- [ ] B-9  `020_ebay_listings_add_columns.sql` 適用済み
- [ ] B-10 `021_seed_scanner_job.sql` 適用済み
- [ ] B-11 `022_global_auction_job.sql` 適用済み
- [ ] B-12 `023_match_engine_job.sql` 適用済み
- [ ] B-13 `024_pricing_watch_job.sql` 適用済み
- [ ] B-14 `025_morning_brief_notion_job.sql` 適用済み

確認コマンド:
```sql
-- job テーブルが存在するか確認
SELECT tablename FROM pg_tables
WHERE schemaname = 'public'
AND tablename LIKE 'job_%'
ORDER BY tablename;
```

---

## C. データ準備

- [ ] C-1  `yahoo_sold_lots_staging` に初期データ投入済み (Excel import or API)
- [ ] C-2  CEO が Yahoo staging を確認・承認済み (`APPROVED_TO_MAIN` 件数 > 0)
- [ ] C-3  `yahoo_sold_lots` に昇格済みデータがある (`yahoo_promoter` 実行済み)
- [ ] C-4  `yahoo_coin_seeds` に `is_active=True` の seed がある:
  ```sql
  SELECT count(*) FROM yahoo_coin_seeds WHERE is_active = TRUE;
  ```

---

## D. E2E dry run

- [ ] D-1  全ステージ dry run 完了:
  ```bash
  python scripts/e2e_dry_run.py
  ```
- [ ] D-2  全ステージ `✅ ok` (または `⏭ skip`)
- [ ] D-3  エラーが `0` 件
- [ ] D-4  Dashboard が正常表示:
  ```bash
  python run.py dashboard --kpi-only
  ```

---

## E. Slack 通知

- [ ] E-1  朝ブリーフ dry run 成功:
  ```bash
  python run.py slack-notify morning-brief --dry-run
  ```
- [ ] E-2  朝ブリーフを手動送信してテスト:
  ```bash
  python run.py slack-notify morning-brief
  ```
- [ ] E-3  Slack `#ceo-room` にメッセージが届いた
- [ ] E-4  `notification_log` に `status='sent'` レコードがある

---

## F. スケジューラー

- [ ] F-1  スケジューラー起動: `startup_all.bat`
- [ ] F-2  ジョブ一覧確認: `python scripts/register_jobs.py`
- [ ] F-3  全ジョブが `READY` 状態である
- [ ] F-4  翌朝 06:00 以降に `job_yahoo_sold_sync_daily` にレコードが入っている
- [ ] F-5  翌朝 08:00 以降に `job_morning_brief_daily` にレコードが入っている

---

## G. 初回本番実行後の確認

- [ ] G-1  `match_engine` が Level A レコードを生成した:
  ```sql
  SELECT candidate_level_bot, count(*) FROM candidate_match_results
  GROUP BY candidate_level_bot;
  ```
- [ ] G-2  `cap_audit` が AUDIT_PASS を生成し `daily_candidates` に昇格した:
  ```sql
  SELECT audit_status, count(*) FROM daily_candidates
  GROUP BY audit_status;
  ```
- [ ] G-3  `pricing_engine` が `target_max_bid_jpy` を計算した:
  ```sql
  SELECT count(*) FROM daily_candidates
  WHERE target_max_bid_jpy IS NOT NULL;
  ```
- [ ] G-4  Dashboard で全 KPI が 0 以外になっている:
  ```bash
  python run.py dashboard --kpi-only
  ```

---

## H. Notion 同期 (任意)

- [ ] H-1  Notion に "候補台帳" データベースを作成し Integration を Connect
- [ ] H-2  Notion に "KEEP監視台帳" データベースを作成し Integration を Connect
- [ ] H-3  `.env` に `NOTION_CANDIDATE_DB_ID` / `NOTION_WATCHLIST_DB_ID` を設定
- [ ] H-4  dry run 確認:
  ```bash
  python run.py notion-sync --dry-run
  ```
- [ ] H-5  本番同期実行:
  ```bash
  python run.py notion-sync
  ```
- [ ] H-6  Notion に候補レコードが表示された

---

## I. Go-Live 承認

| 担当  | 確認内容               | 承認 |
|-------|------------------------|------|
| CAP   | B, C, D, E, F 完了     | ⬜   |
| CEO   | G 確認 (Dashboard 表示)| ⬜   |

---

## J. 緊急停止手順

```bash
# スケジューラー停止
python ops/scheduler/scheduler.py stop

# 運用モード STOP に変更 (BOT 側)
python rakuten-room/bot/run.py mode STOP

# coin_business のジョブを全停止
# → ops/scheduler/ の state ファイルを確認
```
