# CAP Phase 1〜9 開発スケジュール — Day 1〜Day 10

**基準コミット**: `8e228c2`
**対象**: Phase 1〜9（Phase 10 は対象外）
**方針**: Yahoo!母集団安全性の確立 → eBay取得 → 候補抽出・CAP監査 → 監視・通知

---

## 実装の絶対ルール

| # | ルール |
|---|--------|
| 1 | 母集団は Yahoo!落札履歴のみ |
| 2 | Yahoo!落札履歴は当面10日間、CEO承認済みのみ本DB昇格 |
| 3 | Level A = cert完全一致 / Yahoo基準より高グレード+利益条件達成 / 年代差±5年+利益条件達成 |
| 4 | BOT抽出結果は必ず CAP監査を通す |
| 5 | AUDIT_PASS のみ daily_candidates に昇格 |
| 6 | BitNow は本流に入れず negotiate_later に退避 |
| 7 | 最終目標は CEO が候補精査から離れ、入札・買付だけに集中する状態 |

---

## 10日間 全体像

| Day | 主対象 Phase | その日の到達目標 |
|-----|------------|----------------|
| Day 1 | Phase 1 | ルール凍結・定数化・既存 migration の整合確認 |
| Day 2 | Phase 2 | Yahoo staging 取得ジョブを動かす |
| Day 3 | Phase 3 | Dashboard に CEO確認待ちタブを実装 |
| Day 4 | Phase 4 | 承認昇格ジョブと seed 生成を接続 |
| Day 5 | Phase 5 | eBay API の正式取得基盤を接続 |
| Day 6 | Phase 5 | eBay seed scanner と snapshot 更新を完成 |
| Day 7 | Phase 6 | 世界オークション収集と T-minus 基盤を実装 |
| Day 8 | Phase 7 | match_engine + CAP監査で候補昇格を制御 |
| Day 9 | Phase 8 | pricing / watchlist / KEEP監視を完成 |
| Day 10 | Phase 9 | Slack / Notion / Dashboard統合 + E2E確認 |

---

## Day 1 — ルール凍結と土台確認

**目的**: 仕様が揺れないようにする日。
コードを書き始める前に、ルールの参照先を1つに固定し、実装側の定数・状態名・enum をぶらさない。

### コミット構成

**Commit 1**
```
docs(cap): freeze phase1-9 operating rules and promotion gates
```
- `docs/cap_execution_order.md` を実装基準文書として固定
- `docs/decision_rules.md`（新規）: Level A/B/C 定義を切り出す
- `docs/status_dictionary.md`（新規）: 全ステータス名を一覧化
  ```
  AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL
  PENDING_CEO / APPROVED_TO_MAIN / REJECTED / HELD / PROMOTED
  WATCHING / PRICE_OK / PRICE_TOO_HIGH / ENDING_SOON / BID_READY
  ```

**Commit 2**
```
refactor(core): add shared constants for statuses, match levels, and watch cadence
```
- `coin_business/constants.py`（新規）: Python 側の共通定数
- `coin_business/types.py`（新規）: 型定義・enum
- `coin_business/config.py`: 設定値を定数に寄せる
- ステータス名のベタ書き禁止
- Level A/B/C、監視頻度、通知タイプ、ソース名を共通化

**Commit 3**
```
chore(db): validate migrations 012-017 ordering and schema assumptions
```
- `scripts/schema_smoke_check.py`（新規）: migration 適用順・index・FK・unique の監査
- `docs/migration_apply_order.md`（新規）: 依存関係チェック
  ```
  012 yahoo_staging
  → 013 yahoo_seeds
  → 014 ebay_listing_tables
  → 015 global_auction_tables
  → 016 match_audit_watch
  → 017 notifications_negotiate
  ```

### Day 1 完了条件

- [ ] ステータス名が docs / Python / SQL で一致している
- [ ] migration 012〜017 の適用順が固定されている
- [ ] CAP が Day 2 から「定義確認」で止まらない状態になっている

---

## Day 2 — Yahoo staging 取得を実装

**目的**: Yahoo!履歴をいきなり本DBに入れず staging に入れる流れを動かす。
「取得して PENDING_CEO で保留する」までを完成させる。

### コミット構成

**Commit 1**
```
feat(yahoo): ingest sold lots into yahoo_sold_lots_staging with pending_ceo status
```
- `scripts/yahoo_sold_sync.py` 実装
  - Yahoo! 落札履歴の取得
  - 正規化処理（タイトル・cert・grade・year 抽出）
  - `yahoo_sold_lots_staging` への upsert
  - 初期状態は必ず `PENDING_CEO`
- `coin_business/yahoo/parser.py`（新規）
- `coin_business/yahoo/normalizer.py`（新規）
- `coin_business/db/yahoo_repo.py`（新規）

**Commit 2**
```
test(yahoo): add parser and staging upsert tests
```
- `tests/test_yahoo_parser.py`: タイトル正規化・cert抽出・年号/額面/グレード抽出テスト
- `tests/test_yahoo_staging_repo.py`: 同一 Yahoo listing の重複 upsert テスト

**Commit 3**
```
ops(yahoo): register daily yahoo sold sync job and logging
```
- `scripts/register_jobs.py`: 毎日 6:00 実行ジョブ登録
- `coin_business/logging.py`: sync 件数 / parse success / parse fail を計測
- `docs/job_schedule.md`（新規 or 更新）

### Day 2 完了条件

- [ ] staging に Yahoo レコードが毎日入る
- [ ] 本DBにはまだ入らない
- [ ] parse failure が可視化される
- [ ] 全件に `PENDING_CEO` が正しく付く

---

## Day 3 — Dashboard に「Yahoo!履歴 CEO確認待ち」タブを実装

**目的**: CEO が Yahoo staging をレビューできなければ運用が成立しない。
レビュー UI を完成させる。

### コミット構成

**Commit 1**
```
feat(dashboard): add Yahoo sold lots pending CEO review tab
```
- `dashboard.py` に新タブ追加
- 絞り込みフィルタ（PENDING_CEO / certあり / parse confidence / price range / year / grade）
- `coin_business/dashboard/yahoo_review.py`（新規）

**Commit 2**
```
feat(dashboard): implement approve reject hold actions for yahoo staging reviews
```
- 「承認」「却下」「保留」ボタン
- `yahoo_sold_lot_reviews` へレビュー履歴保存
- staging_status 更新（PENDING_CEO → APPROVED_TO_MAIN / REJECTED / HELD）
- `reviewed_at` / `reviewed_by` / `comment` / `rejection_reason` 保存
- `coin_business/db/yahoo_review_repo.py`（新規）

**Commit 3**
```
test(dashboard): add review flow tests for yahoo pending queue
```
- `tests/test_yahoo_review_flow.py`
  - approve / reject / hold 動作
  - 二重レビュー防止
  - 直近レビュー履歴表示

### Day 3 完了条件

- [ ] CEO が dashboard から staging を処理できる
- [ ] 承認・却下・保留が永続化される
- [ ] 承認履歴が残る
- [ ] まだ seed は生成しない

---

## Day 4 — 承認昇格ジョブと seed 生成を接続

**目的**: CEO承認済みデータだけが正式母集団に入る線を完成させる。

### コミット構成

**Commit 1**
```
feat(yahoo): promote approved staging rows into yahoo master table
```
- `scripts/yahoo_promoter.py` 実装
  - `APPROVED_TO_MAIN` の staging を `yahoo_sold_lots` に昇格
  - 昇格後に staging 側へ `promotion_timestamp` を残す
  - idempotent に実装
- `coin_business/db/yahoo_promoter_repo.py`（新規）

**Commit 2**
```
feat(seed): generate yahoo coin seeds from approved master only
```
- `scripts/seed_generator.py` 実装
  - seed パターン: cert_exact / cert_title / title_normalized / year+denom+grade
  - 参照元は `yahoo_sold_lots` のみ（staging 誤参照を禁止）
- `coin_business/seeds/builder.py`（新規）

**Commit 3**
```
test(seed): enforce no staging leakage in seed generation
```
- `tests/test_seed_generator.py`
  - staging データが seed に混じらないテスト
  - 承認済みのみ seed 化
  - duplicate seed 抑止

### Day 4 完了条件

- [ ] CEO承認済み Yahoo 履歴だけが本DBへ昇格する
- [ ] seed が自動生成される
- [ ] 未承認 Yahoo データは探索に使われない

---

## Day 5 — eBay API 正式取得基盤を実装

**目的**: eBay API 接続・取得・保存の下回りを完成させる。
まだマッチングには進まず、raw 取得を主眼とする。

### コミット構成

**Commit 1**
```
feat(ebay): add approved API client and listing ingestion service
```
- `coin_business/ebay/client.py`（新規）: 認証・token 更新・listing 取得基盤・リトライ
- `coin_business/ebay/auth.py`（新規）
- `scripts/ebay_api_ingest.py`

**Commit 2**
```
feat(ebay): persist raw listings and snapshots from API responses
```
- `ebay_listings_raw` への upsert
- `ebay_listing_snapshots` への時系列保存（取得時刻・価格・入札数・終了時刻）
- `coin_business/db/ebay_repo.py`（新規）

**Commit 3**
```
test(ebay): add API smoke tests and schema mapping validation
```
- `tests/test_ebay_client.py`: API レスポンス schema マッピング・token refresh テスト
- `tests/test_ebay_repo.py`: snapshot upsert テスト

### Day 5 完了条件

- [ ] eBay API から listing を取得できる
- [ ] raw / snapshot が保存される
- [ ] API エラーで全体が停止しない

---

## Day 6 — eBay seed scanner を完成

**目的**: Day 5 の取得基盤の上に、Yahoo seed 起点で eBay 全域を監視するスキャナを載せる。

### コミット構成

**Commit 1**
```
feat(scanner): implement yahoo-seed-based ebay scanner and hit recording
```
- `scripts/ebay_seed_scanner.py` 実装
  - seed ごとのクエリ発行
  - `ebay_seed_hits` 保存
  - priority に応じて scan cadence を分岐
- `coin_business/ebay/scanner.py`（新規）

**Commit 2**
```
feat(scanner): add incremental scan scheduler with priority cadence
```
- 高優先 seed: 1h / 標準 seed: 2h〜6h
- `scan_history` / `next_run_at` 管理
- `coin_business/scheduler/scan_scheduler.py`（新規）
- `docs/job_schedule.md` 更新

**Commit 3**
```
test(scanner): verify seed-to-hit linkage and duplicate suppression
```
- `tests/test_ebay_seed_scanner.py`
  - 同一 seed 重複ヒット抑止
  - 同一 item 再取得で snapshot 更新のみ
  - seed-hit linkage の整合テスト

### Day 6 完了条件

- [ ] Yahoo seed を使って eBay listing が継続取得される
- [ ] `ebay_seed_hits` が増える
- [ ] raw / snapshot / seed_hits の3層がつながる

---

## Day 7 — 世界オークション収集と T-minus 運用を実装

**目的**: Heritage / Spink / Stack's Bowers / Noble のイベント・lot を先回り収集する線を作る。

**対象 URL**:
- Heritage: https://coins.ha.com/
- Spink: https://www.spink.com/
- Stack's Bowers: https://www.stacksbowers.com/
- Noble: https://www.noble.com.au/

### コミット構成

**Commit 1**
```
feat(global): ingest auction events from heritage spink bowers noble
```
- `scripts/global_auction_sync.py`
  - event 情報の取得（event date / house / title / url）
  - `global_auction_events` への保存
- `coin_business/global_auctions/events.py`（新規）

**Commit 2**
```
feat(global): ingest auction lots and price snapshots with t-minus cadence
```
- `scripts/global_lot_ingest.py`
  - lot 情報取得（estimate / start price / bid 状況）
  - `global_lot_price_snapshots` への保存
  - T-21 / T-7 / T-3 / T-1 の cadence 設定
- `coin_business/global_auctions/lots.py`（新規）

**Commit 3**
```
test(global): add event-lot linkage and t-minus scheduling tests
```
- `tests/test_global_auction_ingest.py`
  - event-lot FK 整合
  - snapshot 保存
  - cadence 更新テスト

### Day 7 完了条件

- [ ] 世界オークション event / lot が DB に入る
- [ ] T-minus 監視が回る
- [ ] eBay と別線で比較対象が増える

---

## Day 8 — match_engine と CAP監査を実装（コア）

**目的**: BOT抽出 → CAP監査 → daily_candidates 昇格制御を実装する。
**ここが曖昧だと今回の方針が崩れる。最重要日。**

### コミット構成

**Commit 1**
```
feat(match): implement level A/B/C classification with extended A rules
```
- `scripts/match_engine.py`
  - Level A 条件（cert完全一致 / 高グレード+利益条件 / 年差±5年+利益条件）
  - Level B = 価格補助のみ（候補化しない）
  - Level C = 除外
- `coin_business/matching/rules.py`（新規）
- `coin_business/matching/scoring.py`（新規）

**Commit 2**
```
feat(audit): require CAP audit before promoting candidates
```
- `scripts/cap_audit_runner.py`
  - AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL
  - BOT抽出結果をそのまま昇格させない
  - AUDIT_PASS のみ `daily_candidates` へ
- `coin_business/audit/runner.py`（新規）
- `coin_business/db/candidate_repo.py` 更新

**Commit 3**
```
test(audit): block daily candidate promotion unless audit_pass
```
- `tests/test_match_engine.py`
- `tests/test_cap_audit_runner.py`
  - 監査未通過で昇格不可
  - AUDIT_HOLD 再監査可能
  - AUDIT_FAIL 履歴保持

### Day 8 完了条件

- [ ] match 結果が `candidate_match_results` に入る
- [ ] CAP 監査結果が保存される
- [ ] AUDIT_PASS 以外は `daily_candidates` に入らない

---

## Day 9 — pricing / KEEP 監視を完成

**目的**: 候補を出すだけでは不十分。「買えるかどうか」を数字で判断し、CEO が KEEP した案件を追い続ける機構を完成させる。

### コミット構成

**Commit 1**
```
feat(pricing): compute target max bid projected profit and bid readiness
```
- `scripts/pricing_engine.py`（既存拡張）
  - `target_max_bid_jpy`
  - `recommended_max_bid_jpy`
  - `projected_profit_jpy`
  - `comparison_quality_score`
  - Level A 判定後の価格計算
- `coin_business/pricing/engine.py`

**Commit 2**
```
feat(watchlist): add keep watch refresher and watchlist snapshots
```
- `candidate_watchlist` / `watchlist_snapshots` 活用
- `scripts/keep_watch_refresher.py`
  - 監視 cadence:
    - 通常: 3h ごと
    - 24h 以内: 1h ごと
    - 6h 以内: 30m ごと
    - 1h 以内: 10m ごと
- `coin_business/watchlist/refresher.py`（新規）

**Commit 3**
```
test(watchlist): verify keep status transitions and alert thresholds
```
- `tests/test_pricing_engine.py`
- `tests/test_keep_watch_refresher.py`
  - 状態遷移: `WATCHING` → `PRICE_OK` → `ENDING_SOON` → `BID_READY`
  - 価格が基準超えたら `PRICE_TOO_HIGH`
  - 終了済みで `ENDED`

### Day 9 完了条件

- [ ] target bid が計算される
- [ ] CEO KEEP 後に自動監視される
- [ ] 価格・時間で状態遷移する

---

## Day 10 — Slack / Notion / Dashboard 統合 + E2E

**目的**: 通知・台帳・画面統合を行い、実運用の1サイクルを通す。
Phase 1〜9 を「動く状態」に持ち込む。

### コミット構成

**Commit 1**
```
feat(notify): add slack brief alerts and bid-ready notifications
```
- `scripts/slack_notifier.py` 実装
- `coin_business/notifications/slack.py`（新規）
- `coin_business/notifications/templates.py`（新規）

Slack 通知文面:

| 種別 | テンプレート |
|------|-------------|
| 朝ブリーフ | `【Morning Brief】本日のA候補 {count} 件 / KEEP監視 {watch_count} 件 / 本日終了 {ending_today} 件` |
| A候補 | `【A Candidate】{title} / 現在価格: {current_price} / 目標上限: {target_max_bid} / 利益見込み: {projected_profit}` |
| 終了間近 | `【Ending Soon】{title} は {time_left} で終了 / 現在価格 {current_price} / 判定 {watch_status}` |
| Bid Ready | `【Bid Ready】{title} / 上限入札 {target_max_bid} / 現在価格 {current_price} / 実行判断をお願いします` |

**Commit 2**
```
feat(notion): sync approved candidates and watchlist state to notion ledger
```
- `scripts/notion_sync.py` 実装
- `coin_business/integrations/notion.py`（新規）
- A候補・watchlist 状態・bid ready を Notion DB へ反映
- 今回は一方向同期（双方向編集は後回し）

**Commit 3**
```
feat(dashboard): add keep monitoring tab and candidate audit visibility
```
- `dashboard.py`: KEEP監視タブ追加
- candidate 一覧に audit status 表示
- KPI 表示: Yahoo承認済み数 / staging 件数 / audit pass 件数 / bid ready 件数
- `coin_business/dashboard/watchlist.py`（新規）
- `coin_business/dashboard/candidates.py`（新規）

**Commit 4**
```
chore(e2e): add end-to-end runbook dry run checklist and go-live gates
```
- `docs/e2e_dry_run_checklist.md`（新規）
- `docs/go_live_gates.md`（新規）

### Day 10 完了条件

- [ ] Yahoo staging → CEO承認 → master → seed → eBay取得 → match → CAP audit → watchlist → Slack/Notion の一周が通る
- [ ] dashboard で CEO / CAP / COO が必要情報を見られる
- [ ] 通知が過剰ノイズにならない

---

## 実装順の意図

この順番にしている理由:

1. **Yahoo 母集団の安全性を固める**（Day 1〜4）
2. **eBay 取得を入れる**（Day 5〜6）
3. **候補抽出と CAP 監査を実装する**（Day 7〜8）
4. **監視・通知を載せる**（Day 9〜10）

この順番なら、万一どこかで止まっても DB 汚染・誤候補大量発生の事故を最小化できる。

**特に重要**: Yahoo staging → CEO承認 → master-only seed を eBay 探索より先に固定すること。
ここを先にやっておかないと、誤った母集団が後工程すべてに伝播する。

---

## CAP向け着工指示文

> `8e228c2` を基準に、Phase 1〜9 を以下の順番で実装してください。
>
> Day 1 はコード実装より先に、status / level / audit / watch cadence を docs と Python 定数に固定し、migration 012〜017 の依存関係を確認すること。
>
> Day 2 で `yahoo_sold_sync.py` を完成させ、Yahoo!履歴は必ず `yahoo_sold_lots_staging` に `PENDING_CEO` で入れること。本DBへはまだ入れないこと。
>
> Day 3 で dashboard に「Yahoo履歴 CEO確認待ち」タブを実装し、承認・却下・保留が `yahoo_sold_lot_reviews` に保存されるようにすること。
>
> Day 4 で `yahoo_promoter.py` と `seed_generator.py` を実装し、承認済み Yahoo データだけを `yahoo_sold_lots` に昇格し、seed は本DBのみから生成すること。
>
> Day 5〜6 で eBay API 正式連携を実装し、`ebay_listings_raw`、`ebay_listing_snapshots`、`ebay_seed_hits` を正しく保存すること。
>
> Day 7 で世界オークション event / lot 収集と T-minus cadence を実装すること。
>
> Day 8 で `match_engine.py` と `cap_audit_runner.py` を実装し、BOT抽出結果は必ず CAP監査を通過したものだけを `daily_candidates` に昇格させること。AUDIT_HOLD と AUDIT_FAIL の履歴も必ず保持すること。
>
> Day 9 で `pricing_engine.py` と `keep_watch_refresher.py` を実装し、CEO KEEP 後の自動監視・価格判定・Bid Ready 判定を動かすこと。
>
> Day 10 で Slack / Notion / dashboard を接続し、Yahoo staging から通知までの E2E dry run を完了させること。
>
> 期間中、BitNow は本流へ入れず、`negotiate_later` に隔離保管すること。
>
> 期間中、Level A 定義は以下を採用すること:
> - cert 完全一致
> - Yahoo基準より高グレードで利益条件達成
> - 年代差 ±5年 で利益条件達成
>
> 期間中、CEO は Yahoo 母集団承認に関与するが、将来は CEO が候補精査から離れ、入札・買付に集中できるように設計すること。ただし BOT抽出 → CAP監査 の二重チェックは維持すること。
