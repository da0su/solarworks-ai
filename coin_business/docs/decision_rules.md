# 仕入れ判断ルール定義書

**固定日**: Day 1 (基準コミット: 8e228c2)
**変更権限**: COO / CEO のみ。CAP は実装時にこの文書に従う。

---

## 0. 絶対ルール（変更不可）

### §0-A. 【最上位】価格帯・利益条件（CEO確定 2026-04-03）

| 条件 | 値 | 理由 |
|------|-----|------|
| DB対象価格 | **Yahoo落札¥100,000以上のみ** | 2025-01-01〜2026-03中旬の実績のみ使用 |
| 最低利益条件 | **¥20,000以上** | これ未満の仕入れは意味なし |
| 仕入対象価格帯 | **¥100,000以上ゾーンのコインのみ** | DBと仕入対象は同一価格帯 |
| 安価コイン | **仕入候補・注目候補の主軸にしない** | 穴埋め目的の混入禁止 |

**→ CAPが守るべき一言**: 「入札が多い」「市場で人気」だけでは注目候補にしない。**当社の¥10万以上・利益¥2万以上ルールに乗る価格帯か**を必ず確認すること。

### §0-B. その他絶対ルール

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

## 8. コイン種別 固有除外ルール

### 8-1. 旧1円銀貨（Japan 1 Yen Meiji Old Silver）チョップマーク除外

**ルール（CEO承認: 2026-04-03）**

```
旧1円銀貨において、以下のいずれかに該当する個体は
チョップなし清品の比較対象に含めない。

該当キーワード（タイトル・説明文・grade_text に含まれる場合）:
  CHOPMARKED / CHOP MARK / chopmarked
  商人印 / 打刻印 / カウンターマーク
  COUNTERMARK / counterstamp

理由:
  チョップマーク付き個体は、同一グレード・同一年でも商品性が異なる。
  Yahoo!の参照価格（チョップなし清品ベース）と直接比較すると誤判定を生む。
  価格差は grade MS64 で ¥224,000（清品）vs ¥30,000-50,000（チョップ付き）程度。

実務上の扱い:
  - 価格参照: 清品・チョップ付きを分離して集計
  - 利益判定: 清品Yahoo参照価格にチョップ付きeBay価格を当てることは禁止
  - 候補抽出: チョップ付きは Level C（除外）扱い
```

**検出方法（スクリプト実装時）**:
```python
CHOPMARK_KEYWORDS = [
    'chopmarked', 'chop mark', 'chop-mark', 'chopmark',
    'countermark', 'counterstamp', '商人印', '打刻印', 'カウンターマーク'
]
def is_chopmarked(title: str) -> bool:
    t = title.lower()
    return any(kw.lower() in t for kw in CHOPMARK_KEYWORDS)
```

---

## §9. 日次スキャン運用固定ルール（2026-04-03 CEO指示）

以下は `daily_scan.py` 運用の固定ルール。変更には CEO承認が必要。

### §9-1. 正本ルール
- 正式結果は **6バケット方式フォアグラウンド実行の出力のみ**を正本とする
- 旧アーキテクチャ（DB-record per-coin 個別検索 / 813クエリ方式）の結果は **即時破棄**
- バックグラウンドジョブの旧版出力は、正本と競合する場合は常に旧版を捨てる

### §9-2. 0入札 Auction の扱い
- eBay Auction で **入札数 = 0 かつ価格 = $0.00** のアイテムは **WATCH** 扱い（FAIL 扱い禁止）
- 理由: eBay Browse API は0入札時に価格を返さない仕様。入札開始後に再評価する
- WATCH案件はCEOに提示するが「BUY推奨」ではない。入札経過後のROI算出が前提

### §9-3. LOT除外（固定）
以下のいずれかに該当するアイテムは **スキャン対象外**（EXCLUDE_KW固定）:
- `slab lot`, `estate sale`, `grab bag`, `mystery lot`, `lot of`, `N coins`（複数枚ロット）
- `replica`, `restrike copy`, `fantasy`, `token`
- `details`, `cleaned`, `damage`, `chopmarked`, `countermark`

### §9-4. サイズ正規化（分数イーグル）
American Eagle には1oz・1/2oz・1/4oz・1/10oz の4サイズが存在し価値が大幅に異なる。
照合時に denomination で区別する（first-matchのため小サイズを先に定義）:

| denomination | 面値 | 重量 | 主なキーワード |
|---|---|---|---|
| `$5 Eagle`  | G$5  | 1/10 oz | `1/10 oz`, `1/10oz`, `g$5`, `$5 gold eagle` |
| `$10 Eagle` | G$10 | 1/4 oz  | `1/4 oz gold`, `g$10`, `$10 gold eagle` |
| `$25 Eagle` | G$25 | 1/2 oz  | `1/2 oz gold`, `g$25`, `$25 gold` |
| `$50 Eagle` | $50  | 1 oz    | `american eagle`, `50 dollar`, `eagle 1 oz` |

- DB側（Yahoo）に 1/10oz の記録がなく $50 Eagle 記録と denomination が合わない場合 → マッチ不成立
- 分数Eagle が誤って $50 Eagle の参照価格（¥300,000〜¥900,000級）に当たることを防ぐ

### §9-5. ROUND の原則
- 検索クエリは `ROUND1_BUCKETS` の **6本のみ** を使用（追加・削除禁止）
- cert番号は **最終照合専用**（検索入口に使わない）
- ROUND2 は「年号差のみ」または「グレード差のみ」の1変数差にとどめる

---

## 改定履歴

| 日付 | 変更内容 | 承認者 |
|------|---------|--------|
| 2026-04-01 | Day 1 初版作成（Phase 1-9 対応） | COO (CAP) |
| 2026-04-03 | §8-1 旧1円銀貨チョップマーク除外ルール追加 | CEO承認 |
| 2026-04-03 | §9 日次スキャン運用固定ルール追加（正本/WATCH/LOT除外/サイズ正規化/ROUND原則） | CEO承認 |
