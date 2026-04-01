# Day18以降 改善ロードマップ

## 全体方針

**V1は動く。ここからは精度 → 実行 → 拡張の順に伸ばす。**

優先順位:
- P0: 自動承認率を上げるための品質改善
- P1: 半自動入札を自動入札へ近づける
- P2: 価格予測の高度化
- P3: sourcingチャネル統合
- P4: 学習ループと経営ダッシュボード

---

## Phase A — Day18〜Day24: 安定運用 + 自動承認率向上

**目標**: CEOが見る件数をさらに減らす。新機能より精度改善を優先。

### 完了条件

- CEOレビュー件数が 133 → 50件台へ減る
- pricing coverage が 71% → 80%超へ上がる

### 実装項目

#### 1. pricing missing 手動入力UI

Heritage / Spink候補向けに以下フィールドを追加:
```
manual_estimated_buy_price  -- 手動価格
manual_price_source_note    -- 根拠メモ
manual_priced_by            -- 入力者
manual_priced_at            -- 入力日時
```
入力後に pricing snapshot 再生成を自動実行。

#### 2. hard fail / warning のDB保存

- 現在の判定理由を DB列またはJSONに保存
- source別誤判定分析をSQLではなくDB直読みにする

#### 3. REVIEW_NG 専用キュー強化

- 優先度スコアを明示
- 「今日レビューすべき20件」を固定表示
- 前回note の可視化

#### 4. source別ルールの明文化

- eBayだけ USD / US/UK 適用
- Heritage / Spink は別ロジック
- 条件誤適用を完全に防ぐ

---

## Phase B — Day25〜Day35: 自動入札の準備

**目標**: 承認後にqueueするだけでなく、承認済み案件を自動入札へ送れる状態に。

### 完了条件

- approved → auto submit を一部案件で運用可能
- dry_run と本番の差分が追える

### 実装項目

#### 1. Bid Execution Policy

```python
execution_mode = "dry_run" | "manual_bridge" | "auto"
```
- source別に自動入札可否を設定
- 上限金額 / 有効期限 / 再試行回数を保持

#### 2. auto-bid eligibility

以下の条件を全て満たす場合のみ自動入札可:
- AUTO_PASS
- pricing あり / staleでない / active / cert一致
- approved済み / source別自動実行対応済み

#### 3. 入札安全装置

- max bid を超えない
- stale なら停止
- sold / inactive なら停止
- 例外承認案件は自動入札しない

#### 4. 実行ログ強化

- request payload / response summary / failure reason / retry history

---

## Phase C — Day36〜Day50: ML価格予測 / 価格推定高度化

**目標**: comps不足時でも妥当価格を出す。Heritage / Spink 未価格候補に推定値。

### 完了条件

- pricing coverage を 90%近くまで拡張
- manual pricing 件数を大幅削減

### 実装項目

#### 1. Feature整備

```
year, denomination, country, mintmark, grade, grader
cert有無, source, recency_bucket
Yahoo sold stats, comp_count, comparison_quality_score
```

#### 2. ベースラインモデル

最初は複雑なMLではなく:
- 線形回帰
- LightGBM / XGBoost系
- quantile 回帰

#### 3. 予測値の使い方（補助として使う）

| comps状況 | 方針 |
|-----------|------|
| comps十分 | 現行pricing優先 |
| comps不足 | ML予測を fallback |
| 乖離大 | HOLD |

#### 4. confidence出力

- 予測値 / 信頼区間 / comp不足フラグ / fallback理由

---

## Phase D — Day51〜Day70: 複数sourcing統合

**目標**: 取得チャネルを広げる。優先順は「データ品質と運用価値」。

### 完了条件

- 3ソース以上を統合した同一 review queue
- sourceごとの差をUI上で吸収

### sourcing 優先順位

| 順位 | Source | 理由 |
|------|--------|------|
| 1位 | eBay | 母数大・既存連携あり・active/sold更新取りやすい（本番API承認要） |
| 2位 | Heritage | 高品質候補多・Archive証拠価値高 |
| 3位 | Spink | 補助ソースとして有効（価格データ欠損あり） |
| 4位以降 | NumisBids / Stack's / Goldberg / CNG 等 | Phase D後半以降 |

### 実装項目

- source connector 標準I/F
- 共通 candidate schema
- source別 status refresh
- source別 execution policy
- source別 anomaly flags

---

## Phase E — Day71〜Day90: 自己学習と経営ダッシュボード

**目標**: 業務を改善ループへ入れる。CEO判断を次のルール改善へ還元。

### 完了条件

- 「どのsourceが儲かるか」
- 「どの条件で落としすぎるか」
- 「どこを自動化すべきか」 が数値で見える

### 実装項目

#### 1. Feedback Loop

- approved / held / rejected 理由を構造化
- reason code 集計
- source別 / grader別 / country別の成功率集計

#### 2. Outcome Tracking

- won / lost / resale result
- 実績利益 vs 予測利益 / 誤差分析

#### 3. Management Dashboard

| 指標 | 内容 |
|------|------|
| source別仕入れ効率 | ROI / 承認率 |
| queue conversion | approved → queued → won |
| 実利益 | won × (sale - cost) |
| stale率 | source別 |
| pricing coverage | source別 |
| 誤判定率 | FP / FN |

---

## 推奨優先順位まとめ

### 直近でやる順（P0〜P2）

**P0**
- pricing missing 手動入力UI
- REVIEW_NG 専用レビュー強化
- 判定理由のDB保存

**P1**
- auto-bid safety policy
- source別実行ポリシー
- dry-run / real-run の明確分離

**P2**
- ML価格予測
- confidence / fallback logic
- pricing coverage拡大

**P3**
- 複数sourcing統合
- source connector 標準化

**P4**
- 経営ダッシュボード
- 学習ループ
- 実績利益との誤差分析

---

## 経営判断メモ

順番を逆にすると派手な機能が増えても精度が悪くなる。
今の段階では **精度 → 実行 → 拡張** の順が正解。

- まず自動承認率を上げる
- その次に自動入札を限定導入する
- その後でML価格予測と複数sourcing統合に進む
