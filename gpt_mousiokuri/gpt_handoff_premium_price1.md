# プレミアム価格1 算定方法 — 完全抽出レポート

作成日: 2026-03-30
作成者: CAP（キャップさん）
目的: 基準1の仕入れ価格ロジック確定のための現状把握
制約: DBに触れず・ロジック変更なし・推測なし・コード・設定・仕様のみ参照

---

## ① 定義

**プレミアム価格1（変数名: `premium` / DB格納名: `premium_value_jpy`）**

> 「落札価格の中央値（5パターン）から、落札日時点の地金価値を差し引いた額」

```
プレミアム価格1 = 落札価格の中央値（5パターン） − 落札日の地金価値
```

言い換えると **「地金以外の純粋なコイン市場価値」**。
金属相場に依存しない、スラブコインとしてのプレミアム部分のみを切り出した数値。

- 参照ファイル: `scripts/calc_ref_values.py`、`scripts/premium_calculator.py`
- DB格納テーブル: `coin_slab_data`
- DB格納カラム: `premium_value_jpy`（int, JPY）

---

## ② 数式

### Step A: プレミアム価格1の算定

```
median_price  = calc_median_5pattern( 全落札価格リスト )
sold_melt     = 金属レート(JPY/g, 落札日) × weight_g × purity

プレミアム価格1 = median_price − sold_melt
```

### Step B: プレミアム価格1 → 基準1（仕入れ上限）への変換

```
① 販売標準価格   = プレミアム価格1 + current_melt   ← 当日地金価値に差し替え
② 販売手取り     = 販売標準価格 × 0.90             ← ヤフオク手数料 10%
③ 原価上限       = 販売手取り × 0.85               ← 粗利率 15%（手取りの85%）
④ ref1_buy_limit = (原価上限 − 2,000 − 750) / 1.10  ← 転送費・国内送料・関税逆算
```

※ 2,000円 = US転送サービス
※ 750円   = 国内送料・倉庫
※ ÷1.10   = 関税+諸費用 10% の逆算

### KNOWLEDGE.md 記載式（CEO承認済み）

```python
# 基準1: プレミアム + 地金連動
premium = median(落札価格5パターン) - sold_melt_jpy
sales_standard = premium + current_melt_jpy
net_sales = int(sales_standard * 0.90)          # ヤフオク10%
cost_limit = int(net_sales * 0.85)              # 原価上限85%（利益率15%）
ref1_buy_limit_jpy = int((cost_limit - 2000 - 750) / 1.10)
```

---

## ③ 使用変数（完全一覧）

| 変数名 | 型 | 内容 |
|---|---|---|
| `price_history` | list[dict] | ヤフオク落札履歴（price, date, bids, init_price） |
| `prices` | list[int] | price_historyから抽出した落札金額リスト |
| `median_price` | int | 5パターン中央値（calc_median_5pattern()の出力） |
| `sold_date` | str | 直近落札日（price_history降順ソート後の先頭） |
| `sold_rate` | float | 落札日のJPY/g金属レート（daily_ratesテーブルより） |
| `sold_melt` | int | 落札日時点の地金価値 = sold_rate × weight_g × purity |
| `current_rate` | float | 当日のJPY/g金属レート（最新daily_rates） |
| `current_melt` | int | 当日の地金価値 = current_rate × weight_g × purity |
| `premium` | int | **プレミアム価格1** = median_price − sold_melt |
| `sales_standard` | int | 販売標準価格 = premium + current_melt |
| `net_sales` | int | 販売手取り = sales_standard × 0.9 |
| `cost_limit` | int | 原価上限 = net_sales × 0.85 |
| `ref1_buy_limit` | int | **基準1（仕入れ上限JPY）** = (cost_limit − 2750) / 1.1 |
| `premium_value_jpy` | int | DB格納名（coin_slab_data） |
| `metal_value_jpy` | int | DB格納名（当日地金価値、coin_slab_data） |
| `ref1_buy_limit_jpy` | int | DB格納名（基準1仕入れ上限、coin_slab_data） |
| `purity` | float | 純度（例: 0.9167 = 22K） |
| `weight_g` | float | 重量(g)（calc_ref_values.pyでは直接g単位で保持） |
| `material` | str | 素材（'gold'/'silver'/'platinum'） |
| `is_ancient` | bool | 古代コインフラグ（Trueなら地金価値=0で固定） |

---

## ④ 使用ファイル・関数名

| ファイル | 関数 | 役割 |
|---|---|---|
| `scripts/calc_ref_values.py` | `process_row()` | **メイン計算**（全フィールドを一括算定・DB更新） |
| `scripts/calc_ref_values.py` | `calc_median_5pattern()` | 5パターン中央値（L75〜L100） |
| `scripts/calc_ref_values.py` | `metal_rate_per_g()` | 素材→JPY/g変換（gold/silver/platinum） |
| `scripts/calc_ref_values.py` | `get_latest_rates()` | 最新金属レート取得（daily_ratesテーブル） |
| `scripts/calc_ref_values.py` | `get_rate_for_date()` | 指定日の金属レート取得（キャッシュ付き） |
| `scripts/premium_calculator.py` | `determine_pattern()` | パターン1〜5判定 + プレミアム確定 |
| `scripts/premium_calculator.py` | `calc_purchase_price()` | 仕入れ相場価格計算（min条件あり） |
| `scripts/premium_calculator.py` | `calc_melt_value()` | 地金価値算出（oz単位版） |
| `scripts/premium_calculator.py` | `calc_premium()` | プレミアム = 落札価格 − 地金価値 |
| `scripts/ebay_auction_search.py` | （参照のみ） | `ref1_buy_limit_jpy` を `ebay_limit_jpy` として取得 |
| `scripts/ebay_lot_integrator.py` | `ebay_candidate_to_overseas_lot()` | `ebay_limit_jpy` → `buy_limit_jpy` に変換 |
| `coin_business/KNOWLEDGE.md` | — | CEO承認済み計算式の公式記載 |

---

## ⑤ 基準1との関係

```
プレミアム価格1（premium_value_jpy）
    ↓
  ＋ 当日地金価値（current_melt）
    ↓
  = 販売標準価格
    ↓
  × 0.90（ヤフオク手数料）
    ↓
  = 販売手取り
    ↓
  × 0.85（原価上限 粗利15%）
    ↓
  = 原価上限
    ↓
  − 2,750円（転送+国内送料）
  ÷ 1.10（関税・諸費用）
    ↓
  = ref1_buy_limit_jpy ← これが「基準1」
```

**重要:** プレミアム価格1 ≠ 基準1
- プレミアム価格1 = 「コインの純粋な市場価値（JPY）」（中間値）
- 基準1（ref1_buy_limit_jpy） = 「eBayでの仕入れ上限価格（JPY）」（最終出力値）

---

## ⑥ 基準価格2との違い

| 項目 | 基準1（プレミアム+地金連動） | 基準2（Yahoo直近価格ベース） |
|---|---|---|
| **出発点** | 全履歴の中央値（5パターン） − 落札日地金価値 | 直近1件の落札価格のみ |
| **地金連動** | **あり**（当日レートで再計算） | なし |
| **履歴件数** | 全件使用（5パターン統計処理） | 最新1件のみ |
| **変動性** | 地金相場変動で毎日変わる | 落札日から固定 |
| **DB変数名** | `ref1_buy_limit_jpy` | `ref2_yahoo_price_jpy`（落札価格そのもの） |
| **実装式** | `(median_price − sold_melt + current_melt) × 0.9 × 0.85 − 2,750) / 1.1` | `(yahoo_price × 0.9 × 0.85 − 2,750) / 1.1` |
| **目的** | 相場変動に追従する動的上限 | 直近ヤフオク落札値の固定参照 |

### ユーザー補足との照合（基準2の確定式）

```
■ eBay総コスト（CEO確定）:
  総コスト = eBay価格 × 為替 × 1.10 + 2,200

  → コード実装（auction_fee_rules.json: ebayエントリー）:
    (price_usd × fx_rate + 2,000) × 1.10
    = price_usd × fx_rate × 1.10 + 2,200   ← 展開すると一致 ✓

■ 利益条件（2本）:
  条件1: 最低利益 ¥20,000
  条件2: 利益率 15%（ヤフオク手取り基準）
  → premium_calculator.py の cost_limit = min(net−20,000, net×0.85) で実装 ✓
```

---

## ⑦ 具体例（1件）

### 設定値

```
コイン    : Silver American Eagle 1oz NGC MS69
material  : silver
weight_g  : 31.1035g（= 1 troy oz）
purity    : 0.999
落札価格  : ¥15,000（1件のみ）
落札日    : 2026-01-15
落札日の銀価格  : ¥160 / g
当日の銀価格    : ¥168 / g
```

### Step A: プレミアム価格1の算定

```
sold_melt   = 160 × 31.1035 × 0.999 = ¥4,972
median_price = ¥15,000（1件なのでそのまま）

プレミアム価格1 = 15,000 − 4,972 = ¥10,028
```

### Step B: 基準1（仕入れ上限）の算定

```
current_melt   = 168 × 31.1035 × 0.999 = ¥5,221
販売標準価格   = 10,028 + 5,221 = ¥15,249
販売手取り     = 15,249 × 0.90  = ¥13,724
原価上限       = 13,724 × 0.85  = ¥11,665
ref1_buy_limit = (11,665 − 2,750) / 1.10 = ¥8,104

→ このコインのeBay仕入れ上限（基準1）= ¥8,104
```

### 実際のDBデータとの対応

```
daily_candidates.buy_limit_jpy   = 8,104    ← 基準1
daily_candidates.estimated_cost_jpy = (eBay価格USD × 150 + 2,000) × 1.10
daily_candidates.estimated_margin_pct = (buy_limit − cost) / buy_limit
```

---

## 注意点

### 1. 2ファイル間で原価上限の計算式が異なる

| ファイル | 原価上限の計算 |
|---|---|
| `calc_ref_values.py`（DB更新に使われる） | `cost_limit = net_sales × 0.85`（1条件のみ） |
| `premium_calculator.py`（パターン判定） | `cost_limit = min(net − 20,000, net × 0.85)`（2条件のmin） |

**→ 実際にDBの`ref1_buy_limit_jpy`を更新するのは`calc_ref_values.py`（1条件）。**

### 2. プレミアム価格1がマイナスになる場合がある

```
地金価値 > 落札価格 の場合 → premium < 0
例: 地金価値¥5,000 の金貨が ¥4,500で落札 → premium = −500
```

### 3. is_ancient（古代コイン）は地金計算を除外

```python
if is_ancient or current_rate is None:
    current_melt = 0   # 地金価値=0で固定
    sold_melt = 0
```

### 4. 地金価値に使う日付が2種類ある

```
premium算定     : 落札日のレート（sold_melt）← 過去の相場
販売標準価格算定: 当日最新レート（current_melt）← 今日の相場

→ 地金相場が上がると、同じプレミアム価格1でも基準1（仕入れ上限）が上がる設計
```

### 5. ebay_auction_search.py の参照経路

```
coin_slab_data.ref1_buy_limit_jpy
    ↓ db_coin['ref1_buy_limit_jpy']
    ↓ ebay_limit_jpy（ebay_auction_search.py）
    ↓ buy_limit_jpy（ebay_lot_integrator.py）
    ↓ daily_candidates.buy_limit_jpy
    ↓ decide_judgment()（action_notifier.py）で OK/NG 判定
```

---

---

## ⑨ 更新履歴

| 日時 | 内容 |
|------|------|
| 2026-03-30 01:38 | 初版作成 |
| 2026-03-30 10:44 | DB更新実績・異常値注意事項を追記 |

### Phase1-3 DB更新実績（2026-03-30）

```
更新対象: coin_slab_data.ref1_buy_limit_jpy（ほか5カラム）
更新件数: 2,701件（status=completed_hit & purity IS NOT NULL）
NULL率  : 0%（全件値あり）
異常件数: 1件（002713 HAITI G500G：premium=-10,334,113 → ロジック正常）
所要時間: 約3.4分（13.2件/秒）
```

---

*このファイルは改善・変更提案を含まない。現状ロジックの完全把握を目的とした抽出記録。*
