# コイン事業 GPT 復旧ランブック

> **目的**: このファイルだけで、セッションを失っても即座に復旧できる状態にする。
> **対象**: GPT/Claude等のAIエージェントが初回セッションで読む前提で記述。
> **更新**: 2026-03-30

---

## 0. セッション開始時に最初に読むファイル順

> AIエージェントがセッションを開始したら、**必ずこの順番でファイルを読むこと**。
> 読まずに作業を始めてはならない。

| 順序 | ファイル | 目的 |
|---|---|---|
| 1 | `coin_business/gpt_recovery_runbook.md` | **本ファイル**。全体像・復旧手順・停止条件を把握 |
| 2 | `coin_business/KNOWLEDGE.md` | 実装状況の完全版（スクリプト一覧・DB状態・コマンド早見表） |
| 3 | `coin_business/data/auction_fee_rules.json` | CEO承認済み手数料ルール（計算前に必ず確認） |
| 4 | `coin_business/data/auction_schedule.json` | 直近のオークション開催状況（監視対象を把握） |
| 5 | `solarworks-ai/CLAUDE.md` | リポジトリ全体の設計方針・コマンド一覧 |

**読んだら必ず確認すること:**
```bash
python run.py count          # DB接続OK + テーブル件数が正常範囲か
python run.py overseas-watch --candidates  # daily_candidates の現在状態
```

---

## 1. このプロジェクトの目的

### 事業概要

- **ヤフオクで高値で売れるコインを海外から安く仕入れ、差益を得る**
- 仕入れ先: eBay（常時）、Heritage / Stack's Bowers / Noonans / Spink 等（オークション開催時）
- 判断基準: **2万円利益確保** または **粗利15%以上**

### 3台体制

| 機 | 役割 |
|---|---|
| **サイバーさん（Desktop B）** | 本番運用・Source of Truth |
| **キャップさん（ノート型）** | 開発・セッション作業 |
| **レッツさん（Desktop A）** | 旧本番（参照用） |

### 利益計算の基本式（CEO確定）

```
売値 = ヤフオク落札価格
ヤフオク手数料 = 売値 × 10%
国内送料 = ¥750

net_sell = 売値 × 0.9 - ¥750

仕入れコスト総額 = hammer_price × (1 + buyer_premium)
                    × fx_rate × (1 + fx_buffer)
                    + 海外→日本転送費 + 保険
                    （関税 0% — 金貨・銀貨とも、CEO確定 2026-03-30）

利益基準1（2万円）: net_sell - コスト ≥ ¥20,000
利益基準2（15%）:   (net_sell - コスト) / net_sell ≥ 15%
```

---

## 2. 環境構築

### 2-1. リポジトリ移動

```bash
cd C:\Users\砂田　紘幸\solarworks-ai\coin_business
```

### 2-2. 依存パッケージインストール（requirements.txtなし→手動）

```bash
pip install supabase python-dotenv requests httpx \
            beautifulsoup4 lxml openpyxl pandas playwright
```

現在の動作確認済みバージョン:
- supabase 2.28.2
- requests 2.32.5
- httpx 0.28.1
- python-dotenv 1.2.2
- pandas 2.3.3

### 2-3. ディレクトリ構成（重要ファイルのみ）

```
coin_business/
├── run.py                          ← CLIエントリーポイント（全コマンドここから）
├── config.py                       ← 設定定数
├── .env                            ← 接続情報（Git管理外）
├── .env.example                    ← テンプレート
├── gpt_recovery_runbook.md         ← 本ファイル
├── scripts/
│   ├── supabase_client.py          ← DB接続（SUPABASE_URL/KEY読み込み）
│   ├── calc_ref_values.py          ← 基準1/基準2 一括再計算
│   ├── update_web_data.py          ← web/data.json 更新
│   ├── candidates_writer.py        ← daily_candidates 書き込み
│   ├── action_notifier.py          ← 判定ロジック + Slack通知
│   ├── heritage_fetcher.py         ← Heritage ロット取得
│   ├── auction_cost_calculator.py  ← コスト計算（fee_rules.json読み込み）
│   └── ebay_lot_integrator.py      ← eBay候補 → daily_candidates
├── data/
│   ├── auction_fee_rules.json      ← 手数料ルール（CEO承認済み）
│   ├── auction_schedule.json       ← オークション年間スケジュール
│   └── backups/                    ← DBバックアップ（JSON形式）
└── web/
    ├── index.html                  ← 公式UI（モバイル対応）
    └── data.json                   ← UIデータソース（2760件）
```

---

## 3. DB接続確認

### 3-1. .env 設定

```bash
# coin_business/.env に以下を設定
SUPABASE_URL=https://xxxxxxxxxxxxxxxx.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...  # service_role_key
```

> ⚠️ `.env.example` には Airtable の設定しか書かれていない（旧版）。
> SUPABASE_URL / SUPABASE_KEY は実際の `.env` から確認すること。

### 3-2. 接続テスト

```bash
cd coin_business
python run.py count
```

**期待出力（正常）:**

```
テーブル件数:
  coin_slab_data      : 2,927件
  market_transactions : 24,961件
  daily_candidates    : 12件以上
  daily_rates         : 812件
  cost_rules          : 9件
```

### 3-3. 接続エラー時

```
エラー: SUPABASE_URL / SUPABASE_KEY が .env に設定されていません
```

→ `.env` ファイルの存在と内容を確認。`coin_business/.env` に直接記載すること。

---

## 4. バックアップ復元

### 4-1. バックアップの場所

```
coin_business/data/backups/
├── db_backup_summary_YYYYMMDD_HHMM.json        ← テーブル件数サマリー
├── db_backup_coin_slab_data_YYYYMMDD_HHMM.json ← コイン管理DB（~5.7MB）
├── db_backup_daily_candidates_YYYYMMDD_HHMM.json
├── db_backup_daily_rates_YYYYMMDD_HHMM.json
└── db_backup_cost_rules_YYYYMMDD_HHMM.json
```

最新バックアップ（2026-03-30時点）:
- `db_backup_coin_slab_data_20260330_1128.json` — coin_slab_data 2,927件

### 4-2. coin_slab_data 復元コマンド

```bash
cd coin_business
python - << 'EOF'
import json, sys, os
sys.path.insert(0, '.')
from scripts.supabase_client import get_client

BACKUP_FILE = "data/backups/db_backup_coin_slab_data_20260330_1128.json"
TABLE = "coin_slab_data"
BATCH = 50

with open(BACKUP_FILE, encoding="utf-8") as f:
    records = json.load(f)

client = get_client()
ok = 0
for i in range(0, len(records), BATCH):
    batch = records[i:i+BATCH]
    client.table(TABLE).upsert(batch, on_conflict="management_no").execute()
    ok += len(batch)
    print(f"  upsert: {ok}/{len(records)}")
print(f"完了: {ok}件")
EOF
```

### 4-3. daily_candidates 復元（必要な場合のみ）

```bash
python - << 'EOF'
import json, sys
sys.path.insert(0, '.')
from scripts.supabase_client import get_client

BACKUP_FILE = "data/backups/db_backup_daily_candidates_20260330_0138.json"
TABLE = "daily_candidates"

with open(BACKUP_FILE, encoding="utf-8") as f:
    records = json.load(f)

client = get_client()
for i in range(0, len(records), 50):
    client.table(TABLE).upsert(records[i:i+50], on_conflict="dedup_key").execute()
print(f"完了: {len(records)}件")
EOF
```

### 4-4. Supabase SQL Editor での直接確認

ブラウザで `https://app.supabase.com` → プロジェクト → Table Editor で目視確認可能。

---

## 5. 計算再構築（基準1/基準2）

### 5-1. 概要

- `coin_slab_data` の `ref1_buy_limit_20k_jpy` / `ref1_buy_limit_15pct_jpy` / `ref2_buy_limit_20k_jpy` / `ref2_buy_limit_15pct_jpy` を再計算する
- 基準1 = プレミアム価格 + 地金連動方式
- 基準2 = 直近ヤフオク落札価格ベース

### 5-2. 実行

```bash
cd coin_business
python scripts/calc_ref_values.py
```

**所要時間**: ~5〜10分（2,927件対象）

**完了確認:**

```bash
python run.py count
# coin_slab_data の件数が変わらず、ref値が入ったことを確認
```

### 5-3. web/data.json の更新（UI反映）

```bash
python scripts/update_web_data.py
# → web/data.json が更新される（2760件、~5MB）
```

---

## 6. eBay実行

### 6-1. eBay仕入れ候補探索

```bash
cd coin_business
python run.py ebay-search
```

- `~/.slack_bridge/ebay_review_candidates.json` に候補を出力
- 判定（OK/NG/REVIEW）は **ebay-integrate** で実施

### 6-2. eBay候補を daily_candidates に統合

```bash
python run.py ebay-integrate
```

- `~/.slack_bridge/ebay_review_candidates.json` を読み込み
- `candidates_writer.py` → 判定 → Supabase upsert → Slack通知
- `coin_slab_data` から4カラム（ref値）を自動付与

**確認:**

```bash
python run.py overseas-watch --candidates
# daily_candidates の現在件数・判定分布を表示
```

---

## 7. 海外オークション監視

### 7-1. Heritage（4/5 HK Spring 開催）

```bash
cd coin_business

# dry-run（確認のみ、DB書き込みなし）
python run.py overseas-watch --source heritage --dry-run

# 本番実行
python run.py overseas-watch --source heritage
```

### 7-2. スケジュール確認

```bash
python run.py overseas-watch --schedule
# auction_schedule.json の開催状況を一覧表示
```

### 7-3. 全ソース監視

```bash
python run.py overseas-watch
# heritage + numisbids 対応ソースを自動選択
```

### 7-4. 手数料ルール（CEO確定 2026-03-30）

| オークション | Buyer's Premium | 通貨 | 関税 |
|---|---|---|---|
| Heritage | 22% | USD | 0% |
| Stack's Bowers | 22% | USD | 0% |
| Noble | 22% | AUD | 0% |
| Noonans | 24% | GBP | 0% |
| Spink London | 22.5% | GBP | 0% |
| Spink HK | 20% | HKD | 0% |
| SINCONA | 20% | CHF | 0% |

設定ファイル: `data/auction_fee_rules.json`（全社 `ceo_confirmed: true`）

---

## 8. UI起動

### 8-1. 公式UI（モバイル対応）

```bash
cd C:\Users\砂田　紘幸\solarworks-ai\coin_business\web
python -m http.server 8502
```

ブラウザで: `http://localhost:8502/index.html`

### 8-2. UIが古い場合（data.json を更新）

```bash
cd C:\Users\砂田　紘幸\solarworks-ai\coin_business
python scripts/update_web_data.py
# その後ブラウザをリロード
```

### 8-3. UI仕様

- 初期表示: 100件（もっと見るで+100件ずつ）
- 検索: 管理番号・コイン名・グレード対応
- 中央表示: `ref1_buy_limit_20k_jpy`（基準1・2万円）
- 価格フィルタ・素材フィルタ・グレーダーフィルタ搭載

---

## 9. 正常確認

### 9-1. daily_candidates 件数・OK件数

```bash
cd coin_business
python run.py overseas-watch --candidates
```

**期待出力例:**

```
=== daily_candidates (12件) ===
判定別:
  OK       : 7件
  NG       : 5件
ステータス別:
  pending  : 12件
```

### 9-2. 4カラム充填率確認

```bash
python scripts/check_status.py
# ref1_buy_limit_20k_jpy の NULL件数を表示
```

### 9-3. データ整合性確認（フルチェック）

```bash
python run.py count          # テーブル件数
python run.py stats --clean  # 市場統計（直近3か月重視）
python scripts/check_data_json.py  # web/data.json の内容確認
```

### 9-4. 正常状態の定義

| チェック項目 | 正常値 |
|---|---|
| coin_slab_data | 2,927件 |
| market_transactions | 24,961件以上 |
| 4カラム充填率 | 97%以上 |
| price=0 の件数 | 0件 |
| UI表示 | 2,760件、100件初期表示 |

---

## 10. 異常時の停止条件

> **以下の条件に該当した場合、AIは即座に作業を停止してCEOまたはキャップに報告すること。**
> 自己判断で続行・修正・削除を行ってはならない。

### 10-0. 停止条件一覧

| # | 条件 | 停止理由 |
|---|---|---|
| A | `coin_slab_data` の件数が **2,900件を下回る** | データ欠損の可能性。復元前にCEO確認必要 |
| B | `market_transactions` の件数が **24,000件を下回る** | ヤフオク取引履歴の大量消失。原因不明の場合は停止 |
| C | `ceo_confirmed: false` のルールで仕入れ計算を実行しようとしている | 未承認ルールによる誤判断を防ぐ |
| D | **DBへの DELETE / DROP / TRUNCATE** が必要な操作 | 復元不能な破壊的操作。必ずCEO口頭承認を得てから実行 |
| E | `auction_fee_rules.json` の `buyer_premium_pct` を変更しようとしている | 全判定結果に波及する。CEO承認なしに変更禁止 |
| F | OK判定案件の **eBay即決・Heritage入札操作** | AIによる実購買操作は禁止。CEOが人間として実行する |
| G | Slack への **CEO宛メッセージの代理送信** | 判断内容の偽装リスク。AIは通知のみ、意思決定の代行不可 |
| H | `.env` ファイルの読み出し・内容の外部出力 | 認証情報の漏洩防止 |
| I | エラーが **3回連続** で同じ箇所で発生 | ループしている可能性。修正せず状況を報告して停止 |
| J | 予期しないテーブルへの **upsert/insert** | スキーマ外への書き込みは停止して確認 |

### 停止時の報告フォーマット

```
【停止報告】
停止条件: [上記 A〜J の番号と内容]
発生箇所: [スクリプト名 / コマンド名]
状況    : [何をしようとしていたか]
現在状態: [DBの件数など確認済みの状態]
推奨対応: [復元が必要か / 確認だけでよいか]
```

---

## 11. トラブル時

### 10-1. DB ロールバック（coin_slab_data 破損時）

```bash
# 最新バックアップを確認
ls coin_business/data/backups/ | sort

# 復元（セクション4-2 の手順を実行）
python - << 'EOF'
...（セクション4-2のコードを貼る）
EOF
```

### 10-2. JSON復元（auction_fee_rules.json が壊れた場合）

```bash
# Git から復元
git checkout coin_business/data/auction_fee_rules.json

# または data/backups/ に入っている場合はそこから
# ない場合はキャップさんに連絡してCEO承認済みの値を再設定
```

**auction_fee_rules.json の正常状態:**
全社 `"ceo_confirmed": true`、Heritage/Stack's 22%、Noonans 24%、Spink 22.5%、Noble/SINCONA 20%

### 10-3. daily_candidates にデータが入らない

確認順序:
1. `python run.py count` → DB接続確認
2. `.env` の `SUPABASE_URL` / `SUPABASE_KEY` を確認
3. `python run.py overseas-watch --source heritage --dry-run` → ロット取得確認
4. auction_schedule.json でオークションが `active` / `imminent` になっているか確認

### 10-4. UIが真っ白・件数0

```bash
# data.json が存在するか確認
ls coin_business/web/data.json

# なければ再生成
python scripts/update_web_data.py

# ファイルが壊れている場合
# → calc_ref_values.py → update_web_data.py の順で再実行
```

### 10-5. Slack通知が届かない

```bash
# .env に SLACK_BOT_TOKEN があるか確認
# coin_business/scripts/action_notifier.py のトークンを確認
grep -n "SLACK_BOT_TOKEN" coin_business/scripts/action_notifier.py
```

---

## クイックリファレンス

```bash
# ── 状態確認 ──────────────────────────────────────────
python run.py count                              # テーブル件数
python run.py overseas-watch --candidates        # daily_candidates状況
python run.py overseas-watch --schedule          # オークションスケジュール
python run.py overseas-watch --ceo-list          # CEO判断リスト

# ── データ更新 ────────────────────────────────────────
python run.py update-yahoo                       # ヤフオク差分更新
python run.py update-ebay                        # eBay落札差分更新
python scripts/calc_ref_values.py                # 基準1/2 再計算
python scripts/update_web_data.py                # UI用data.json 更新

# ── 仕入れ監視 ────────────────────────────────────────
python run.py ebay-search                        # eBay仕入れ候補探索
python run.py ebay-integrate                     # eBay → daily_candidates
python run.py overseas-watch --source heritage   # Heritage 本番監視
python run.py overseas-watch --dry-run           # 全ソース dry-run

# ── UI ────────────────────────────────────────────────
cd web && python -m http.server 8502             # http://localhost:8502/index.html
```

---

## サイバーさん共有記録

> サイバーさん（Desktop B）がセッションを引き継ぐ際に参照する記録。
> キャップさんが作業を完了したタイミングで更新する。

### 現在の状態（2026-03-30 更新）

| 項目 | 状態 | 詳細 |
|---|---|---|
| DB（coin_slab_data） | ✅ 正常 | 2,927件 / 4カラム充填率97.9% |
| DB（market_transactions） | ✅ 正常 | 24,961件 |
| DB（daily_candidates） | ✅ 正常 | OK=7件 / NG=5件 |
| auction_fee_rules.json | ✅ CEO承認済み | 全社 ceo_confirmed:true / v1.2 |
| auction_schedule.json | ✅ 正常 | Heritage HK Spring 4/5-6 登録済み |
| web/index.html | ✅ 正常 | 100件ページネーション / 管理番号検索対応 |
| web/data.json | ✅ 正常 | 2,760件 / price=0件ゼロ |
| Heritage dry-run | ✅ 完了 | 25件取得確認済み（3/30実施） |
| Heritage 本番実行 | ⏳ 未実施 | **4/5に実行すること** |

### サイバーさんへの引き継ぎ指示

```
4/5（日）に以下を実行してください：

1. Heritage 本番実行
   cd coin_business
   python run.py overseas-watch --source heritage

2. 結果を以下の形式でキャップ（または #ceo-room）に報告：
   - 取得件数
   - OK / REVIEW / NG / CEO判断 の件数
   - TOP3案件（管理番号・コイン名・推定コスト・買い上限・利益率）
   - エラーの有無

3. OK案件があれば CEO に Slack 通知が飛びます（自動）。
   追加で人間が確認する必要がある場合のみ手動報告。
```

### 作業ログ（キャップ → サイバー 引き継ぎ履歴）

| 日付 | 作業内容 | 担当 | 完了 |
|---|---|---|---|
| 2026-03-29 | eBay-integrate 本番実行（4カラム反映）| キャップ | ✅ |
| 2026-03-29 | daily_candidates DDL 4カラム追加（Supabase SQL Editor）| キャップ | ✅ |
| 2026-03-29 | web/index.html 100件ページネーション実装 | キャップ | ✅ |
| 2026-03-30 | auction_fee_rules.json CEO承認（全社確定）| キャップ | ✅ |
| 2026-03-30 | Heritage dry-run 25件取得確認 | キャップ | ✅ |
| 2026-03-30 | gpt_recovery_runbook.md 作成 | キャップ | ✅ |
| 2026-04-05 | Heritage HK Spring 本番監視 | **サイバー** | ⏳ |

---

## 引き継ぎ事項（2026-03-30時点）

| 項目 | 状態 | 次アクション |
|---|---|---|
| coin_slab_data 4カラム | ✅ 全件充填済み（97.9%）| 定期確認のみ |
| daily_candidates 4カラム | ✅ OK案件7件に反映済み | heritage実行後確認 |
| Heritage 手数料 | ✅ CEO承認（22%）| — |
| Heritage dry-run | ✅ 25件取得確認 | 4/5本番実行 |
| UI軽量化 | ✅ 100件ページネーション | — |
| ebay-search→integrate 接続 | ⚠️ ファイルパス断点あり | 将来対応 |
| Heritage 4/5 本番 | ⏳ 未実施 | **4/5に実行** |
