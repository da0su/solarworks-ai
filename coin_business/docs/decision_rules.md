# 仕入れ判断ルール定義書

**固定日**: Day 1 (基準コミット: 8e228c2)
**変更権限**: COO / CEO のみ。CAP は実装時にこの文書に従う。

---

## 0. 絶対ルール（変更不可）

| # | ルール | 根拠 |
|---|--------|------|
| 0-1 | 母集団は **Yahoo!落札履歴DB のみ** | DBはリサーチの命。汚染防止最優先 |
| 0-2 | Yahoo!履歴は **当面10日間 CEO承認済みのみ本DB昇格** | 初期品質保証 |
| 0-3 | BOT抽出結果は **必ず CAP監査を通す** | 誤抽出の防波堤 |
| 0-4 | **AUDIT_PASS のみ** daily_candidates に昇格 | AUDIT_HOLD/FAIL は昇格しない |
| 0-5 | BitNow は **本流に入れない** | negotiate_later に退避のみ |
| 0-6 | eBay は **正式 API 主ルート**（OAuth 承認取得済み） | — |
| 0-7 | 世界オークションは **T-minus 運用** | T-21/T-7/T-3/T-1 |
| 0-8 | 通知=Slack / 台帳=Notion / 判断UI=Dashboard | 役割を分離 |

---

## 1. 候補レベル定義（Level A / B / C）

### Level A — 仕入れ対象

**CEOが見るのはAだけ。BOTが候補に上げてよいのもAだけ。**

以下の **3条件のいずれかを満たす** こと:

#### A-1: cert 完全一致
```
cert_company（NGC / PCGS）が一致
AND cert_number が完全一致
```
> 同一個体であることが最も確実な証拠。これだけで Level A。

#### A-2: Yahoo!基準より高グレードで利益条件を満たす
```
source の grade > Yahoo!落札時の grade
AND projected_profit_jpy > 0
AND projected_roi > 0%
```
> より良いコンディションが市場相場より安く買えるケース。

#### A-3: 年代差 ±5年 以内で利益条件を満たす
```
|source_year - yahoo_year| <= 5
AND cert_company 一致（NGC または PCGS）
AND projected_profit_jpy > 0
AND projected_roi > 0%
```
> 年号違いでも同一シリーズ・同一設計で価値が近いケース。

---

### Level B — 価格参考のみ

```
Level A 条件を満たさない
AND 相場データとして参考になる類似コイン
```

- **候補化しない**
- pricing の補助データとして参照可能
- daily_candidates に入れない

---

### Level C — 除外

```
上記 A / B にも該当しない
OR 除外条件を1つでも満たす
```

| 除外条件 | 理由 |
|---------|------|
| non NGC/PCGS | cert 信頼性なし |
| cert 不明 / 偽造疑い | 個体同一性が担保できない |
| sold 済み / ended | 入札できない |
| multi-lot | 個体特定不可 |
| eBay: US/UK 以外発送 | 二重関税リスク |
| eBay: USD 以外の通貨 | 価格計算誤差リスク |
| 利益マイナス / ROI ゼロ以下 | 仕入れ対象外 |

---

## 2. CEO 承認条件

### 承認してよい条件（全て満たすこと）

- `auto_tier` が `AUTO_PASS` または `AUTO_REVIEW`
- `is_active = true` / `is_sold = false`
- stale でない（`last_status_checked_at` が 6時間以内）
- cert が NGC / PCGS として確認可能（外部リンクで確認済み）
- cert 番号が一致している
- evidence >= 3件
- `projected_profit_jpy > 0`
- `recommended_max_bid_jpy` が出ている
- eBay の場合は USD かつ US/UK 発送

### 保留にする条件

- pricing がない（Heritage / Spink の価格未入力）
- stale のまま（6時間以上更新なし）
- cert が曖昧（DB上 missing だが lot_title から確認が必要）
- evidence はあるが比較品質が弱い
- source listing 側の更新が古い
- 高額案件で確証が足りない

### 却下する条件

- `auto_tier = AUTO_REJECT`
- non NGC/PCGS / cert 不明
- sold 済み / multi-lot
- eBay で US/UK 発送外
- 利益マイナス / ROI 0%以下
- 個体同一性が怪しい

---

## 3. BOT抽出 → CAP監査 の二重チェック必須

```
1段目: BOT抽出（機械照合）
  Yahoo! seed × eBay listing / global lot
  → 仮 Level A 候補生成
  → candidate_match_results に記録

2段目: CAP監査（rules.py + audit/runner.py）
  以下を全チェック:
  - cert 妥当性
  - タイトル整合
  - グレード差
  - 年数差
  - 利益条件
  - shipping 条件
  - lot size（単品のみ）
  - stale でない
  - sold でない
  - ended でない
  → AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL を付与

AUDIT_PASS のみ → daily_candidates に昇格
AUDIT_HOLD     → 保留キュー（人間確認）
AUDIT_FAIL     → 履歴保存のみ（昇格しない）
```

**BOT抽出結果をそのまま daily_candidates に入れることは禁止。**

---

## 4. 例外承認ルール

### 例外承認してよいケース

- CEO が cert / 個体同一性を目視で確認済み
- DB上 cert missing でも lot_title / 画像から確定可能
- stale だが直近確認で実質問題なし
- 高利益で通常ルールより優先したい

**必須**: `reason_code` 入力 + `note` 必ず残す + source URL / cert URL を確認済みとして記録

### 例外承認してはいけないケース

- sold 確定
- multi-lot
- non NGC/PCGS が明らか
- 個体が別物の疑い
- eBay で発送元条件違反が明確

---

## 5. BitNow 取り扱いルール

```
BitNow は本流（daily_candidates）に入れない。
値段が高く、仕入れ向きではないため。

ただし将来の seller 直接交渉候補として negotiate_later テーブルに保存する。
自動交渉・自動送信は未実装。保存のみ。
```

---

## 6. 利益計算式（CEO確定）

```
expected_sale_jpy     = Yahoo!落札中央値（直近3か月）
customs_cost          = expected_sale_jpy × (関税率 × 1.1)
total_cost_jpy        = purchase_price_jpy
                        + customs_cost
                        + US_FORWARDING_JPY (¥2,000)
                        + DOMESTIC_SHIPPING_JPY (¥750)
gross_profit_jpy      = (expected_sale_jpy × (1 - YAHOO_FEE)) - total_cost_jpy
projected_roi         = gross_profit_jpy / total_cost_jpy
target_max_bid_jpy    = expected_sale_jpy / (1 + MIN_GROSS_MARGIN) - cost_excl_purchase
```

定数値: `constants.py` の `ProfitCalc` クラスを参照。

---

## 7. Yahoo!履歴の取り扱い規則

```
最初の10日間:
  Yahoo!新規履歴 → yahoo_sold_lots_staging (PENDING_CEO)
  CEO が承認 → APPROVED_TO_MAIN
  yahoo_promoter.py が昇格 → yahoo_sold_lots (本DB)

その後（Day 11以降）:
  CAP 監査主体への移行を検討
  ただし母集団の精度を担保できるまでは CEO 承認を維持

seed 生成の入力:
  yahoo_sold_lots (本DB) のみ
  yahoo_sold_lots_staging は絶対に使わない
```

---

## 改定履歴

| 日付 | 変更内容 | 承認者 |
|------|---------|--------|
| 2026-04-01 | Day 1 初版作成（Phase 1-9 対応） | COO (CAP) |
