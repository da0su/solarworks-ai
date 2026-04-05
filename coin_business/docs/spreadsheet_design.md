# コイン事業 現況管理スプレッドシート 設計書
作成日: 2026-04-04 | 作成者: キャップ

---

## スプシの位置づけ
- **正本**: Supabase DB (ceo_review_log テーブル)
- **スプシ**: CEOとマーケが確認・指示するための「現況ビュー」
- **Slack**: 報告・依頼・差し戻しの窓口（#coin-cap-marke）
- **Notion**: ルール・ナレッジ・計算式

スプシは「読む・指示する」場所。更新はキャップがDBから定期エクスポートして反映。

---

## シート構成（5シート）

### Sheet 1: ✅ MARKETING_REVIEW（91件）
**目的**: CEO確認・マーケ判断の主戦場

| 列 | 内容 | 例 |
|---|---|---|
| A | item_id | noble_141_2026_apr_473 |
| B | オークション | Noble 141 / Heritage / Spink |
| C | コイン名 | Elizabeth II Perth Mint 1oz |
| D | 国 | Australia |
| E | 素材 | 金 |
| F | 年号 | 2023 |
| G | グレード | MS70 |
| H | 認定機関 | NGC / PCGS |
| I | **判定** | ✅CAP_BUY / 🔍CEO_CHECK |
| J | **入札上限(USD)** | $679 |
| K | 推定売価(JPY) | ¥120,000 |
| L | 推定利益(JPY) | ¥18,000 |
| M | 予想ROI(%) | 15.0% |
| N | 参照ヤフオク商品 | AU 1oz 2023 MS70 120k |
| O | 証拠状況 | PRICE_NEEDED / OK |
| P | 比較タイプ | TYPE_ONLY / EXACT |
| Q | URL | リンク |
| R | 更新日 | 2026-04-04 |
| S | マーケコメント | (マーケ記入欄) |
| T | CEOコメント | (CEO記入欄) |

**色分け:**
- 🟢 緑: CAP_BUY（即入札OK）
- 🟡 黄: CEO_CHECK（CEO判断待ち）
- 🔴 赤: PRICE_OVER_BL（予算超過）

---

### Sheet 2: 🔍 INVESTIGATION（70件）
**目的**: キャップが調査中・参照価格未設定

| 列 | 内容 |
|---|---|
| A | item_id |
| B | オークション |
| C | コイン名 |
| D | 証拠状況 (evidence_status) |
| E | 優先度メモ |
| F | 更新日 |

**主なステータス:**
- `スラブ未確認`: 66件 → NGC/PCGS確認待ち
- `要確認`: 4件 → 追加調査待ち

---

### Sheet 3: 👁 OBSERVATION（78件）
**目的**: 相場監視のみ。仕入れ対象外だが価格動向を追う

| 列 | 内容 |
|---|---|
| A | item_id |
| B | オークション |
| C | コイン名 |
| D | 除外理由 |
| E | 更新日 |

---

### Sheet 4: 📊 サマリーダッシュボード
**目的**: CEOが1分で全体把握できるシート

```
■ 現況サマリー（最終更新: 2026-04-04）

MARKETING_REVIEW  91件
  └ CAP_BUY      1件  ← 即入札可
  └ CEO_CHECK    90件 ← CEO判断待ち

INVESTIGATION    70件（スラブ未確認66 / 要確認4）
OBSERVATION      78件

■ オークション別（MARKETING_REVIEW）
  Noble 141              51件
  Spink                  18件
  Heritage Spotlight PL  12件
  Noble 10505             3件
  Heritage 61607          3件
  Heritage HK Spring      2件
  other                   2件

■ 次のアクション
1. CEO_CHECK 90件 → CEOが入札判断
2. INVESTIGATION 70件 → 参照価格調査でMARKETING_REVIEWに昇格
3. Noble 141 / Heritage 落札後 → PRICE_NEEDED案件を実売価格で更新
```

---

### Sheet 5: 📋 ルール・凡例
- 判定の意味（CAP_BUY / CEO_CHECK / OBSERVATION）
- 利益計算式（BL計算、FX: USD=150, AUD=95, HKD=19.5）
- 更新タイミング・担当
- 禁止事項（直接セル編集禁止 → DBが正本）

---

## 更新フロー

```
DB (Supabase)
  ↓ キャップが python export.py を実行
CSV (data/spreadsheet_export.csv)
  ↓ Googleスプシにインポート（または手動貼り付け）
スプシ更新完了
  ↓ Slack #coin-cap-marke に完了報告
```

**更新タイミング（暫定）:**
- Noble/Heritage/Spink 落札後（翌営業日）
- CEO_CHECKの判断が出た後
- マーケから依頼があった時

---

## 初版作成手順

1. [data/spreadsheet_export.csv](../data/spreadsheet_export.csv) をダウンロード
2. Googleスプレッドシートを新規作成
3. Sheet 1 に MARKETING_REVIEW データを貼り付け
4. 色分けルールを条件付き書式で設定
5. Sheet 4 にサマリーをコピー
6. URLをキャップに共有 → Supabase / KNOWLEDGE.md に記録

---

## 今後の自動化（Phase 2）

- キャップが定期的に `python export_to_sheets.py` を実行 → Google Sheets API で直接更新
- 落札後自動トリガー → 実売価格を DB に書き戻し → スプシ自動更新
