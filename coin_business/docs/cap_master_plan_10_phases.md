# CAP Master Plan — コイン探索・監視・承認・入札自動化

## 概要

**目的**: Yahoo!落札DB起点の探索エンジンを作り、CEOが「DB確認」ではなく「入札・買付」だけに集中できる運用へ移行する。

**前提**: 既存基盤（dashboard、review queue、pricing、nightly ops、bid queue）は維持しつつ、新探索エンジンを追加する。

---

## 基本方針

### 母集団 = Yahoo!落札履歴DB のみ

```
Yahoo!落札履歴DB
  → 同一/準同一コインを eBay・世界オークションで探す
  → CAP監査
  → 候補化
  → KEEP監視
  → 入札
```

> "売れた実績があるものだけを逆引きする" が基本。eBayで良さそうなコインを無限に探す発想は捨てる。

### 候補レベル定義（Level A のみが実戦対象）

| Level | 定義 | 扱い |
|-------|------|------|
| **A** | cert company + cert number 完全一致 **または** Yahoo!基準より高グレードで利益条件を満たす **または** 年号差±5年以内で利益条件を満たす | **候補化・入札対象** |
| **B** | 価格参考・相場補助に使えるが仕入れ対象外 | 候補化しない。参照のみ。 |
| **C** | 完全無関係 | 除外 |

**CEOが見るのはAだけ。BOTが候補に上げてよいのもAだけ。**

### 固定レーンで探さない

> eBay 新着専用レーン・$1オークション専用レーン等の固定切り方は不採用。
> Yahoo!で売れたもの全体を母集団として、そこからeBay全域を動的に見に行く。

- 新着は補助情報
- $1オークションは有力チャンスとして扱うが最初から固定しない
- BitNowは将来交渉候補として `NEGOTIATE_LATER` に保存

### リサーチ量は減らさない。減らすのは人の目視時間

> 機械が全量を見る。人間はAだけを見る。

---

## eBay・世界オークション・BitNow の位置づけ

### eBay

- **正式API連携を主ルート**（API承認取得済み）
- 入札実行は短期慎重: PlaceOffer 系は自動・予約・sniping的運用に制限あり
- 短期構成:
  1. eBay API で listing 取得
  2. 監視と価格判定は自動
  3. 入札実行は既存の承認運用と接続

### 世界オークション（Heritage / Spink / Stack's Bowers / Noble 等）

- イベント型（週次・月次）
- **T-minus運用**: 公開lot を事前収集し、開催直前だけでなく早期から追う
- T-21 / T-7 / T-3 / T-1 の節目で監視

### BitNow

- **原則除外**（値段が高く、仕入れ向きではない）
- 将来の seller 直接交渉用に `NEGOTIATE_LATER` 箱だけ作る
- 自動交渉送信は未実装

---

## 通知・台帳・判断画面の役割分担

| ツール | 役割 | 使い方 |
|--------|------|--------|
| **Slack** | 通知 | 朝ブリーフ / 終了間近 / KEEP変化 / 注目案件 / 結果報告 |
| **Notion** | 台帳 | スケジュール / 進捗 / 履歴 / 次回確認日時 / イベント管理 |
| **Dashboard** | 最終判断画面 | 候補確認 / Yahoo履歴確認 / KEEP監視 / BID_READY確認 / queue送信 |

---

## 運用モード設計（CEO離脱前提）

| Mode | 説明 |
|------|------|
| **Mode 1: Safe Mode** | Yahoo!履歴はCEO承認。候補はCEO承認。入札は半自動。 |
| **Mode 2: CAP Audit Mode** | Yahoo!履歴はCAP監査で本DB化。CEOは候補判断のみ。低リスクは自動 BID_READY。 |
| **Mode 3: Autonomous Mode** | BOT抽出→CAP監査→自動候補化→自動監視→ルール内は自動入札。CEOは予算・例外・最終買付のみ。 |

### CEO離脱条件（Mode 3 移行基準）

- CAP監査 precision が閾値超え
- Yahoo履歴誤登録が一定期間ゼロ
- watch見逃しゼロ
- bid上限超過ゼロ
- false positive が許容内

---

## 10フェーズ実装計画

### Phase 1 — ルール凍結

**目的**: 散らばった議論をコード化可能なルールに固定する。

**やること**:
- Level A/B/C 定義を確定
- eBay API 承認済み前提に更新
- BitNow は `NEGOTIATE_LATER` に分類
- Yahoo!履歴は最初の10日だけ CEO 承認必須を明文化
- その後は CAP 監査を主にする方針を明文化
- BOT 抽出は必ず CAP 監査を通すことを明文化

**担当**: COO / CAP Backend

**完了条件**: 仕様書・運用モード文書・定数定義が更新されている

**CAP指示**:
> まず業務ルールを凍結してください。Level A/B/C、Yahoo!履歴の10日間CEO承認、eBay API承認済み前提、BitNow除外、BOT抽出のCAP監査必須を、文書とコード定数に反映してください。

---

### Phase 2 — Yahoo!履歴 staging 実装

**目的**: Yahoo!履歴を直接本DBに入れず、安全に受ける。

**テーブル**:
```
yahoo_sold_lots_staging   -- 自動取得受け皿
yahoo_sold_lot_reviews    -- CEO/CAP審査記録
job_yahoo_sold_sync_daily -- 日次同期ジョブ
```

**処理フロー**:
```
Yahoo!履歴自動取得
  → staging に保存 (status=PENDING_CEO)
  → 本DBには入れない
```

**担当**: CAP Backend

**完了条件**: Yahoo!新規履歴が staging に蓄積される。本DBは汚れない。

**CAP指示**:
> Yahoo!同期は本DBへ直接保存せず、必ず staging に入れてください。最初の10日間は PENDING_CEO から先に進めないでください。

---

### Phase 3 — Yahoo!履歴 CEO確認待ちタブ

**目的**: 母集団チェックを Dashboard で回せるようにする。

**やること**:
- `dashboard.py` に「Yahoo!履歴確認待ち」タブ追加
- 一覧・詳細・承認/却下/保留
- レビュー履歴保存

**担当**: CAP UI / CAP Backend

**完了条件**: CEOが staging をレビューできる。approved/rejected/held が保存される。

**CAP指示**:
> 既存の候補レビューとは別に、Yahoo!履歴確認待ちタブを作ってください。ここは候補確認ではなく母集団確認です。承認されたものだけを本DBへ進めてください。

---

### Phase 4 — 承認済みYahoo!履歴の昇格と seed 生成

**目的**: 承認済みの Yahoo!履歴だけを探索母集団にする。

**テーブル/ジョブ**:
```
job_promote_approved_yahoo_lots  -- APPROVED_TO_MAIN のみ昇格
job_seed_generator_daily         -- seed 生成ジョブ
yahoo_coin_seeds                 -- cert exact / title / year+grade 等
```

**担当**: CAP Backend

**完了条件**: 未承認履歴は seed に混ざらない。承認済みだけ探索対象になる。

**CAP指示**:
> seed 生成は `yahoo_sold_lots` だけを入力にしてください。staging データは絶対に使わないでください。

---

### Phase 5 — eBay API正式連携と全域監視

**目的**: Yahoo seed を使って eBay 全域を監視する。

**テーブル/スクリプト**:
```
scripts/ebay_api_ingest.py      -- eBay API listing 取得
scripts/ebay_seed_scanner.py    -- seed ごとに eBay 検索
ebay_listings_raw               -- listing upsert
ebay_listing_snapshots          -- 価格・入札数・残時間の時系列
ebay_seed_hits                  -- seed と listing のマッチ記録
```

**処理**:
- seed ごとに eBay API 検索
- listing を raw に upsert
- 価格・入札数・残時間を snapshot 保存
- 新着に偏らず、進行中オークションも取る

**担当**: CAP Backend / Ops

**完了条件**: eBay listing が継続取得できる。raw / snapshot / seed_hit が埋まる。

**CAP指示**:
> eBay API 承認は取得済みなので、listing 取得は正式 API を主ルートにしてください。Yahoo seed で eBay 全域を監視し、listing の時系列履歴を必ず保存してください。

---

### Phase 6 — 世界オークション event / lot 収集

**目的**: 世界オークションを当日ではなく事前に追う。

**テーブル/スクリプト**:
```
global_auction_events     -- Heritage / Spink / Stack's / Noble イベント台帳
global_auction_lots       -- 公開 lot 事前収集
global_auction_sync.py    -- event 取得スクリプト
global_lot_ingest.py      -- lot 収集スクリプト
```

**処理**:
- Heritage / Spink / Stack's / Noble の event 取得
- 公開 lot を事前収集
- T-21 / T-7 / T-3 / T-1 で監視更新

**担当**: CAP Backend / CAP Automation

**完了条件**: event と lot が台帳化される。事前入札・価格上昇追跡の基礎ができる。

**CAP指示**:
> 世界オークションは公開 lot を早期に取り込み、イベントと lot を分けて管理してください。開催日直前だけの調査ではなく、T-minus 運用前提で作ってください。

---

### Phase 7 — BOT抽出 + CAP監査の二重チェック

**目的**: BOT 単独の誤抽出を防ぎ、将来 CEO 確認を外せる安全装置を作る。

**テーブル/スクリプト**:
```
scripts/match_engine.py         -- Yahoo!基準と eBay/lot の機械照合
scripts/cap_audit_runner.py     -- CAP 監査実行
candidate_match_results         -- 照合・監査ログ
```

**2段チェック構造**:

```
1段目: BOT抽出
  Yahoo!基準 × eBay/auction lot を機械照合
  → 仮 Level A 候補生成

2段目: CAP監査
  cert 妥当性 / タイトル整合 / グレード差
  年数差 / 利益条件 / shipping / lot size
  stale / sold / ended
  → AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL

AUDIT_PASS のみ daily_candidates に昇格
```

**担当**: CAP Backend

**完了条件**: BOT 抽出だけでは候補化されない。監査通過のみ候補化される。監査ログが残る。

**CAP指示**:
> BOT 抽出結果をそのまま候補にしないでください。必ず CAP 監査を別ステップで実行し、AUDIT_PASS だけを `daily_candidates` に入れてください。これは将来の全自動化の前提です。

---

### Phase 8 — pricing / target bid / KEEP監視

**目的**: 候補を実際に仕入れ判断できる状態にする。

**スクリプト/テーブル**:
```
pricing_engine.py 更新       -- Yahoo!基準 expected sale / target max bid
candidate_watchlist          -- CEO KEEP 後の自動監視リスト
keep_watch_refresher.py      -- 監視頻度制御
```

**監視頻度**:
| 残時間 | 監視頻度 |
|--------|---------|
| 通常 | 3時間ごと |
| 24時間以内 | 1時間ごと |
| 6時間以内 | 30分ごと |
| 1時間以内 | 10分ごと |

**担当**: CAP Backend / CAP UI

**完了条件**: 候補に max bid が入る。KEEP した候補が watchlist に入る。状況差分が保存される。

**CAP指示**:
> 監査通過候補には必ず price / profit / max bid を計算し、CEO が KEEP した瞬間から watchlist に入れて自動監視してください。

---

### Phase 9 — Slack / Notion / Dashboard 統合

**目的**: 秘書業務を自動化し、CEO の確認負荷を下げる。

**Slack で送るもの**:
- 朝ブリーフ / 新規 Level A 候補 / KEEP 変化 / 終了間近 / BID_READY / 結果通知

**Notion で持つもの**:
- 台帳 / 状態遷移 / 次回確認日時 / イベント管理

**担当**: CAP Automation / CAP UI

**完了条件**: CEO が Slack + dashboard だけで意思決定できる。Notion に履歴が残る。

**CAP指示**:
> 通知は Slack、台帳は Notion、最終判断は dashboard に分離してください。CEO が細かい探索ログを追わなくても運用できる構造にしてください。

---

### Phase 10 — CEO離脱前提の自動化モード移行

**目的**: 最終的に CEO を「DB確認」から外し、入札・買付だけに集中させる。

**モード設計**: Mode 1 → Mode 2 → Mode 3 の段階移行（上記「運用モード設計」参照）

**担当**: COO / CAP Backend / CAP Automation

**完了条件**: モード切替が設計されている。CEO の作業量が段階的に減る。全自動化に移行可能な構造ができる。

**CAP指示**:
> 最初の10日だけ CEO 承認を残してください。ただし構造は最初から「CEO離脱前提」で設計し、将来的に CAP 監査を主にして全自動へ移行できるモード設計を入れてください。

---

## CAP向け最終指示文（そのまま渡せる版）

> 現在の既存基盤（dashboard、review queue、pricing、nightly ops、bid queue）は維持しつつ、次の主戦略へ移行してください。母集団は Yahoo!落札履歴のみです。Yahoo!履歴は当面は `yahoo_sold_lots_staging` に自動取得し、CEO 承認済みのものだけを `yahoo_sold_lots` に昇格させてください。（DBはリサーチの命であり、ここは慎重に取り扱うというCEOの方針によって、時間の無い中、CEOが当面は自らDBの新規追加に関しては承認制をとるとのことです。）seed 生成は承認済み Yahoo!履歴のみを使ってください。eBay API 承認は取得済みなので、eBay listing 取得は正式 API を主ルートにしてください。世界オークション lot も事前に収集してください。候補化は Level A のみで、A は cert 完全一致だけでなく、Yahoo 基準より高グレードで利益条件を満たす案件、および前後5年以内で利益条件を満たす年号差案件も含みます。BOT 抽出結果は必ず CAP 監査を通し、AUDIT_PASS だけを `daily_candidates` に昇格させてください。CEO が KEEP した候補は watchlist に登録し、通常3時間ごと、24時間以内は1時間ごと、6時間以内は30分ごと、1時間以内は10分ごとに自動監視してください。通知は Slack、履歴台帳は Notion、最終判断 UI は dashboard に集約してください。BitNow は原則除外ですが、将来交渉用に `NEGOTIATE_LATER` 箱へ保存可能にしてください。最終目標は、CEO が母集団確認や候補精査から離れ、入札・買付だけに集中できる全自動運用です。ただし、その全自動化は必ず BOT 抽出 + CAP 監査の二重チェック前提で実装してください。

---

## COOとしての最終結論

> 「Yahoo!で売れた実績を母集団にし、eBay と世界オークションを常時監視し、BOT 抽出を CAP が監査し、CEO は最終的に入札・買付だけを行う仕組みに変える」

この順序で進めることで:
- 母集団が汚れない
- 候補の質が保たれる
- CEO の手作業が減る
- 将来の全自動化にも繋がる
