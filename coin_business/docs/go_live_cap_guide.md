# Go-Live 実行指示書（CAP向け 1ページ版）
対象: キャップさん
作成日: 2026-04-02
目的: migration 適用 → E2E 確認 → Go-Live 判定 の手順を1枚で完結させる

---

## STEP 1: Supabase に migration を適用する（SQL Editor）

**必ず 023 → 024 → 025 の順で実行すること**

| 順序 | ファイル | 実行 |
|------|----------|------|
| 1st  | `coin_business/migrations/023_match_engine_job.sql` | SQL をコピーして実行 → Success 確認 |
| 2nd  | `coin_business/migrations/024_pricing_watch_job.sql` | SQL をコピーして実行 → Success 確認 |
| 3rd  | `coin_business/migrations/025_morning_brief_notion_job.sql` | SQL をコピーして実行 → Success 確認 |

> エラーが出たら: `docs/e2e_rerun_after_migration_025.md` の「1-4. 失敗時の切り分け」を参照

---

## STEP 2: E2E dry run を実行する

```bash
cd coin_business
python scripts/e2e_dry_run.py
```

### 合格条件（全部 OK であること）
```
Total: 14 ok / 0 error / 0 skip
[DONE] 全ステージ完了!
```

### 追加確認: schema 警告が消えていること
以下の WARNING が **出ていないこと** を確認:
```
WARNING ... column daily_candidates.audit_status does not exist   ← 出ていたら NG
WARNING ... column yahoo_coin_seeds.seed_status does not exist    ← 出ていたら NG
WARNING ... column yahoo_coin_seeds.priority_score does not exist ← 出ていたら NG
```

---

## STEP 3: Go-Live 判定

| 確認項目 | コマンド / 方法 | 合格条件 |
|----------|----------------|----------|
| Slack dry-run | `python run.py slack-notify morning-brief --dry-run` | `status=dry_run` が返ること |
| Dashboard 起動 | `streamlit run dashboard.py` または `web/index.html` を開く | KPI が表示され、エラーなし |
| Notion sync | `python run.py notion-sync --dry-run` | エラーなし or 仕様通り skip |

---

## STEP 4: CEOへ報告

以下をSlackに貼る:

```
【Go-Live 報告】
実行日時: YYYY-MM-DD HH:MM
git hash: [git rev-parse --short HEAD の結果]

migration:
  023: OK / NG
  024: OK / NG
  025: OK / NG

E2E: 14/14 OK / [X]/14 OK
schema 警告: なし / あり（詳細: ）

判定: GO-LIVE YES / CONDITIONAL / NO
```

---

## 異常時の緊急対応

| 症状 | 対処 |
|------|------|
| migration で `column already exists` エラー | 正常（IF NOT EXISTS で吸収済み）。続行してよい |
| migration で `table does not exist` エラー | 前の migration が未適用。023→024の順を確認 |
| E2E で `import error` | `git rev-parse --short HEAD` を確認。最新コードか確認 |
| E2E で schema 警告が残る | migration が未適用 or 適用失敗。Supabase で再確認 |
| E2E で 1件でも error | 強行不可。Slackでサイバー/CEOに共有してから判断 |

---

## Go-Live 後の初動確認（実行初日のみ）

```bash
# Yahoo取り込み確認（dry_run=False で1件テスト）
python run.py import-yahoo --limit 1

# DB件数確認
python run.py count
```

- `yahoo_sold_items` が増えていること
- `daily_candidates` が変化していること（0件なら正常 — staging → CEO承認フロー待ち）

---

> 詳細は `docs/go_live_final_decision_sheet.md` と `docs/e2e_rerun_after_migration_025.md` を参照
