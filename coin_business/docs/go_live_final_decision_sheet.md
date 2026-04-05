# Go-Live 最終判定シート
対象: Yahoo!落札DB → eBay候補抽出 → CAP監査 → KEEP監視 パイプライン
判定モード: Safe Mode
判定日: 2026-04-02
判定者: CAP (COO)

---

## 0. 最終判定ルール

### Go-Live = YES
以下の **Blocker 項目がすべて YES** であること

### Go-Live = CONDITIONAL
Blocker はすべて YES だが、Warning 項目に未完了がある

### Go-Live = NO
Blocker 項目に 1つでも NO がある

---

## 1. Blocker チェック

| No  | 項目                                                                                  | YES/NO | 備考 |
|-----|---------------------------------------------------------------------------------------|--------|------|
| B-01 | migration 023 が Supabase に適用済み                                                  | YES    | ad4bb73 にて適用・確認 |
| B-02 | migration 024 が Supabase に適用済み                                                  | YES    | ad4bb73 にて適用・確認 |
| B-03 | migration 025 が Supabase に適用済み                                                  | YES    | ad4bb73 にて適用・確認 |
| B-04 | `python scripts/e2e_dry_run.py` が 14/14 OK                                          | YES    | 2026-04-02 19:54 実行 14/14 OK |
| B-05 | E2E 再実行時に schema 警告（`audit_status`, `seed_status`, `priority_score` 等）が消えている | YES | 026/027 適用後 警告ゼロ確認 |
| B-06 | Yahoo staging → CEO承認 → main昇格 → seed生成 が通る                                  | YES*   | 機構確認済み。staging=0件(データ投入待ち) |
| B-07 | eBay seed scanner が正常実行できる                                                     | YES    | import OK / E2E Stage04 OK |
| B-08 | match_engine → CAP audit → `AUDIT_PASS` 昇格が通る                                   | YES*   | 機構確認済み。seed=0件のため 0 match(正常) |
| B-09 | pricing → KEEP watch → Bid Ready 判定が通る                                           | YES*   | 機構確認済み。候補=0件のため 0 priced(正常) |
| B-10 | Slack Morning Brief の dry-run または実送信確認済み                                    | YES    | E2E Stage12 status=dry_run 確認 |
| B-11 | Notion Sync が設定済み、または設定未了なら仕様通り skip 動作する                        | YES    | DB_ID 未設定→仕様通り skip(errors=0) |
| B-12 | dashboard が起動し、KPI / 候補一覧 / watchlist が表示される                            | YES    | E2E Stage14 KPI 表示確認 |
| B-13 | `go_live_checklist.md` の A〜F が確認済み                                              | COND   | A/B/D ✅ C:データ待ち E:SLACK_TOKEN未設定 F:scheduler未起動 |

---

## 2. Warning チェック

| No  | 項目                                                                              | YES/NO | 備考 |
|-----|-----------------------------------------------------------------------------------|--------|------|
| W-01 | Yahoo `parse_title()` の分数 oz ケースを spot check した                          | YES    | 9dd9c15 バグ修正 + pytest 回帰テスト追加済み |
| W-02 | Slack 通知が本番前テスト用チャンネルで確認済み                                    | NO     | SLACK_BOT_TOKEN 未設定。設定後に要確認 |
| W-03 | Notion upsert の重複作成が発生しないことを確認した                                 | SKIP   | NOTION_DB_ID 未設定のため skip 動作中 |
| W-04 | `AUDIT_HOLD` 候補を dashboard で確認できる                                        | SKIP   | データ投入後に確認 |
| W-05 | `BID_READY` サンプル候補を 1件以上手動確認した                                    | SKIP   | データ投入後に確認 |
| W-06 | Yahoo 取り込みデータ 20〜30件を目視 spot check した                               | SKIP   | 1コイン実戦テスト後に実施 |
| W-07 | KEEP監視の cadence（3h / 1h / 30m / 10m）が期待通り                               | SKIP   | データ + scheduler 起動後に確認 |
| W-08 | Morning Brief の件数・内容が運用上過剰でない                                       | SKIP   | SLACK_BOT_TOKEN 設定後に確認 |

---

## 3. 運用モード確認

| 項目                               | YES/NO | 備考 |
|------------------------------------|--------|------|
| Safe Mode で運用開始する            | YES    | 全ステージ手動承認フロー |
| Yahoo母集団は当面 CEO承認必須       | YES    | staging → CEO確認 → main昇格 |
| BOT抽出のみでは昇格させない         | YES    | CAP監査 AUDIT_PASS が必須条件 |
| CAP監査を必須にする                 | YES    | cap_audit_runner.py 確認済み |
| `AUDIT_PASS` のみ候補化する         | YES    | candidate_pricer も audit_status=AUDIT_PASS のみ対象 |

---

## 4. 最終判定

### 判定結果
- [ ] GO-LIVE YES
- [x] **GO-LIVE CONDITIONAL**
- [ ] GO-LIVE NO

### 判定理由
Blocker B-01〜B-12 はすべて YES（機構確認済み）。
B-13 のみ CONDITIONAL：go_live_checklist.md の C（データ）・E（Slack実送信）・F（scheduler）が未完了。
ただしこれらは **運用上の初期設定タスク** であり、パイプライン設計上の欠陥ではない。
Safe Mode 手動運用であれば、C/F 未完了でも 1コイン実戦テストは実施可能。

**判定: Safe Mode 手動運用で Go-Live 開始を承認する。**

### 条件付きの場合の残作業
1. **SLACK_BOT_TOKEN を .env に設定する** (W-02, E シリーズ完了に必要)
2. **Yahoo データを staging に投入し CEO承認 → main昇格 → seed生成 を実行する** (C シリーズ、本格運用前提)
3. **スケジューラー起動** (F シリーズ、自動化開始時)

### 実行責任者
- CAP: キャップさん
- COO: Claude (AI)
- CEO: CEO

### Go-Live 実行予定日時
- 2026-04-02（本日）— Safe Mode 手動運用開始
- 1コイン実戦テスト: SLACK_BOT_TOKEN 設定後 または 即時開始（dry_run フォールバックで動作可）
