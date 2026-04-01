# 日次運用 SOP — CEO確認ダッシュボード

## 目的

CEOの役割を「候補の検証」から「例外を含む最終承認」に縮小し、
通常案件は証拠・価格・判定理由を見て即決できる状態を維持する。

**毎日の基本フロー**: 朝→候補確認 / 昼→承認・保留・却下 / 夜→入札キューと例外整理

---

## 起動

```bash
streamlit run coin_business/dashboard.py
# URL: http://localhost:8501
```

---

## 朝の実務手順（10〜20分）

1. ダッシュボード起動
2. サイドバー設定:
   ```
   REVIEW_NGのみ = ON
   Active only   = ON
   Stale除外     = ON
   Evidence >= 3
   Profit > 0
   ```
3. 上から順に確認（優先順）:
   - Active → Not Sold → Staleでない → 利益が高い → Evidenceが厚い
4. **まず REVIEW_NG を優先処理** → 旧システムの一括NGだが現システムでは再確認価値あり

---

## 昼の実務手順（30〜60分）

### 確認項目（1候補あたり）

| 項目 | 確認内容 |
|------|---------|
| Auto Tier | PASS / REVIEW / REJECT |
| Cert情報 | NGC / PCGS 確認 |
| Cert番号 | 一致確認（NGC Verify / PCGS Cert） |
| Source listing | active か |
| Yahoo相場 | evidence の bucket 分布 |
| Heritage / Spink / Numista | 証拠件数 |
| Expected Sale / Cost / Profit / ROI | pricing snapshot |
| Max Bid | recommended_max_bid_jpy |

### 判断アクション

| 判断 | 意味 | 次のアクション |
|------|------|--------------|
| **Approved** | 条件を満たし入札してよい | Bid Queue に追加 → bid_max_jpy 確認して登録 |
| **Held** | 情報不足・価格未確定・再確認必要 | そのまま保留 |
| **Rejected** | 仕入れ対象外 | NG reason code + note 必須 |

---

## 夜の実務手順（10〜15分）

- [ ] 当日件数確認（Approved / Held / Rejected / Queued）
- [ ] stale候補の有無確認
- [ ] 例外承認のnoteを確認
- [ ] pricing missing 候補を翌日 hold レーンに回す

---

## 承認基準

### 承認してよい条件

- AUTO_PASS または AUTO_REVIEW
- is_active = true / is_sold = false
- Cert が NGC / PCGS で確認可能、番号一致
- Evidence >= 3件
- Pricing あり (projected_profit_jpy > 0)
- recommended_max_bid_jpy あり
- eBay は USD かつ US/UK 発送

### 保留にする条件

- pricing がない
- stale のまま
- cert が曖昧
- evidence はあるが比較品質が弱い
- source更新が古い
- 高額案件で確証不足

### 却下する条件

- AUTO_REJECT
- non NGC/PCGS / cert 不明
- sold 済み / multi-lot
- eBay で発送元条件違反
- 利益が薄い / マイナス
- 個体同一性が怪しい

---

## 例外承認ルール

### 例外承認してよいケース

- CEO が cert / 個体同一性を目視で確認済み
- DB上 cert missing でも lot_title / 画像から確定可能
- stale だが直近確認済み
- 高利益で通常ルールより優先したい

**必須**: reason_code 入力 + note 必ず残す + source URL / cert URL を確認済み記録

### 例外承認してはいけないケース

- sold確定 / multi-lot / 明確な non NGC/PCGS
- 別個体の疑い / eBay の発送条件違反が明確

### pricing missing の例外

以下条件を全て満たす場合のみ手動再評価可:
- evidence が十分 / cert が確実
- source の見積額を人が補完できる
- note に手動価格の根拠を書く

---

## 毎日見るべき4指標

| 指標 | 確認場所 |
|------|---------|
| REVIEW_NG 残件数 | ダッシュボード サイドバー |
| pricing missing 件数 | `ops_pricing_missing.sql` B-2 |
| stale active 件数 | `ops_stale_candidates.sql` D-2 |
| bid queue 件数 | ダッシュボード「入札実績」タブ |

---

## 外部確認リンク

| リンク | 用途 |
|--------|------|
| [NGC Verify](https://www.ngccoin.com/certlookup/) | NGC cert確認 |
| [PCGS Cert Verification](https://www.pcgs.com/cert/) | PCGS cert確認 |
| [Heritage Archive](https://coins.ha.com/archives/price-results.zx) | 落札相場 |
| [Spink Prices](https://www.spink.com/results) | 落札相場 |
