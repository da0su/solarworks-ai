# テーブル承認是正案 — 三分類一覧
作成日: 2026-04-02  作成者: Cap/COO

---

## 凡例
| 区分 | 意味 |
|---|---|
| **継続利用候補** | データあり・業務上不可欠。事後追認申請で正式化を推奨 |
| **追認申請候補** | データなし・設計上必要。CEO承認取得後に継続 |
| **廃止候補** | データなし・代替あり・今後使用予定不明。削除推奨 |

---

## カテゴリ 0：コアDB（変更なし・承認不要）

| テーブル名 | 行数 | 用途 | 分類 |
|---|---|---|---|
| `market_transactions` | 24,961 | 落札生データ（Yahoo/eBay） | ✅ 正式 |
| `coin_slab_data` | 2,927 | スラブマスタ・参照基準 | ✅ 正式 |
| `daily_candidates` | 518 | 仕入れ候補管理 | ✅ 正式 |
| `daily_rates` | 812 | 為替レート | ✅ 正式 |
| `cost_rules` | 9 | 利益計算ルール | ✅ 正式 |
| `candidate_evidence` | 4,644 | 候補根拠データ（稼働中） | ✅ 正式 |
| `candidate_pricing_snapshots` | 1,104 | 候補価格スナップショット（稼働中） | ✅ 正式 |

---

## カテゴリ 1：継続利用候補（事後追認申請対象）

| テーブル名 | 行数 | Migration | 用途 | 是正アクション |
|---|---|---|---|---|
| `yahoo_sold_lots_staging` | 6,349 | 012 | Yahoo落札履歴受け皿。P1の中核 | **追認申請** |
| `yahoo_sold_lot_reviews` | 0 | 012 | staging審査記録（監査証跡） | **追認申請** |
| `job_yahoo_sold_sync_daily` | 1 | 012 | Yahoo同期ジョブ管理 | **追認申請** |
| `bid_history` | 1 | 008_ceo | 入札追跡（2026-03-30 1件） | **追認申請** |

**追認申請理由**: 設計上の必要性が高く、既にデータが存在するため廃止コストが発生する。
承認が下りれば継続、下りなければ廃止処理を行う。

---

## カテゴリ 2：追認申請候補（概念・設計の承認が必要）

| テーブル名 | 行数 | Migration | 用途 | 是正アクション |
|---|---|---|---|---|
| `yahoo_sold_lots` | 0 | 019 | staging昇格後の本格格納先（"main"） | **概念承認申請** → 承認後に継続 |
| `candidate_match_results` | 0 | 016 | eBay×Yahoo照合結果 | **概念承認申請** → 不要なら廃止 |
| `notification_log` | 0 | 017 | システム通知ログ | **概念承認申請** → 不要なら廃止 |

**注意**: `yahoo_sold_lots` は停止命令対象の「昇格先」。承認なしに使用継続不可。

---

## カテゴリ 3：廃止候補（削除推奨）

| テーブル名 | 行数 | Migration | 廃止理由 |
|---|---|---|---|
| `yahoo_coin_seeds` | 0 | 013 | seedアプローチ未承認、0件、代替設計検討中 |
| `job_yahoo_promoter_daily` | 0 | 019 | 昇格処理停止命令中、再開見込み未定 |
| `job_seed_generator_daily` | 0 | 019 | seed生成未承認、0件 |
| `shadow_run_reports` | 0 | 008_day13 | shadow run完了、役割終了 |
| `shadow_run_items` | 0 | 008_day13 | 同上 |
| `coin_master` | 0 | 001 | 未使用、coin_slab_dataが実質的に代替 |
| `sellers` | 0 | 001 | 未使用 |
| `sourcing_records` | 0 | 001 | 未使用、市場データ収集はmarket_transactionsに統合 |
| `listing_records` | 0 | 001 | 未使用 |
| `profit_analysis` | 0 | 001 | 未使用、利益計算はcoin_slab_data+cost_rulesで実施 |
| `inventory` | 0 | 001 | 未使用 |
| `inventory_snapshots` | 0 | 001 | 未使用 |
| `exchange_rates` | 0 | 001 | 未使用、daily_ratesが正式 |
| `candidate_decisions` | 0 | 001_day1 | 未使用 |
| `candidate_status_checks` | 0 | 002_day1 | 未使用 |
| `bidding_records` | 0 | 002_day1 | 未使用、bid_historyが正式 |

**廃止実施条件**: CEO承認後にDROP TABLE（データなしのため影響なし）

---

## Migrationファイルのみ・DB未適用（放置リスクなし）

以下はSQLファイルのみ存在し、Supabaseには適用されていない。
即時リスクなし。設計ファイルとして保留または削除検討。

| Migration | 対象テーブル | 対応 |
|---|---|---|
| 014 | ebay_listings_raw, ebay_listing_snapshots, ebay_seed_hits | ファイル保留 |
| 015 | global_auction_events, global_auction_lots 他 | ファイル保留 |
| 016（一部） | candidate_watchlist, watchlist_snapshots | ファイル保留 |
| 017（一部） | negotiate_later | ファイル保留 |
| 020-025 | job系テーブル群（8種） | ファイル保留 |
