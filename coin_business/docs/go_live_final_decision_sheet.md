# Go-Live 最終判定シート
対象: Yahoo!落札DB → eBay候補抽出 → CAP監査 → KEEP監視 パイプライン
判定モード: Safe Mode
判定日:
判定者:

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
| B-01 | migration 023 が Supabase に適用済み                                                  |        |      |
| B-02 | migration 024 が Supabase に適用済み                                                  |        |      |
| B-03 | migration 025 が Supabase に適用済み                                                  |        |      |
| B-04 | `python scripts/e2e_dry_run.py` が 14/14 OK                                          |        |      |
| B-05 | E2E 再実行時に schema 警告（`audit_status`, `seed_status`, `priority_score` 等）が消えている |   |   |
| B-06 | Yahoo staging → CEO承認 → main昇格 → seed生成 が通る                                  |        |      |
| B-07 | eBay seed scanner が正常実行できる                                                     |        |      |
| B-08 | match_engine → CAP audit → `AUDIT_PASS` 昇格が通る                                   |        |      |
| B-09 | pricing → KEEP watch → Bid Ready 判定が通る                                           |        |      |
| B-10 | Slack Morning Brief の dry-run または実送信確認済み                                    |        |      |
| B-11 | Notion Sync が設定済み、または設定未了なら仕様通り skip 動作する                        |        |      |
| B-12 | dashboard が起動し、KPI / 候補一覧 / watchlist が表示される                            |        |      |
| B-13 | `go_live_checklist.md` の A〜F が確認済み                                              |        |      |

---

## 2. Warning チェック

| No  | 項目                                                                              | YES/NO | 備考 |
|-----|-----------------------------------------------------------------------------------|--------|------|
| W-01 | Yahoo `parse_title()` の分数 oz ケースを spot check した                          |        |      |
| W-02 | Slack 通知が本番前テスト用チャンネルで確認済み                                    |        |      |
| W-03 | Notion upsert の重複作成が発生しないことを確認した                                 |        |      |
| W-04 | `AUDIT_HOLD` 候補を dashboard で確認できる                                        |        |      |
| W-05 | `BID_READY` サンプル候補を 1件以上手動確認した                                    |        |      |
| W-06 | Yahoo 取り込みデータ 20〜30件を目視 spot check した                               |        |      |
| W-07 | KEEP監視の cadence（3h / 1h / 30m / 10m）が期待通り                               |        |      |
| W-08 | Morning Brief の件数・内容が運用上過剰でない                                       |        |      |

---

## 3. 運用モード確認

| 項目                               | YES/NO | 備考 |
|------------------------------------|--------|------|
| Safe Mode で運用開始する            |        |      |
| Yahoo母集団は当面 CEO承認必須       |        |      |
| BOT抽出のみでは昇格させない         |        |      |
| CAP監査を必須にする                 |        |      |
| `AUDIT_PASS` のみ候補化する         |        |      |

---

## 4. 最終判定

### 判定結果
- [ ] GO-LIVE YES
- [ ] GO-LIVE CONDITIONAL
- [ ] GO-LIVE NO

### 判定理由


### 条件付きの場合の残作業
1.
2.
3.

### 実行責任者
- CAP:
- COO:
- CEO:

### Go-Live 実行予定日時
-
