# Day15-17 運用チェックリスト

## 目的

CEOが「見るべきものだけ見て、押すだけ」で回る状態を維持する。
毎日見るべき数字は4つだけ:

| 指標 | SQL |
|------|-----|
| REVIEW_NG 残件数 | ops_review_ng_priority.sql A-2 |
| pricing missing 件数 | ops_pricing_missing.sql B-2 |
| stale active 件数 | ops_stale_candidates.sql D-2 |
| bid queue 件数 | ダッシュボード「入札実績」タブ |

---

## ダッシュボード起動

```bash
streamlit run coin_business/dashboard.py
# URL: http://localhost:8501
```

---

## Day15 — 初日運用（REVIEW_NG消化開始日）

### 朝

- [ ] nightly_ops の成功を確認
  - status phase error = 0
  - tier phase error = 0
  - pricing phase の skip は Heritage / Spink なら許容
- [ ] 朝イチ確認SQL実行 (`ops_stale_candidates.sql` の E セクション)
  - total / has_evidence / has_pricing / auto_review / stale_count
- [ ] ダッシュボード起動

サイドバー設定:
```
REVIEW_NGのみ = ON
Active only  = ON
Stale 除外   = ON
min evidence >= 3
min projected profit > 0
```

### 昼

- [ ] 上位20件の REVIEW_NG をレビュー
  - 各候補で確認すること:
    - Auto Tier 理由 (Hard fail / Warning バッジ)
    - cert 情報 (NGC Verify / PCGS Cert リンク)
    - pricing snapshot (直近3か月の相場)
    - evidence bundle (source listing / cert / yahoo 件数)
    - source listing の active 状態
  - 判断:
    - 良い → **approved** → そのまま bid queue 追加
    - 迷う → **held**
    - ダメ → **rejected** (NG reason code + note 必須)

### 夜

- [ ] 今日の処理件数を確認
  - approved 件数 / held 件数 / rejected 件数 / queued 件数
- [ ] 例外承認があれば note を確認
- [ ] stale 候補が残っていれば翌朝 refresh 対象に回す

---

## Day16 — 通常運用（承認→キューを安定化）

### 朝

- [ ] stale候補一覧SQL実行 (`ops_stale_candidates.sql` D-2)
- [ ] stale かつ active な候補を優先 refresh（ダッシュボード Status パネル）
- [ ] REVIEW_NG の残件数を確認

### 昼

- [ ] approved 済み候補の bid queue を確認（入札実績タブ）
- [ ] ステータス更新:
  - `queued` → `submitted`
  - `submitted` → `won` / `lost`
- [ ] 入札失敗・不整合があれば note 追加

### 夜

- [ ] pricing missing 一覧を source別に確認 (`ops_pricing_missing.sql`)
- [ ] Heritage / Spink のうち、価値の高いものだけ手動価格入力対象にする
- [ ] DISAGREE_FN 例外案件が増えていないか確認

---

## Day17 — 安定化（ルール固定・例外処理整理）

### 朝

- [ ] source別除外理由SQL実行 (`ops_reject_reason_by_source.sql`)
- [ ] 除外理由の偏りを確認:
  - eBay ship_from_invalid が多いか
  - missing_cert が多いか
  - non_ngc_pcgs が多いか
- [ ] 偏りが大きければ、取得側の改善対象としてメモ

### 昼

- [ ] CEOレビューの実感と shadow 結果を見比べる
- [ ] 確認事項:
  - DISAGREE_FP が増えていないか
  - REVIEW_NG の中からどれだけ approved されたか
  - pricing missing を hold に寄せる運用で問題ないか

### 夜

- [ ] 運用ルールを固定
- [ ] 承認基準と例外基準を短文化
- [ ] 翌週以降は「改善」ではなく「反復運用」に入る

---

## 承認基準

### 承認してよい条件

- AUTO_PASS または AUTO_REVIEW
- is_active = true
- is_sold = false
- stale でない、または直前 refresh 済み
- cert が NGC / PCGS として確認できる
- cert番号が一致している
- evidence が十分（目安3件以上）
- pricing snapshot がある
- projected_profit_jpy > 0
- recommended_max_bid_jpy が出ている
- eBay の場合は USD かつ US/UK 発送

### 保留にする条件

- pricing がない
- stale のまま
- evidence はあるが cert が曖昧
- 価格は出ているが比較品質が弱い
- source listing 側の状態更新が古い
- 高額だが確証が足りない

### 却下する条件

- AUTO_REJECT
- non NGC/PCGS
- cert 不明
- sold 済み
- multi lot
- eBay で US/UK 発送外
- 利益が薄い / マイナス
- 個体同一性が怪しい

---

## 例外対応ルール

### 例外承認してよいケース

- CEO が cert / 個体同一性を目視で確認できた
- DB上は cert missing だが lot_title と画像から確定できる
- stale だが直近確認で実質問題なし
- 高利益で、通常ルールより優先したい

**必須条件**: reason_code + note 必ず書く。source URL / cert URL を確認済みとして記録。

### 例外承認してはいけないケース

- sold 確定
- multi-lot
- non NGC/PCGS が明らか
- 個体が別物の疑い
- eBay で発送元条件違反が明確

### pricing missing の例外

Heritage / Spink の価格未入力案件は以下に限って手動再評価可:
- evidence が十分
- cert が確実
- source の見積額を人が補完できる
- note に手動価格の根拠を書く

---

## 実務上の推奨処理順

```
1. REVIEW_NG 上位20件
2. stale active 候補
3. pricing missing のうち high evidence 候補
4. bid queue の status 更新
5. source別除外理由の確認（週1回）
```

---

## SQL ファイル一覧

| ファイル | 目的 |
|---------|------|
| `docs/sql/ops_review_ng_priority.sql` | REVIEW_NG 優先キュー (A-1/A-2/A-3) |
| `docs/sql/ops_pricing_missing.sql` | pricing missing 一覧 (B-1〜B-4) |
| `docs/sql/ops_reject_reason_by_source.sql` | AUTO_REJECT 推定理由 (C-1/C-2/C-3) |
| `docs/sql/ops_stale_candidates.sql` | stale候補 + 朝イチ確認SQL (D-1/D-2/D-3/E) |

---

## 現在のDB状態 (2026-04-01 確認)

| 指標 | 値 |
|------|-----|
| 総候補数 | 518件 |
| Evidence完備 | 518/518 (100%) |
| Pricing完備 | 368/518 (71%) |
| Tier設定済み | 518/518 (100%) |
| AUTO_PASS | 0件 |
| AUTO_REVIEW | 133件 |
| AUTO_REJECT | 385件 |
| REVIEW_NG | 133件 |
| Shadow Precision | 74.1% |
| DISAGREE_FP | 0件 |
| pricing missing | 150件 (Heritage 25 + Spink 125) |
