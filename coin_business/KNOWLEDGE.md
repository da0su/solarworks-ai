# coin_business 実装ナレッジ完全版
**最終更新: 2026-03-30** （正式UI決定 + 4カラム追加完了）

---

## 0. このファイルの目的

過去セッションで決まった仕様・設計判断・実装状況を永続保存する。
セッションが変わっても「決まったこと」が消えないよう、ここを Source of Truth とする。

---

## 0-A. 正式UI決定（2026-03-30 CEO確定）

### 正式UI
| 項目 | 内容 |
|---|---|
| **正式ファイル** | `coin_business/web/index.html` |
| **正式URL** | `http://localhost:8502/index.html` |
| **起動コマンド** | `cd coin_business/web && python -m http.server 8502` |
| **Streamlit版** | **正式採用しない**（`dashboard.py` は非採用） |

### 絶対ルール
- **UIテイストを勝手に変えない**
- 変更前に「既存UI維持が前提」を確認する
- 新機能追加より既存UI維持を最優先
- 元UIの見た目・導線・操作感を崩さない

### 維持必須項目
- 管理番号検索 / スラブテキスト検索
- 素材フィルタ（Gold / Silver / Platinum）
- 表裏画像（カード内）
- 既存カラー（濃紺 #1a1a2e / 黄 #ffd700 / 緑 #4ade80）
- 既存カード構成
- 軽い動作

### 表示ルール
| 優先度 | 項目 | DBカラム |
|---|---|---|
| **主表示** | 基準1（2万円）| `ref1_buy_limit_20k_jpy` |
| 補助 | 基準1（15%）| `ref1_buy_limit_15pct_jpy` |
| 補助 | 基準2（2万円）| `ref2_buy_limit_20k_jpy` |
| 補助 | 基準2（15%）| `ref2_buy_limit_15pct_jpy` |

### data.json 更新手順
```bash
cd coin_business
python scripts/update_web_data.py
```
Supabase の `coin_slab_data` から4カラムを取得して `web/data.json` を更新。

---

## 1. 事業の目的・全体像

### ミッション
「仕入判断支援システム」: eBay で仕入れてヤフオクで販売する際の **即座に「この価格以下なら買い」を表示する** システム。

### 対象コイン
- 鑑定会社: **NGC / PCGS のみ**（CAG等は除外）
- 市場: eBay（仕入れ）→ ヤフオク（販売）
- 対象期間: **2025年1月1日以降のヤフオクデータ**（2024年以前は2026-03に削除済み）

---

## 2. 確定済みの価格計算式（CEO承認済み）

### 用語定義
| 用語 | 定義 |
|---|---|
| 地金価値 | 重量(oz) × 純度 × 金属価格(USD) × 為替レート |
| 販売標準価格 | プレミアム販売標準価格 + 地金価値（ヤフオク手数料含まない） |
| プレミアム販売標準価格 | 販売標準価格 - 地金価値（CEOが確定する純粋なコイン市場価格） |
| 仕入相場価格 | eBayで競れる上限価格（USD × 為替） |
| プレミアム仕入相場価格 | 仕入相場価格 - 地金価値 |

### 仕入れ時コスト計算
```
原価 = (仕入相場価格 × 1.10)     ← 関税5.5% × 1.1 概算
     + (eBay送料USD × 為替)
     + 2,000円                   ← US転送サービス
     + 750円                     ← 国内送料・倉庫

※ 仕入相場価格 = eBay商品価格USD × 為替（当日レート）
```

### 販売時手取り
```
販売手取り = 販売標準価格 × 0.90    ← ヤフオク手数料10%
利益 = 販売手取り - 原価
```

### 仕入相場価格の上限算出
```
条件1（最低利益¥20,000確保）:
  原価上限 = 販売手取り - 20,000

条件2（粗利率15%以上 ← CEOが20%→15%に修正済み）:
  原価上限 = 販売手取り × 0.85
  ※「販売手取りの15%を粗利」= 原価は手取りの85%以下

原価上限 = min(条件1, 条件2)
仕入相場価格上限 = (原価上限 - 送料JPY - 2,750) / 1.10
プレミアム仕入相場価格 = 仕入相場価格上限 - 地金価値
```

### calc_ref_values.py の実装式（既存コード）
```python
# 基準1: プレミアム + 地金連動
premium = median(落札価格5パターン) - sold_melt_jpy
sales_standard = premium + current_melt_jpy
net_sales = int(sales_standard * 0.90)          # ヤフオク10%
cost_limit = int(net_sales * 0.85)              # 原価上限85%（利益率15%）
ref1_buy_limit_jpy = int((cost_limit - 2000 - 750) / 1.10)

# 基準2: Yahoo直近価格ベース（簡易版）
ref2_buy_limit_jpy = int((yahoo_recent_price * 0.90 * 0.85 - 2750) / 1.10)
```

---

## 3. 価格決定ロジック（パターン1〜5）

`premium_calculator.py` に実装済み。

| パターン | 条件 | 判定方法 |
|---|---|---|
| 1 | 1円出品 + 入札10件以上 × 1件 | auto_confirmed |
| 2 | 1円出品 + 入札10件未満（他の履歴あり） | auto_confirmed（中央値採用） |
| 3 | 5件以上 | 上下1件カット → 残り中央値 |
| 4 | 4件 | 上下1件カット → 2件中央値 |
| 5-1 | 3件 | 中央値 |
| 5-2 | 2件 | 安い方 |
| 5-3 | 1件 | needs_review（CEO判断） |

**補足:** パターン3〜5の条件は「**入札10件未満**の履歴が○件」（CEO修正済み）

---

## 4. 時間軸価格判断ルール（確定）

```
直近3か月    → 仕入価格判断の主参考値（最重視）
3〜6か月     → 参考値（母数補完）
6〜12か月    → 長期トレンド（主判断根拠にしない）
12か月超     → アーカイブ参考
```

**集計4区分で必ず出す項目:**
- 件数 / 平均価格 / 中央価格 / 最高価格 / 10万超件数 / 30万超件数

`market_stats.py` に `--time` フラグで実装済み。

---

## 5. 同一コイン判定ルール

### DB再構築後の主軸
```
coin_id = grader + "_" + raw_slab_text（完全一致）
```
スラブ表面の文字列が全一致 = 同一コイン。

### 補助: Numista照合
- 年号まで一致させること（年号違いは重量・サイズ違いリスク → 致命的）
- Numista APIは使えないためブラウザ確認
- スラブから素材読み取れる場合（Gold/Silver等）→ 自動確定
- 読み取れない場合 → CEO確認リスト

### 旧T1/T2/T3方式（精度問題あり・非推奨）
| Tier | 条件 |
|---|---|
| T1 | grader + grade + year |
| T2 | + country |
| T3 | + series / denomination |

---

## 6. DB設計と現状

### テーブル状態（2026-03-29時点）

| テーブル | 件数 | 状態 |
|---|---|---|
| market_transactions | 24,961件 | ✅ 2025年1月〜ヤフオク落札履歴 |
| coin_slab_data | 2,927件 | ✅ Phase1処理済み（スラブOCR結果） |
| daily_rates | 件数あり | ✅ 日次FX/金属価格 |
| cost_rules | 9件 | ✅ シード済み |
| coin_master | 0件 | ❌ 未投入（v_cross_market_prices使えない） |
| profit_analysis | 0件 | ❌ 未使用（Airtable版のみ） |
| daily_candidates | 0件 | ❌ パイプライン未接続 |
| sourcing_records | 0件 | ❌ 仕入れ記録なし（まだ） |
| listing_records | 0件 | ❌ 出品記録なし（まだ） |

### coin_slab_data の中身
- `management_no`, `grader`, `slab_line1/2/3`, `grade`, `material`
- `purity`, `weight_g`, `price_history`
- **`ref1_buy_limit_jpy`**: 2,745件に値あり、168件NULL（purity/price_history欠損）
- **`ref2_yahoo_price_jpy`**: 参照値として利用
- `is_ancient`, `metal_value_jpy`, `premium_value_jpy`
- Phase1でスラブ表裏画像のOCRにより生成（Anthropic Vision API使用）

---

## 7. スクリプト一覧と実装状態

### ✅ 実装済み・使用可能

| スクリプト | run.py コマンド | 内容 |
|---|---|---|
| fetch_yahoo_closedsearch.py | `update-yahoo` | Yahoo closedsearch NEXT_DATA JSON自動取得 |
| fetch_ebay_sold.py | `update-ebay` | eBay Completed Listings差分更新 |
| fetch_ebay_terapeak.py | `update-ebay --csv` | CSV手動取込モード |
| market_stats.py | `stats --time` | 4区分時間帯別集計 |
| import_yahoo_history.py | `import-yahoo` | ヤフオクCSV取込（weight/purity抽出付き） |
| supabase_client.py | (内部) | Supabase接続ラッパー |
| fetch_daily_rates.py | (内部) | FX/金属価格取得 |
| calc_ref_values.py | **未登録** | 仕入上限計算（手動実行のみ） |
| premium_calculator.py | (内部) | 価格決定パターン1〜5 |
| ebay_auction_search.py | `ebay-watch`経由 | eBay API検索+judge_opportunity() |
| ebay_api_client.py | (内部) | eBay Browse API クライアント |
| metal_prices.py | (内部) | 地金価値計算 |
| cross_market_analysis.py | `explore`経由 | クロス市場分析（3段階精度） |
| auto_explorer.py | `explore` | eBay vs Yahoo 価格差探索 |
| phase1_processor.py | (内部) | HTML fetch→OCR→Supabase write |
| coin_matcher.py | (内部) | T1/T2/T3マッチング（精度問題残存） |
| purity_weight_judge.py | (内部) | 純度・重量判定 |
| material_judge.py | (内部) | 素材判定（テキストベース） |

### ⚠️ 実装済みだが問題あり

| スクリプト | 問題点 |
|---|---|
| auto_explorer.py | eBay BIN価格パース不具合（¥0返却） |
| phase1_processor.py | Supabase Storage へのfront/back画像アップロード未実装 |
| coin_matcher.py | 精度問題残存（T1/T2では誤マッチ多発） |

### ❌ デッドコード（Supabaseスタックでは使えない）

| スクリプト | 理由 |
|---|---|
| profit_calculator.py | Airtable専用（テーブルIDハードコード） |
| airtable_client.py | Airtable専用 |
| setup_airtable.py | Airtable専用 |

---

## 8. 現在の最重要切断点（優先順）

### 切断点① judgement保存問題（最優先）

```
ebay_auction_search.py
  judge_opportunity() → "OK"/"NG"/"REVIEW"/"CEO判断" を返すが...

  main() の matches.append() に judgment が含まれていない
  ↓
  ebay_matches_latest.json の matches[] に judgment フィールドなし
  ↓
  slack_bridge.py:handle_ebay_search の _KEEP フィルタにも "judgment" なし
  ↓
  CAPの ebay-review 画面に OK/NG 根拠が届かない
```

**修正箇所:**
1. `ebay_auction_search.py` main() → `match["judgment"] = result["judgment"]` を追加
2. `slack_bridge.py` _KEEP に `"judgment", "judgment_reason"` を追加

### 切断点② daily_candidates への流入なし

```
ebay-search → 候補が ebay_review_candidates.json に保存されるが
daily_candidates テーブルには INSERT されない
→ CEOパイプライン終端が機能しない
```

### 切断点③ calc-ref コマンド未登録

```
calc_ref_values.py は run.py に登録されていない
→ 仕入上限の再計算が手動のみ
→ ref1_buy_limit_jpy=NULL が 168件残存
```

---

## 9. Phase1_processor の処理フロー

```
対象: market_transactions (2025-01-01以降, Yahoo落札, NGC/PCGS)

1件処理フロー（process_one）:
  1. Supabase から URL を取得（cursor-based で再開耐性）
  2. Playwright でヤフオクページ fetch
  3. 全画像を収集（スクロール遅延読み込み対応）
  4. スラブ表面画像を Claude Vision で特定
     プロンプト: "Is this a PHOTO of a physical coin grading slab?
                Must be PHOTOGRAPH not website screenshot.
                Label must show BOTH year AND grade. YES or NO."
  5. スラブ表面から coin_id（slab_line1, line2, grader, grade, year）を OCR
  6. coin_slab_data に upsert（100件バッチ）
  7. 処理済みIDを get_processed_ids で管理（再実行スキップ）

画像の優先順位:
  表面: スラブ必須（文字情報がある）
  裏面: スラブあれば最高 → スラブなしコインのみでも可 → 最悪なくてもOK
```

**コスト実績:**
- 通常版: 1件 ≈ $0.0056
- 24,961件全処理 ≈ $140（$200クレジット追加済み）

**Supabase接続制限:**
- 約10,000 API calls で ConnectionTerminated
- 対策: 200件ごとにクライアントを再作成

---

## 10. eBay API 設定状況

| 項目 | 状態 |
|---|---|
| Developer Account | 登録済み |
| EBAY_CLIENT_ID | coin_business/.env に設定 |
| EBAY_CLIENT_SECRET | coin_business/.env に設定 |
| Browse API | 利用可能 |
| 1日上限 | 5,000コール |
| 対応フォーマット | BIN + Auction（AUCTION filter）|

**eBay BIN/Auction対応方針:**
- BIN $300超も積極検討（US転送あり）
- 限定コイン（Cook Islands等）も対象
- 日本未発送もOK（US転送 + UK転送あり）
- 検索: NGC Gold / NGC Silver / PCGS Gold / PCGS Silver の4パターン × 6時間ごと

---

## 11. Numista照合ルール

- **年号まで一致させること**（年号違いは重量・サイズ違いリスク → 致命的）
- URL形式: `https://en.numista.com/{id}`
- APIなし（ブラウザ確認のみ）
- 素材をスラブから読み取れる場合（Gold/Silver等の記載）→ 自動確定
- 読み取れない場合のみ CEO確認リスト

---

## 12. ヤフオクデータ状況

| 期間 | 件数 | 状態 |
|---|---|---|
| 2025-01〜2026-02 | 約14,355+10,606 = 24,961件 | ✅ DB格納済み |
| 2024年分 | 削除済み | CEO指示で削除 |
| 2023年以前 | 削除済み | CEO指示で削除 |

- grader分布: NGC 中心 + PCGS（鑑定なし = 0件）
- 月別カバレッジ: 欠損なし（全月データあり）

---

## 13. eBay検索で確認された利益パターン（参考）

| パターン | 内容 |
|---|---|
| A. 即回転 | PCGS MS70 2016年、NGC PF69UC 1980年等 |
| B. 交渉前提 | Best Offer 案件 |
| C. ニッチ検証 | 明治期日本コイン NGC鑑定品（eBayで安い） |
| D. 見送り | 中国コイン低グレード（薄利） |

**避けるべき条件:**
- 中国コイン低グレード（eBayの方がヤフオクより高い場合あり）
- イタリアコイン（eBayの方が高い）
- 日本コインで日本人セラーが出品しているもの（裁定機会なし）

---

## 14. Slack Bridge との接続状態

| コマンド | Slack handle | 状態 |
|---|---|---|
| ebay-search | `handle_ebay_search` | ✅ Cyberで自動実行 |
| ebay-review | `handle_ebay_review` | ✅ CAPで承認 |
| ceo-report | `handle_ceo_report` | ✅ #ceo-room報告 |

**接続フロー:**
```
Cyber: ebay_auction_search.py 実行（6時間ごと）
  → 候補を ebay_review_candidates.json に保存
  → CAP に TASK(ebay-review) 送信
CAP: 候補確認・承認
  → 承認済みを #ceo-room に報告
  → [未接続] daily_candidates テーブルへの INSERT
```

---

## 15. 今すぐ実装すべき3点（最短復旧パス）

### 優先① judgment フィールド追加（30分）
```python
# ebay_auction_search.py: main() 内 matches.append() 前
result = judge_opportunity(match, conn)
match["judgment"] = result["judgment"]
match["judgment_reason"] = result.get("reason", "")

# slack_bridge.py: handle_ebay_search の _KEEP に追加
_KEEP = {...既存..., "judgment", "judgment_reason"}
```

### 優先② daily_candidates INSERT（1時間）
```python
# handle_ebay_search 末尾に追加
ok_matches = [m for m in matches if m.get("judgment") in ("OK", "REVIEW")]
if ok_matches:
    conn.execute("""
        INSERT INTO daily_candidates (mgmt_no, ebay_url, buy_limit_jpy, judgment, created_at)
        VALUES (:mgmt_no, :ebay_url, :ebay_limit_jpy, :judgment, NOW())
        ON CONFLICT (mgmt_no, ebay_url) DO UPDATE SET updated_at=NOW()
    """, ok_matches)
```

### 優先③ calc-ref コマンド登録（15分）
```python
# coin_business/run.py に追加
def cmd_calc_ref():
    from scripts.calc_ref_values import main as calc_main
    calc_main()

# コマンドマップに追加
"calc-ref": cmd_calc_ref,
```

---

## 16. 今後の設計課題（未定義事項）

| 課題 | 内容 |
|---|---|
| CEO入札上限通知フォーマット | 自動入札BOT向けの通知チャンネル・フォーマット未定 |
| coin_master テーブル投入 | 推奨: coin_slab_data から (line1+line2+grade) でユニーク生成 |
| profit_analysis Supabase版 | sourcing_records が入り始めたら実装 |
| Supabase Storage 画像UP | phase1_processor.py で front/back 画像URLのDB保存は修正済みだが Storage への実アップロードは未実装 |

---

## 17. 運用コマンド早見表

```bash
cd coin_business

# データ更新
python run.py update-yahoo              # ヤフオク差分更新
python run.py update-ebay               # eBay差分更新
python run.py update-ebay --csv <path>  # CSV手動取込

# 分析
python run.py stats --clean --time      # 4区分時間帯別集計
python run.py search --country イギリス --grader NGC
python run.py count                     # 全テーブル件数

# 探索
python run.py ebay-watch                # eBay仕入れ監視
python run.py explore                   # eBay vs Yahoo 価格差探索

# [未登録・手動実行]
python scripts/calc_ref_values.py       # 仕入上限再計算
python scripts/phase1_processor.py     # スラブOCR処理

# Slack Bridge 経由（Cyber）
python slack_bridge.py watch            # 監視常駐
python slack_bridge.py send-task --task ebay-search --to cyber
```

---

## 11. eBay修正記録（再発防止ルール）

### 運用ルール
eBay側で問題が発生した場合は必ず以下4点セットで記録する。

```
■ 問題    [何が起きたか：現象・件数・スクリプト名]
■ 原因    [なぜ起きたか：コード箇所・API仕様変化・データ品質]
■ 修正内容 [何をどう直したか：ファイル名・行番号・変更内容]
■ 再発防止策 [同じ問題が出ないための仕組み]
```

---

### [修正記録 #001] eBay BIN価格「取得不可」 — 2026-03-29

**■ 問題**
`ebay_auction_search.py` 実行時、全19件のeBay出品価格が「取得不可。要ブラウザ確認」となった

**■ 原因**
Browse API で AUCTION フィルタ適用時、入札前アイテムの `price.value` が 0 を返す。
コードが `if api_price_usd:` で 0 はFalsy → 「取得不可」と判定。APIフィールドの未確認。

**■ 修正内容**
未修正（表示問題のみ。判定ロジックへの影響なし）

**■ 再発防止策**
次フェーズで `currentBidPrice` / `startingBidPrice` フィールド名確認と「$0（入札前）」表示対応を実施。
APIレスポンスのフィールド構造を定期確認する。

---

### [修正記録 #002] ヤフオク落札価格の誤認識（コイン違い混入） — 2026-03-29

**■ 問題**
001001（$20 Liberty MS62, 参考価格801,000円）の落札履歴に5,750円・8,252円が混入。
`avg_closed` が引き下げられNGに誤判定。

**■ 原因**
Yahoo検索クエリ「NGC 1904 MS62」が、$20ダブルイーグル以外の低価格コイン（同年別コイン）にもヒット。
eBay側は `strict_match()` でフィルタしているが、Yahoo落札履歴にはフィルタなし。

**■ 修正内容**
`judge_opportunity()` に `ref2_yahoo_price_jpy` の1/10以下の落札値を外れ値除外するフィルタ1行追加。
対象: `ebay_auction_search.py` L644 / コミット `2d26ba6`

**■ 再発防止策**
Yahoo検索クエリの精度強化（コイン名を具体的に）。
今後NG率が100%継続する場合は自動アラートを設ける。
`ref2_yahoo_price_jpy` がNULLのコインへの対処（フォールバック価格）を次フェーズで設計。

---

### [修正記録 #003] Yahoo出品価格の異常値（開始入札1,000円） — 2026-03-29

**■ 問題**
001400（PCGS 2021 MS70 ASE, 仕入上限107,110円）に対し「ライバルが1,000円で出品中」→ 誤NG。
実際の落札相場は28,000〜44,800円。

**■ 原因**
Yahoo出品中検索で「1,000円スタートのオークション開始価格」を `cheapest_active` として取得。
`min()` は単純最小値なので開始入札価格も拾う。

**■ 修正内容**
`cheapest_active` の計算前に `buy_limit_jpy × 0.05` 以下の価格をフロア除外。
対象: `ebay_auction_search.py` L622-625 / コミット `2d26ba6`

**■ 再発防止策**
「入札0件 + 価格が閾値以下」の組み合わせは自動除外ロジックとして拡張予定。
Yahoo取得データの価格分布を ebay-search 実行時にログ出力し、異常値を可視化する。

---

## Git運用ルール（2026-03-30 正式化）

### 基本方針

- **GitHubが正本（Single Source of Truth）**
- サイバーさんPCは GitHub から `git pull` して最新を反映する
- キャップさんは更新時に必ず `commit → push` する
- `gpt_mousiokuri/` は Git管理対象（5ファイル固定）
- 「ローカルだけ最新」は禁止

### 更新フロー（必須）

```bash
git status                        # 変更確認
git add <対象ファイル>              # 必要ファイルのみ（git add -A 多用禁止）
git commit -m "scope: 内容"
git push
```

### commitメッセージ規則

```
feat(coin): add buy-limit calculation
fix(watch): guardian heartbeat detection
docs(runbook): update recovery steps
refactor(bridge): consolidate ACK handling
chore(deps): update requirements
```

### 禁止事項

| 禁止 | 理由 |
|------|------|
| `.env` / APIキーをcommit | 秘密情報漏洩 |
| DBバックアップ本体をpush | 大容量・機密データ |
| ローカルだけ更新して放置 | サイバーと乖離する |
| `gpt_mousiokuri/` 以外にgpt文書を置く | 復旧導線の混乱 |

### gpt_mousiokuri/ — 復旧起点（Git管理・変更時は必ずpush）

```
gpt_mousiokuri/
  gpt_bootstrap.txt           ← 新セッション起動プロンプト
  gpt_handoff_latest.md       ← 全社最新申し送り
  gpt_recovery_runbook.md     ← coin BOT 復旧手順（本ファイル）
  gpt_handoff_db_safety.md    ← DB安全操作ガイド
  gpt_handoff_premium_price1.md ← プレミアム価格計算
```

### サイバーさんへの指示ルール

- サイバーへの指示は必ず **キャップ経由（Slack ai-bridge）** で行う
- 起動前に `git pull` 必須（startup_all.bat に組み込み済み）
- 結果はキャップへ報告 → キャップがCEOへ報告

---

## 18. 紙幣混入インシデント 再発防止策（2026-03-31 CEO指示・恒久対応完了）

### インシデント概要
Spink混合セールから紙幣501件（PMG/Pick 85件 + 管理番号なし416件）がCEO確認待ちリストに混入。
CEOがCEO確認作業中に2件をNGにするまで検出されなかった。

---

### 根本原因（3点）

| # | 場所 | 原因 |
|---|---|---|
| ① | `fetch_noble_noonans_spink.py` | `is_non_coin_lot()`フィルタが未実装。各フェッチ関数が全ロットを無条件返却していた |
| ② | `candidates_writer.py` | `management_no`必須チェックがなく、coin_slab_dataに一致しないロットもCEO確認へ流れていた |
| ③ | `web/index.html` CEO確認クエリ | `ref1_buy_limit_20k_jpy=not.is.null` フィルタが欠落しており、ref1未算出ロットも表示されていた |

---

### 実施した恒久対応

#### レイヤー1: 取得段階フィルタ（`fetch_noble_noonans_spink.py`）

```python
# is_non_coin_lot() — PMG/Pick/banknote/bond等を正規表現で判定
# filter_coin_lots() — AuctionLotリストを coin_lots / excluded_lots に分割
# 各フェッチ関数末尾で filter_coin_lots() を呼び出し → coin_lots のみ返却
```

対象関数:
- `fetch_noble_lots()` → フィルタ後のみ返却
- `fetch_noonans_lots()` → フィルタ後のみ返却
- `fetch_spink_lots()` → フィルタ後のみ返却

#### レイヤー2: パイプライン書込フィルタ（`candidates_writer.py`）

```python
# Step 0-A: is_non_coin_lot() で紙幣除外（重複防御）
# Step 0-C: management_no + ref1_buy_limit_20k_jpy が両方揃った案件のみ candidates 化
#           どちらか欠ける → coin_match_status="unmatched", ceo_skip=True
# Step 0-D: _audit_lots() で監査ログを毎回出力
```

`_audit_lots()` 出力項目:
- 紙幣混入件数
- 管理番号未登録件数
- ref1未算出件数
- 合計対象件数

#### レイヤー3: 表示フィルタ（`web/index.html` CEO確認クエリ）

```javascript
// Supabase REST クエリ条件（恒久）
management_no=not.is.null
&ref1_buy_limit_20k_jpy=not.is.null
&or=(ceo_decision.eq.pending,ceo_decision.is.null)
```

---

### 再発防止チェックリスト

CEO確認ロジックを変更する際は以下を必ず確認すること。

- [ ] `fetch_*_lots()` 関数が `filter_coin_lots()` を呼んで返却しているか
- [ ] `candidates_writer.write_candidates()` のStep 0-A/0-C が有効か
- [ ] CEO確認クエリに `management_no=not.is.null` が含まれているか
- [ ] CEO確認クエリに `ref1_buy_limit_20k_jpy=not.is.null` が含まれているか
- [ ] `_audit_lots()` の出力ログに紙幣混入=0件が確認できるか

---

### 絶対禁止（CEOからの明示指示）

| 禁止事項 | 理由 |
|---|---|
| 管理番号未登録のまま承認可能にする | Yahoo履歴・画像・基準価格が取得できない |
| 選定理由なしでCEO確認に上げる | 判断根拠がなく承認/NGができない |
| 紙幣・債券をCEO確認に表示する | 当社は硬貨・記念メダルのみ扱う |
| 既存相場DBカード・ref1/ref2ロジックを変更する | CEO確定ロジック、変更不可 |
