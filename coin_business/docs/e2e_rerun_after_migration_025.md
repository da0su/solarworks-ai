# migration 025 適用後の E2E 再実行手順書

目的:
Supabase 側に最新 migration を反映した後、E2E dry run を再実行し、
schema 警告が消えた完全クリーン状態を確認する

---

## 0. 前提

以下の migration が Supabase に適用済みであること:

1. `migrations/023_match_engine_job.sql`
2. `migrations/024_pricing_watch_job.sql`
3. `migrations/025_morning_brief_notion_job.sql`

注意:
- 025 だけ適用しても、023 / 024 が未適用だと schema 警告が残る可能性がある
- 必ず 023 → 024 → 025 の順で確認する

---

## 1. Supabase SQL Editor 作業

### 1-1. migration 023 を適用
- ファイル: `migrations/023_match_engine_job.sql`
- 実行結果が Success であること

### 1-2. migration 024 を適用
- ファイル: `migrations/024_pricing_watch_job.sql`
- 実行結果が Success であること

### 1-3. migration 025 を適用
- ファイル: `migrations/025_morning_brief_notion_job.sql`
- 実行結果が Success であること

### 1-4. 失敗時の切り分け

| エラー種別            | 確認箇所                        |
|-----------------------|---------------------------------|
| 同名列の重複追加       | `ADD COLUMN IF NOT EXISTS` を使う |
| 既存 index 重複        | `CREATE INDEX IF NOT EXISTS` を使う |
| 依存テーブル未作成     | 前の migration が適用済みか確認 |

---

## 2. ローカル作業前チェック

### 2-1. 最新コード確認
```bash
cd coin_business
git rev-parse --short HEAD
```

期待: 最新コミット（`9dd9c15` 以降）が反映されていること

### 2-2. Python 環境確認
```bash
python --version
pip list | findstr supabase
```

### 2-3. 必須 env 確認
`.env` に以下が設定されていること:

| 変数名                   | 必須  | 用途                        |
|--------------------------|-------|-----------------------------|
| `SUPABASE_URL`           | 必須  | Supabase 接続               |
| `SUPABASE_KEY`           | 必須  | Supabase service role       |
| `SLACK_BOT_TOKEN`        | 必須  | Slack 通知                  |
| `EBAY_CLIENT_ID`         | 必須  | eBay API                    |
| `EBAY_CLIENT_SECRET`     | 必須  | eBay API                    |
| `NOTION_TOKEN`           | 任意  | Notion 同期 (未設定→skip)   |
| `NOTION_CANDIDATE_DB_ID` | 任意  | Notion 候補台帳             |
| `NOTION_WATCHLIST_DB_ID` | 任意  | Notion KEEP監視台帳         |

---

## 3. E2E dry run 実行

### 3-1. 全ステージ実行
```bash
cd coin_business
python scripts/e2e_dry_run.py
```

### 3-2. 特定ステージのみ確認したい場合
```bash
# Slack のみ
python scripts/e2e_dry_run.py --stage 12

# Stage 5 以降
python scripts/e2e_dry_run.py --from 5

# match + audit + pricing + watch
python scripts/e2e_dry_run.py --from 8 --to 11
```

### 3-3. 期待結果
```
======================================================================
E2E Dry Run Summary
======================================================================
  [OK]   Stage 01 Yahoo Staging Sync    (XXXms) exit_code=0
  [OK]   Stage 02 Yahoo Promoter        (XXXms) promoted=0 errors=0
  [OK]   Stage 03 Seed Generator        (XXXms) lots=0 seeds=0 upserted=0
  [OK]   Stage 04 eBay Seed Scanner     (XXXms) import_ok (no run() API)
  [OK]   Stage 05 eBay API Ingest       (XXXms) seeds=0 listings=0 saved=0
  [OK]   Stage 06 Global Auction Sync   (XXXms) fetched=0 synced=0 new=0
  [OK]   Stage 07 Global Lot Ingest     (XXXms) events=0 lots_fetched=0 saved=0
  [OK]   Stage 08 Match Engine          (XXXms) listings=0 lots=0 matches=0
  [OK]   Stage 09 CAP Audit             (XXXms) audited=0 pass=0 hold=0 fail=0
  [OK]   Stage 10 Pricing Engine        (XXXms) found=0 priced=0
  [OK]   Stage 11 Keep Watch Refresher  (XXXms) checked=0 bid_ready=0 ended=0
  [OK]   Stage 12 Slack Morning Brief   (XXXms) status=dry_run kpi_keys=[...]
  [OK]   Stage 13 Notion Sync           (XXXms) candidates=0 watchlist=0 errors=0
  [OK]   Stage 14 Dashboard             (XXXms) yahoo_pending=N audit_pass=N
----------------------------------------------------------------------
  Total: 14 ok / 0 error / 0 skip
  [DONE] 全ステージ完了!
```

---

## 4. 重点確認ポイント

### 4-1. schema 警告が消えていること

以前の実行で出ていた以下の警告が出なくなること:

```
WARNING ... column daily_candidates.audit_status does not exist
WARNING ... column yahoo_coin_seeds.seed_status does not exist
WARNING ... column yahoo_coin_seeds.priority_score does not exist
```

### 4-2. Slack notifier

```bash
# dry_run での確認
python run.py slack-notify morning-brief --dry-run

# 実送信（テストチャンネルへ）
python run.py slack-notify morning-brief
```

- `status=dry_run` または `status=sent` が返ること
- `notification_log` にレコードが作成されること（実送信時）

### 4-3. Notion sync

```bash
# dry_run
python run.py notion-sync --dry-run
```

- DB ID 未設定の場合: skip して異常終了しないこと
- DB ID 設定済みの場合: upsert が実行され `candidates_synced` > 0 になること

### 4-4. Dashboard

```bash
python run.py dashboard --kpi-only
```

- KPI が表示されること
- エラーなく終了すること

---

## 5. ログ確認

### 成功条件
- traceback がない
- `import error` がない
- 関数名不一致がない
- Windows 環境でも文字化け・cp932 エラーが出ない

### 失敗時の切り分け

| エラー種別             | 確認箇所                                      |
|------------------------|-----------------------------------------------|
| DB schema error        | migration 適用漏れを確認                      |
| env / credential error | `.env` の API key / DB ID / webhook を確認     |
| import error           | `git pull` で最新コードを取得                 |
| encoding error         | stdout を UTF-8 に再設定（既に対応済み）      |
| 0件警告                | データ未投入なら正常（stage は OK のまま）    |

---

## 6. Go-Live 判定条件

以下をすべて満たしたら `docs/go_live_final_decision_sheet.md` の判定へ進む:

- [ ] migration 023 適用済み
- [ ] migration 024 適用済み
- [ ] migration 025 適用済み
- [ ] `python scripts/e2e_dry_run.py` が正常完了
- [ ] schema 警告が消えている
- [ ] Slack notifier 正常
- [ ] Notion sync 正常または仕様通り skip
- [ ] dashboard 起動確認済み
- [ ] `docs/go_live_checklist.md` A〜F 完了

---

## 7. 実行ログ貼り付け欄

```
実行日時:
git hash:
実行者:

Stage 01:
Stage 02:
Stage 03:
Stage 04:
Stage 05:
Stage 06:
Stage 07:
Stage 08:
Stage 09:
Stage 10:
Stage 11:
Stage 12:
Stage 13:
Stage 14:

Total:
WARN:
ERROR:
判定:
```
