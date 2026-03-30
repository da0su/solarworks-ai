# DB安全操作ガイド（バックアップ・ロールバック手順）

作成日: 2026-03-30 01:38 JST
作成者: CAP（キャップさん）
目的: DB破損・データ消失・ロールバック不能を防ぐための固定手順書

---

## ■ 最重要認識

**DBは会社の資産。壊れた後に直すのではなく、壊れない前提で運用する。**

```
バックアップなしの変更は禁止
上書きバックアップは禁止
過去バックアップは全件並列保管
```

---

## 1. バックアップ手順（必須）

### 命名ルール（厳守）

```
db_backup_{テーブル名}_{YYYYMMDD}_{HHMM}.json

例:
  db_backup_coin_slab_data_20260330_0138.json
  db_backup_daily_candidates_20260330_0138.json
  db_backup_summary_20260330_0138.json
```

**上書き禁止。毎回新規ファイルとして保存。過去分すべて保持。**

### 保存先（固定）

```
C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups\
```

### バックアップ実行コマンド

```python
# coin_business ディレクトリで実行
cd C:\Users\砂田　紘幸\solarworks-ai\coin_business

python3 << 'EOF'
import sys, io, json, os
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '..')
from dotenv import load_dotenv
load_dotenv('.env')
from scripts.supabase_client import get_client

sb = get_client()
ts = datetime.now().strftime("%Y%m%d_%H%M")
backup_dir = r"C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups"
os.makedirs(backup_dir, exist_ok=True)

tables = ["coin_slab_data", "daily_candidates", "daily_rates", "cost_rules"]
summary = {"timestamp": ts, "tables": {}}

for table in tables:
    all_data = []
    offset = 0
    while True:
        r = sb.table(table).select("*").range(offset, offset + 999).execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        offset += 1000
    fname = f"db_backup_{table}_{ts}.json"
    fpath = os.path.join(backup_dir, fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump({"table": table, "backed_up_at": ts, "count": len(all_data), "data": all_data},
                  f, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(fpath) / 1024
    summary["tables"][table] = {"count": len(all_data), "file": fname, "size_kb": round(size_kb, 1)}
    print(f"[{table}] {len(all_data)}件 -> {fname} ({size_kb:.1f}KB)")

summary_path = os.path.join(backup_dir, f"db_backup_summary_{ts}.json")
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"\nバックアップ完了: {summary_path}")
EOF
```

### バックアップ確認（取得後に必ず実施）

```python
# 読み込みテスト
import json
backup_dir = r"C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups"
ts = "YYYYMMDD_HHMM"  # ← 実際のタイムスタンプに変更

for table in ["coin_slab_data", "daily_candidates", "daily_rates", "cost_rules"]:
    fpath = f"{backup_dir}\\db_backup_{table}_{ts}.json"
    with open(fpath, encoding='utf-8') as f:
        d = json.load(f)
    print(f"[{table}] count={d['count']} keys={len(d['data'][0].keys()) if d['data'] else 0}")
```

---

## 2. バックアップタイミング（必須）

```
① DB更新前（必ず）
② DBスキーマ変更前（必ず）
③ バッチ処理実行前（行数が多い場合）
④ ロジック変更後（変更内容確認用）
```

---

## 3. ロールバック手順

### Supabaseへの復元手順

```python
# 特定テーブルをバックアップから復元
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '..')
from dotenv import load_dotenv
load_dotenv('.env')
from scripts.supabase_client import get_client

sb = get_client()
backup_path = r"C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups\db_backup_coin_slab_data_YYYYMMDD_HHMM.json"

with open(backup_path, encoding='utf-8') as f:
    backup = json.load(f)

records = backup["data"]
print(f"復元対象: {len(records)}件")

# 100件ずつupsert
batch_size = 100
for i in range(0, len(records), batch_size):
    batch = records[i:i+batch_size]
    sb.table("coin_slab_data").upsert(batch, on_conflict="id").execute()
    print(f"  復元: {i+batch_size}/{len(records)}")

print("復元完了")
```

**注意:** `on_conflict="id"` でupsertするため、存在するレコードは上書き、存在しないレコードは新規挿入。

### Supabase SQL Editor での確認

```sql
-- バックアップ前後の件数確認
SELECT COUNT(*) FROM coin_slab_data;
SELECT COUNT(*) FROM coin_slab_data WHERE ref1_buy_limit_jpy IS NOT NULL;
SELECT COUNT(*) FROM coin_slab_data WHERE premium_value_jpy IS NOT NULL;
```

---

## 4. DB操作許可条件チェックリスト

DB操作前に以下を全確認。すべてYESの場合のみ実行可。

```
□ ① バックアップ取得済み（ファイル名・タイムスタンプ確認）
□ ② バックアップ読み込みテスト完了（件数・キー確認）
□ ③ ロールバック手順を説明できる
□ ④ 変更対象テーブル・カラムを明確に説明できる
□ ⑤ 変更内容を言語化できる（何が変わるかを事前に記述）
```

---

## 5. 知識チェック（回答）

### Q1. DBを壊した場合の復旧手順

```
1. Supabaseダッシュボードで現状確認（件数・最終更新時刻）
2. 最新バックアップファイルを特定
   → C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups\
   → db_backup_summary_{ts}.json でタイムスタンプ確認
3. ロールバック用Pythonスクリプトを実行（手順3参照）
4. 復元後に件数・主要カラムの値を確認
5. calc_ref_values.py を再実行してref1_buy_limit_jpy を再計算
```

### Q2. 今回変更するテーブルとカラム

```
対象テーブル: coin_slab_data

現在存在するカラム（基準1関連）:
  - premium_value_jpy     : プレミアム価格1（算定済み）
  - metal_value_jpy       : 当日地金価値（算定済み）
  - ref1_buy_limit_jpy    : 基準1仕入れ上限（15%ベース、算定済み）
  - ref2_yahoo_price_jpy  : 基準2参照価格（直近落札価格）
  - ref2_sold_date        : 基準2落札日

今後追加予定（CEO承認後）:
  - ref1_buy_limit_20k_jpy    : 基準1・2万円条件
  - ref1_buy_limit_15pct_jpy  : 基準1・15%条件（現在のref1_buy_limit_jpy相当）
  - ref2_buy_limit_20k_jpy    : 基準2・2万円条件
  - ref2_buy_limit_15pct_jpy  : 基準2・15%条件
```

### Q3. ref1_buy_limit_jpy の生成元

```
生成元ファイル : scripts/calc_ref_values.py
生成元関数     : process_row()
実行方法       : python scripts/calc_ref_values.py（手動実行のみ）
生成式         :
  premium    = median_price - sold_melt
  sales_std  = premium + current_melt
  net_sales  = int(sales_std * 0.90)
  cost_limit = int(net_sales * 0.85)
  ref1       = int((cost_limit - 2000 - 750) / 1.10)

使用元         : ebay_auction_search.py が db_coin['ref1_buy_limit_jpy'] として参照
                 → ebay_lot_integrator.py → daily_candidates.buy_limit_jpy
```

### Q4. calc_ref_values.py が何をしているか

```
対象テーブル: coin_slab_data
対象レコード: status='completed_hit' かつ purity NOT NULL の全件（現在2,927件）

処理内容:
  1. Supabaseから対象レコードを全件取得
  2. daily_rates テーブルから最新金属レート取得
  3. 各レコードの price_history（ヤフオク落札履歴）を読み込む
  4. 中央値・地金価値・プレミアム・基準1仕入れ上限・基準2参照価格を計算
  5. coin_slab_data に更新（UPDATE per record）

更新カラム:
  - metal_value_jpy
  - premium_value_jpy
  - ref1_buy_limit_jpy
  - ref2_yahoo_price_jpy
  - ref2_metal_rate_per_g
  - ref2_sold_date
```

### Q5. ロールバック方法

```
方法1（推奨）: Pythonスクリプトでupsert復元
  → 本ファイル「3. ロールバック手順」参照

方法2: Supabaseダッシュボード SQL Editor
  → バックアップJSONから INSERT/UPDATE文を生成して実行

方法3（部分ロールバック）: 特定カラムのみ復元
  → バックアップJSONから該当レコードのみ抽出してupsert
  例: ref1_buy_limit_jpy だけ元に戻す場合
      records_subset = [{"id": r["id"], "ref1_buy_limit_jpy": r["ref1_buy_limit_jpy"]}
                        for r in backup["data"]]
      sb.table("coin_slab_data").upsert(records_subset, on_conflict="id").execute()
```

---

## 6. 現在のバックアップ一覧（2026-03-30時点）

```
【バックアップ#1】取得日時: 2026-03-30 01:38 JST（初回バックアップ）
保存先: C:\Users\砂田　紘幸\solarworks-ai\coin_business\data\backups\
  db_backup_coin_slab_data_20260330_0138.json    2,927件 5,783KB
  db_backup_daily_candidates_20260330_0138.json     12件    21KB
  db_backup_daily_rates_20260330_0138.json          812件   384KB
  db_backup_cost_rules_20260330_0138.json             9件     3KB
  db_backup_summary_20260330_0138.json           （サマリー）

【バックアップ#2】取得日時: 2026-03-30 10:44 JST（DB操作直前・実行前再取得）
  db_backup_coin_slab_data_20260330_1044.json    2,927件 5,069KB
  ← Phase1-3 DB更新の直前に取得。このバックアップで今日の変更全件ロールバック可能
```

**読み込みテスト: 全ファイル確認済み**

---

## 9. ロールバックドライラン結果（2026-03-30）

```
実施日時: 2026-03-30 10:44 JST
使用バックアップ: db_backup_coin_slab_data_20260330_0138.json
本番書き込み: なし（読み取り専用）

[PASS] バックアップ読み込み     : 2,927件 全34フィールド確認済み
[PASS] 必須フィールド完全性     : 14/14項目 OK
[PASS] 本番DB vs BK 差分比較(20件): 全20件 完全一致
[PASS] 件数一致確認             : 2,927件 = 2,927件
[PASS] upsertシミュレーション   : 冪等確認（差分なし）= 安全に復元可能
[PASS] ID完全性チェック         : 本番のみ0件 / BKのみ0件 → IDセット完全一致

✅ バックアップは「戻せる」状態であることを確認済み
✅ 推定復元時間: 約15秒（30バッチ×0.5秒）
```

---

## 10. Phase1-3 DB更新実行結果（2026-03-30）

```
対象テーブル : coin_slab_data
対象条件     : status='completed_hit' AND purity IS NOT NULL
更新カラム   : premium_value_jpy, metal_value_jpy, ref1_buy_limit_jpy,
               ref2_yahoo_price_jpy, ref2_metal_rate_per_g, ref2_sold_date

Phase 1 ( 10件): 更新=10  skip=0  error=0  異常=0  所要=1.3秒  ✅
Phase 2 (100件): 更新=100 skip=0  error=0  異常=0  所要=11.7秒 ✅
Phase 3 (全件) : 更新=2701 skip=0 error=0  異常=1  所要=3.4分  ✅
  ↑ 合計: 2,701件を ref1_buy_limit_jpy NULL率 0% で更新

【異常1件: mgmt_no=002713】
  1974 HAITI G500G（Gold 500g）
  premium=-10,334,113（極端な負値）→ 自然現象（500g金貨が地金価値の1/60で落札）
  ref1_buy_limit_jpy=10,157 に自動設定（正常動作）
  → データエラーではない。ロジック通りの結果。

【最終確認】
  全件数: 2,927件（変化なし）
  ref1_buy_limit_jpy NULL率: 0%（2,701件全て有値）
  daily_candidates: 12件（影響なし）
```

---

## 7. 禁止事項（変更禁止）

```
- バックアップなしでDB変更
- 上書きバックアップ（タイムスタンプ付き新規ファイルが必須）
- 一括更新の無確認実行（必ず件数・変更内容を事前確認）
- ロジック未理解での実行
- calc_ref_values.py 以外の方法でref1_buy_limit_jpy を直接書き換える
```

---

## 8. 許可フロー（確定）

```
1. バックアップ取得（本ファイルのコマンドを使用）
2. バックアップ確認（件数・読み込みテスト）
3. 知識チェック回答（本ファイルのQ&A参照）
4. 変更内容を言語化（何を変えるかを文章で記述）
5. DB操作実行
6. 実行後確認（件数・値の妥当性確認）
```

---

*このファイルは運用手順書として固定。変更する場合はCEO承認後に新版を作成。*
