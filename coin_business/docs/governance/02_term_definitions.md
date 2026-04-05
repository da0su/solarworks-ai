# 用語定義案 v1.0
作成日: 2026-04-02  作成者: Cap/COO  状態: CEO承認待ち

---

## 背景

これまでのセッションで「main」「staging」「現行DB」「本DB」「PENDING_CEO」等の
用語が定義なしに使われてきた。今後の統制のため、正式名称と意味を定義する。

---

## 正式用語定義

### 1. コアDB（既存DB）

**定義**: プロジェクト当初から存在し、CEOが承認している本体データ群。

**正式呼称**: `コアDB` または `既存テーブル`

| 構成テーブル |
|---|
| market_transactions |
| coin_slab_data |
| daily_candidates |
| daily_rates |
| cost_rules |
| candidate_evidence |
| candidate_pricing_snapshots |

**禁止呼称**: 「現行DB」（曖昧なため廃止）

---

### 2. Yahoo受け皿（Yahooステージング）

**定義**: Yahoo落札履歴を自動取得して一時格納する受け皿テーブル。
CEOレビュー前のデータのみを格納する。本格データとは明確に区別する。

**正式呼称**: `Yahooステージング` または `yahoo_sold_lots_staging`

**禁止呼称**: 「staging」単独（文脈が不明確）、「現行DB」、「本DB以外」

---

### 3. Yahoo本格格納先

**定義**: CEOが承認したYahoo落札データを格納する本格テーブル。
yahoo_sold_lots_stagingから昇格したデータのみを受け入れる。

**正式呼称**: `Yahoo本格テーブル` または `yahoo_sold_lots`

**禁止呼称**: 「main」（英語略称で定義が曖昧）、「本DB」（コアDBと混同する）

---

### 4. データステータス値

| 値 | 正式意味 | 使用テーブル |
|---|---|---|
| `PENDING_CEO` | CEOレビュー待ち（自動取得直後の初期状態） | yahoo_sold_lots_staging.status |
| `APPROVED_TO_MAIN` | CEO/CAP承認済み・Yahoo本格テーブルへの昇格処理待ち | yahoo_sold_lots_staging.status |
| `PROMOTED` | yahoo_sold_lotsへの昇格完了 | yahoo_sold_lots_staging.status |
| `REJECTED` | 却下 | yahoo_sold_lots_staging.status |
| `HELD` | 保留 | yahoo_sold_lots_staging.status |

---

### 5. 昇格処理

**定義**: APPROVED_TO_MAINステータスのレコードを
yahoo_sold_lots_stagingからyahoo_sold_lotsへ移動する処理。

**正式呼称**: `昇格処理` または `プロモーション`

**実行権限**: CEO明示承認後のみ実行可

**現在の状態**: **停止命令中**（2026-04-02 CEO指示）

---

### 6. 廃止用語一覧

| 廃止用語 | 代替用語 | 廃止理由 |
|---|---|---|
| `現行DB` | `コアDB` または `既存テーブル` | 「現行」の意味が曖昧 |
| `本DB` | `Yahoo本格テーブル` | コアDBと混同しやすい |
| `main` | `Yahoo本格テーブル` | 英語略称で定義不明確 |
| `staging` | `Yahooステージング` | 単独では文脈不明 |
| `母集団` | `Yahoo本格テーブル内データ` | 概念が曖昧 |

---

## 承認依頼

上記定義案について、CEO承認をお願いします。
承認後、本ファイルのステータスを「承認済み」に変更し、
CLAUDE.mdおよびKNOWLEDGE.mdに反映します。
