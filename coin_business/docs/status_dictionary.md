# ステータス辞書（Status Dictionary）

**固定日**: Day 1 (基準コミット: 8e228c2)
**参照先コード**: `coin_business/constants.py`

このドキュメントは全テーブル・全フローで使われるステータス名の唯一の定義場所です。
コード内でステータス名をベタ書きすることを禁止し、`constants.py` の定数を参照すること。

---

## 1. Yahoo! staging ステータス

**テーブル**: `yahoo_sold_lots_staging.status`
**定数クラス**: `YahooStagingStatus`

| 値 | 意味 | 次のステータス |
|----|------|--------------|
| `PENDING_CEO` | CEO確認待ち | → `APPROVED_TO_MAIN` / `REJECTED` / `HELD` |
| `APPROVED_TO_MAIN` | CEO/CAP承認済み。昇格処理待ち | → `PROMOTED` |
| `PROMOTED` | `yahoo_sold_lots`（本DB）へ昇格完了 | 終端 |
| `REJECTED` | 却下 | 終端 |
| `HELD` | 保留中。再確認予定 | → `APPROVED_TO_MAIN` / `REJECTED` |

**遷移図**:
```
PENDING_CEO ──→ APPROVED_TO_MAIN ──→ PROMOTED  (終端)
            ──→ REJECTED               (終端)
            ──→ HELD ──→ APPROVED_TO_MAIN
                     ──→ REJECTED
```

**制約**: 最初の10日間は `APPROVED_TO_MAIN` から先に進めない（`CEO_APPROVAL_DAYS = 10`）

---

## 2. Yahoo! レビュー決定

**テーブル**: `yahoo_sold_lot_reviews.decision`

| 値 | 意味 |
|----|------|
| `approved` | 承認（staging → `APPROVED_TO_MAIN`） |
| `rejected` | 却下（staging → `REJECTED`） |
| `held` | 保留（staging → `HELD`） |

---

## 3. 候補レベル

**テーブル**: `ebay_seed_hits.candidate_level`, `candidate_match_results.candidate_level_bot`
**定数クラス**: `CandidateLevel`

| 値 | 意味 | 仕入れ対象 |
|----|------|-----------|
| `A` | cert完全一致 / 高グレード+利益 / ±5年+利益 | ✅ のみ |
| `B` | 価格参考・相場補助 | ❌ |
| `C` | 無関係 | ❌ |

---

## 4. マッチング種別

**テーブル**: `candidate_match_results.match_type`, `ebay_seed_hits.match_type`
**定数クラス**: `MatchType`

| 値 | 意味 |
|----|------|
| `cert_exact` | cert_company + cert_number 完全一致（Level A-1） |
| `high_grade` | Yahoo!基準より高グレード + 利益条件（Level A-2） |
| `year_delta` | 年代差 ±5年 + 利益条件（Level A-3） |
| `title_fuzzy` | タイトル類似（補助データ。Level A 判定には使わない） |

---

## 5. CAP 監査ステータス

**テーブル**: `candidate_match_results.audit_status`
**定数クラス**: `AuditStatus`

| 値 | 意味 | daily_candidates 昇格 |
|----|------|----------------------|
| `AUDIT_PASS` | 全チェック通過 | ✅ 昇格する |
| `AUDIT_HOLD` | 一部条件未達。人間確認待ち | ❌ 昇格しない |
| `AUDIT_FAIL` | 除外条件あり | ❌ 昇格しない。履歴保持 |
| `NULL` | 未審査 | ❌ |

**絶対ルール**: `AUDIT_PASS` 以外は `daily_candidates` に昇格しない。

---

## 6. CAP 監査チェック項目の結果

**テーブル**: `candidate_match_results.audit_check_results` (JSONB)
**定数クラス**: `AuditCheck`

| チェック項目 | キー名 | pass の条件 |
|------------|--------|-----------|
| cert 妥当性 | `cert_validity` | cert_company が NGC / PCGS かつ cert_number 形式が正しい |
| タイトル整合 | `title_consistency` | lot_title がコインの基本情報と一致 |
| グレード差 | `grade_delta` | グレード差が許容範囲内 |
| 年数差 | `year_delta` | |年号差| ≤ 5年（Level A-3 適用時） |
| 利益条件 | `profit_condition` | `projected_profit_jpy > 0` |
| shipping 条件 | `shipping_valid` | eBay: USD / US または UK 発送 |
| lot size | `lot_size_single` | lot_size = 1（単品のみ） |
| stale でない | `not_stale` | `last_status_checked_at` が 6時間以内 |
| sold でない | `not_sold` | `is_sold = false` |
| ended でない | `not_ended` | 終了日時が未来 |

チェック結果値: `"pass"` / `"fail"` / `"warn"` / `"skip"`

---

## 7. 自動評価ティア

**テーブル**: `daily_candidates.auto_tier`
**定数クラス**: `AutoTier`

| 値 | 意味 |
|----|------|
| `AUTO_PASS` | 完全自動承認可能 |
| `AUTO_REVIEW` | CEO レビュー推奨（REVIEW_NG 対象） |
| `AUTO_REJECT` | 自動除外 |

---

## 8. CEO 判断ステータス

**テーブル**: `daily_candidates.ceo_decision`
**定数クラス**: `CeoDecision`

| 値 | 意味 |
|----|------|
| `pending` | 未判断 |
| `approved` | 承認 → bid queue に送れる |
| `rejected` | 却下 |
| `held` | 保留 |
| `ng` | 旧DB互換。`rejected` と同等扱い（`normalize_ceo_decision()` で変換） |

---

## 9. KEEP 監視ステータス

**テーブル**: `candidate_watchlist.status`
**定数クラス**: `WatchStatus`

| 値 | 意味 | アクティブ |
|----|------|----------|
| `watching` | 監視中（初期状態） | ✅ |
| `price_ok` | 価格が目標上限以内 | ✅ |
| `price_too_high` | 価格が上限超過 | ✅ |
| `ending_soon` | 終了間近（1時間以内） | ✅ |
| `bid_ready` | 入札実行可能状態 | ✅ |
| `bid_queued` | 入札キュー登録済み | — |
| `ended` | 終了（落札・流れ問わず） | ❌ 終端 |
| `cancelled` | 監視キャンセル | ❌ 終端 |

**遷移図**:
```
watching ──→ price_ok ──→ ending_soon ──→ bid_ready ──→ bid_queued
         ──→ price_too_high                           ──→ ended (終端)
                                                      ──→ cancelled (終端)
```

---

## 10. KEEP 監視 cadence

**定数クラス**: `WatchCadence`

| 残時間 | refresh 間隔 |
|--------|------------|
| 通常（24h超） | 3時間ごと |
| 24時間以内 | 1時間ごと |
| 6時間以内 | 30分ごと |
| 1時間以内 | 10分ごと |

---

## 11. 世界オークション event ステータス

**テーブル**: `global_auction_events.status`

| 値 | 意味 |
|----|------|
| `upcoming` | 開催予定 |
| `active` | 開催中 |
| `ended` | 終了 |
| `cancelled` | キャンセル |

---

## 12. 世界オークション lot ステータス

**テーブル**: `global_auction_lots.status`

| 値 | 意味 |
|----|------|
| `active` | 出品中 |
| `sold` | 落札済み |
| `passed` | 流れ（不落） |
| `withdrawn` | 取り下げ |

---

## 13. 入札記録ステータス

**テーブル**: `bidding_records.status`
**定数クラス**: `BidStatus`

| 値 | 意味 |
|----|------|
| `queued` | キュー登録済み |
| `submitted` | 送信済み |
| `won` | 落札 |
| `lost` | 落選 |
| `cancelled` | キャンセル |
| `error` | エラー |

---

## 14. eBay listing マッチングステータス

**テーブル**: `ebay_listings_raw.match_status`

| 値 | 意味 |
|----|------|
| `pending` | マッチング未実施 |
| `matched` | seed とマッチした |
| `no_match` | seed とマッチしなかった |
| `audit_pass` | CAP監査通過 |
| `audit_fail` | CAP監査不通過 |

---

## 15. 通知種別・チャネル

**定数クラス**: `NotificationType`, `NotificationChannel`

### 通知種別

| 値 | 場面 |
|----|------|
| `morning_brief` | 毎朝8時のサマリー |
| `level_a_new` | Level A 新規候補発生 |
| `keep_price_alert` | KEEP 候補の価格変化 |
| `ending_soon` | 終了間近（1時間以内） |
| `bid_ready` | BID_READY 状態 |
| `global_lot_alert` | 世界オークション注目 lot |
| `bid_result` | 入札結果（won/lost） |
| `nightly_summary` | 夜次サマリー |

### 通知チャネル

| 値 | 役割 |
|----|------|
| `slack` | リアルタイム通知 |
| `notion` | 履歴・台帳 |
| `dashboard` | 最終判断 UI |

---

## 16. negotiate_later ステータス（BitNow 退避先）

**テーブル**: `negotiate_later.status`

| 値 | 意味 |
|----|------|
| `saved` | 保存のみ |
| `interested` | 将来検討 |
| `contacted` | 連絡済み |
| `negotiating` | 交渉中 |
| `acquired` | 取得済み |
| `passed` | 見送り |

---

## 改定履歴

| 日付 | 変更内容 | 承認者 |
|------|---------|--------|
| 2026-04-01 | Day 1 初版作成（Phase 1-9 全ステータス） | COO (CAP) |
