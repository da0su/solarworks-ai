# GPT申し送り - 2026-03-30 01:38 JST

---

## 1. Company Direction

CEO→CAP（代表COO）→cyber 3層自動運用。楽天ROOM自動投稿+コイン仕入れリサーチ。

**事業の目的（固定）:**
「ヤフオクで売れるコインを、海外で安く仕入れること」

- 売値基準: ヤフオク落札
- 仕入れ基準: 海外価格（eBay等）
- 補助: PCGS（主判断禁止）

---

## 2. Progress vs Objective

- 楽天ROOM: health=CRITICAL / pool=0件
- コイン: coin_slab_data=2,927件（ref1更新済2,701件・NULL率0%） / daily_candidates=12件（OK=7, NG=5）
- eBay: 候補19件（未承認19件）/ 最終検索=2026-03-28 17:24
- 定時チェック: 07:30/12:30/18:30 稼働中
- **DB更新: Phase1-3 完了（2026-03-30 10:44）** ← 本日実施済み

---

## 3. Current Issues

- 楽天ROOM health=CRITICAL（要確認）
- Heritage手数料未承認（ceo_confirmed=False）→ 基準1コスト計算なし

---

## 4. Next Priority

- cap/cyber watch 常時起動維持
- daily-check 定時発火監視（schedule_state.json確認）
- ebay-review 承認待ち（19件）
- 4/5 Heritage本番監視開始: `python run.py overseas-watch --source heritage`

---

## 5. Risk / Bottlenecks

- 楽天ROOM health=CRITICAL
- Heritage/Spink/Noble等 手数料CEO未承認（cost計算不可）
- calc_ref_values.py 手動実行のみ（自動化未対応）

---

## 6. Operational Knowledge

- Slack 2500字制限: slim payload のみ送信、詳細は *_latest.json 参照
- 重複発火防止: schedule_state.json の status=done で制御
- セッション引継ぎ: このファイル（daily_handoff.json）を読む
- cyberは git pull 後に watch 再起動で最新コードを読み込む

---

## 7. Behavioral Notes

- CEO: batを押すだけ。日次は #ceo-room を確認。判断事項のみCAPに指示。
- CAP: 代表COO。watch常時起動。daily-handoff で日次申し送り生成。
- cyber: 実処理担当。watch常時起動。git pullで最新コード維持。ebay-search実行役。

---

## 8. Decision Required

- ebay-review 承認待ち（19件）: `python slack_bridge.py approve --task ebay-review --by cap`
- 楽天ROOM health=CRITICAL: `python run.py health` で詳細確認
- Heritage手数料承認: `data/auction_fee_rules.json` → `ceo_confirmed=true` に変更

---

---

# ■ 仕入れ判断ロジック（確定版）

> 作成日: 2026-03-30 / 作成者: CAP
> 目的: 基準1・基準2の完全統一。同じ確認を二度行わないための固定化。

---

## A. 全体前提（最重要・変更禁止）

```
事業目的: ヤフオクで売れるコインを、海外で安く仕入れる

売値基準 : ヤフオク落札価格
仕入基準 : 海外価格（eBay等）
補助情報 : PCGS（主判断には使用しない）
```

**一言運用原則:** 「プレミアムで価値を見る、2万円で意思決定する」

---

## B. プレミアム価格1（定義）

```
プレミアム価格1 = 落札価格の中央値（5パターン） − 落札日時点の地金価値

意味: コイン固有の市場価値（地金を除いた純粋なプレミアム部分）
DB格納: coin_slab_data.premium_value_jpy
Source of Truth: scripts/calc_ref_values.py の process_row()
```

### 5パターン中央値の計算ルール（calc_median_5pattern）

| 件数 | 処理方法 |
|---|---|
| 5件以上 | 上下1件カット → 残りの中央値 |
| 4件 | 上下1件カット → 残り2件の平均 |
| 3件 | 中央値 |
| 2件 | 安い方 |
| 1件 | そのまま |

---

## C. 基準1（プレミアム連動方式）

### 定義

> 地金変動を吸収する動的価格モデル。
> プレミアム価格1を起点に、地金相場・ヤフオク手数料・利益条件を逆算して仕入れ上限を出す。

### 計算構造（全ステップ）

```
Step 1: プレミアム価格1の算定
  premium    = median_price − sold_melt
  sold_melt  = 落札日の金属レート(JPY/g) × weight_g × purity

Step 2: 当日地金価値に差し替えて販売標準価格を復元
  sales_standard = premium + current_melt
  current_melt   = 当日の金属レート(JPY/g) × weight_g × purity

Step 3: 販売手取り（ヤフオク手数料10%）
  net_sales = sales_standard × 0.90

Step 4: 原価上限の算出
  cost_limit = net_sales × 0.85   ← 粗利率15%（手取りの85%が原価上限）

Step 5: eBay仕入れ上限の逆算
  ref1_buy_limit_jpy = (cost_limit − 2,000 − 750) / 1.10
    ※ 2,000円 = US転送サービス
    ※   750円 = 国内送料・倉庫
    ※ ÷1.10  = 関税・諸費用10% の逆算
```

### 現行DB仕様（Source of Truth）

```python
# scripts/calc_ref_values.py の process_row() が採用している式
cost_limit         = int(net_sales * 0.85)
ref1_buy_limit_jpy = int((cost_limit - 2000 - 750) / 1.10)
```

### premium_calculator.py との差異（重要）

```python
# premium_calculator.py の calc_purchase_price() は2条件のmin
cost_limit = min(net_sales - 20_000, int(net_sales * 0.85))

# ← ただし現行DBには未採用
# ← ref1_buy_limit_jpy の更新は calc_ref_values.py が行う
```

**→ DB反映済みの値は常に calc_ref_values.py の式が正しい**

---

## D. 基準2（簡略方式）

### 定義

> 直近ヤフオク落札価格ベース。地金非連動・速度優先の補助判断用。

### 計算式

```
ref2_buy_limit_jpy = (yahoo_price × 0.90 × 0.85 − 2,750) / 1.10

  yahoo_price = coin_slab_data.ref2_yahoo_price_jpy（直近1件の落札価格）
```

---

## E. eBay総コスト式（確定・共通）

```
eBay総コスト(JPY) = (USD価格 × 為替レート + 2,000) × 1.10
                 = USD × 為替 × 1.10 + 2,200

  ※ 2,000円 = US転送サービス（auction_fee_rules.json: transfer_jpy）
  ※ × 1.10  = 関税込み係数（ebay専用暫定値。他社に流用禁止）
  ※ Source of Truth: data/auction_fee_rules.json（source=ebay, ceo_confirmed=true）
```

---

## F. 仕入れ判断の2本ライン（確定）

### 利益条件の分離（最重要）

```
① 主判定（意思決定基準）
   利益2万円以上 → 仕入れOK
   利益 = buy_limit_jpy − estimated_cost_jpy

② 補助表示（CEO確認用・表示のみ）
   利益率15%ライン
   利益率 = (buy_limit − cost) / buy_limit ≥ 0.15
```

**「厳しい方を採用」はしない。** 主判定は2万円ライン。

### 理由

```
前提: 10万円以上の商品のみ仕入れ対象
結論: 利益2万円 = 実質20%利益率（10万円商品時）→ 実務上成立
     15%は安全確認ライン（補助表示）
```

---

## G. DB表示仕様（確定）

### 持つべきフィールド

```
■ 基準1
  ref1_buy_limit_20k_jpy    ← 2万円条件（主判定）
  ref1_buy_limit_15pct_jpy  ← 15%条件（補助表示）

■ 基準2
  ref2_buy_limit_20k_jpy    ← 2万円条件
  ref2_buy_limit_15pct_jpy  ← 15%条件
```

### 表示順

```
1. 基準1（2万円）← 主判定
2. 基準1（15%）  ← 補助
3. 基準2（2万円）
4. 基準2（15%）
```

### 注意: 現行DB状態（2026-03-30 更新後）

```
現在DBに存在・更新済み:
  ref1_buy_limit_jpy   ← 15%ベース・全2,701件更新完了（NULL率0%）
  premium_value_jpy    ← 更新済み
  metal_value_jpy      ← 更新済み
  ref2_yahoo_price_jpy ← 更新済み

未実装（CEO確認後DDLで追加が必要）:
  ref1_buy_limit_20k_jpy    ← 2万円条件
  ref1_buy_limit_15pct_jpy  ← 15%条件（= 現ref1_buy_limit_jpy相当）
  ref2_buy_limit_20k_jpy    ← 基準2・2万円条件
  ref2_buy_limit_15pct_jpy  ← 基準2・15%条件

→ 4カラム追加にはSupabase SQL EditorでDDL実行が必要（CAP・Pythonクライアントでは不可）
```

### DDL（Supabase SQL Editorで実行すること）

```sql
ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref1_buy_limit_20k_jpy   integer;
ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref1_buy_limit_15pct_jpy integer;
ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref2_buy_limit_20k_jpy   integer;
ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref2_buy_limit_15pct_jpy integer;
```

### 追加後の計算式

```python
# 既存DBの値から計算可能（再API取得不要）
net_sales_ref1 = int((premium_value_jpy + metal_value_jpy) * 0.9)
ref1_buy_limit_15pct_jpy = ref1_buy_limit_jpy  # 既存値と同一
ref1_buy_limit_20k_jpy   = int((net_sales_ref1 - 20000 - 2750) / 1.1) if net_sales_ref1 > 22750 else 0

ref2_net = int(ref2_yahoo_price_jpy * 0.9)
ref2_buy_limit_15pct_jpy = int((int(ref2_net * 0.85) - 2750) / 1.1) if int(ref2_net * 0.85) > 2750 else 0
ref2_buy_limit_20k_jpy   = int((ref2_net - 20000 - 2750) / 1.1) if ref2_net > 22750 else 0
```

---

## H. 基準1と基準2の使い分け

| 項目 | 基準1 | 基準2 |
|---|---|---|
| 地金連動 | あり（毎日変動） | なし（固定） |
| 使用履歴 | 全件（統計処理） | 直近1件 |
| 精度 | 高い | 低い（速い） |
| 主/補助 | **主判断** | 補助判断 |
| DB変数 | `ref1_buy_limit_jpy` | `ref2_yahoo_price_jpy`（参考値） |
| 判断優先度 | **基準1 > 基準2** | — |

---

## I. 基準1の未統一事項（CEO判断待ち）

```
A案: 現状維持（15%のみ）
  cost_limit = net_sales × 0.85

B案: 2万円条件も統合（premium_calculator.py の方式に合わせる）
  cost_limit = min(net_sales − 20,000, net_sales × 0.85)

→ どちらにするかはCEO判断
→ 変更する場合は必ず calc_ref_values.py を更新してDB再計算
```

---

## J. 禁止事項（変更禁止）

```
- eBay手数料ロジックを他社に流用しない
- 手数料を勝手に決めない（auction_fee_rules.jsonのみ）
- DBに直接影響する変更を無確認で即実装しない
- 推測でロジックを補完しない
- バックアップなしのDB変更禁止
```

---

## K. ロジック参照経路（データフロー）

```
[ヤフオク落札履歴]
    → price_history（coin_slab_data）
    → calc_ref_values.py / process_row()
        → premium_value_jpy（プレミアム価格1）
        → metal_value_jpy（当日地金価値）
        → ref1_buy_limit_jpy（基準1仕入れ上限）
        → ref2_yahoo_price_jpy（基準2参照価格）

[eBay検索]
    → ebay_auction_search.py
        → ref1_buy_limit_jpy を ebay_limit_jpy として取得
    → ebay_lot_integrator.py
        → buy_limit_jpy として daily_candidates に格納
    → action_notifier.py / decide_judgment()
        → OK / REVIEW / CEO判断 / NG を判定
    → daily_candidates.judgment
```

---
