# 楽天ROOM 投稿BOT 運用開始手順書

CEO向け。Desktop A で楽天ROOM 投稿100件/日を開始するための手順。

---

## 前提（COO確認済み ✅）

| 項目 | 状態 |
|------|------|
| Python 3.12 | ✅ |
| schedule | ✅ |
| playwright | ✅ |
| Chromium ブラウザ | ✅ |
| Chrome 実行ファイル | ✅ |
| scheduler.py | ✅ |
| watchdog.py | ✅ |

---

## STEP 1: 事前チェック（1分）

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai
python preflight_check.py
```

❌ の項目を以下の手順で解消していきます。

---

## STEP 2: .env 設定（3分）

### 2-1. 楽天APIキーの準備

楽天Developers（https://webservice.rakuten.co.jp/）にログインし、
アプリIDを取得してください。

### 2-2. .env ファイル作成

```powershell
notepad C:\Users\砂田　紘幸\solarworks-ai\bots\room_bot\.env
```

以下を入力して保存:

```
RAKUTEN_APP_ID=ここにアプリIDを貼り付け
```

> ※ RAKUTEN_ACCESS_KEY は新API利用時のみ必要。旧APIが使える場合は不要。

---

## STEP 3: 投稿データ作成（10分）

### 3-1. 楽天APIで商品候補を取得

100件/日の運用には **150件以上** の候補を用意します。
（重複除外・スキップ分を見越して多めに取得）

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai\bots\room_bot

# キーワードを変えて複数回実行し、候補を蓄積
python fetch_products.py "キッチン 便利グッズ" --count 30 --output data/source_items_1.json
python fetch_products.py "収納 おしゃれ" --count 30 --output data/source_items_2.json
python fetch_products.py "コスメ 人気" --count 30 --output data/source_items_3.json
python fetch_products.py "生活家電 おすすめ" --count 30 --output data/source_items_4.json
python fetch_products.py "バッグ レディース" --count 30 --output data/source_items_5.json
python fetch_products.py "スキンケア 保湿" --count 30 --output data/source_items_6.json
```

### 3-2. 結合して source_items.json を作成

```powershell
python -c "
import json, glob
all_items = []
for f in glob.glob('data/source_items_*.json'):
    with open(f, encoding='utf-8') as fp:
        items = json.load(fp)
        if isinstance(items, list):
            all_items.extend(items)
# URL重複除去
seen = set()
unique = []
for item in all_items:
    url = item.get('url', '')
    if url and url not in seen:
        seen.add(url)
        unique.append(item)
with open('data/source_items.json', 'w', encoding='utf-8') as fp:
    json.dump(unique, fp, ensure_ascii=False, indent=2)
print(f'source_items.json: {len(unique)}件（重複除去済み）')
"
```

### 3-3. 確認

```powershell
python -c "
import json
with open('data/source_items.json', encoding='utf-8') as f:
    items = json.load(f)
print(f'商品候補: {len(items)}件')
for i, item in enumerate(items[:5]):
    print(f'  [{i+1}] {item.get(\"title\", \"\")[:50]}')
print(f'  ...')
"
```

> 100件以上あればOK。足りなければキーワードを変えて STEP 3-1 を追加実行。

---

## STEP 4: 楽天ROOMログイン（5分）

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai\bots\room_bot
python run.py login
```

1. BOT専用Chromeが開きます
2. 楽天ROOM のページで「ログイン」をクリック
3. 楽天ID/パスワードでログイン
4. ROOMのページが表示されたら
5. PowerShell に戻って **Enter** を押す

> Cookie は自動保存されます。次回以降この手順は不要です。

---

## STEP 5: テスト実行（5分）

### 5-1. 事前チェック再実行

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai
python preflight_check.py
```

> 全項目 ✅ になっていることを確認。

### 5-2. scheduler テストモード

```powershell
python scheduler.py --test
```

確認ポイント:
- `[TEST] 試験実行を開始します...` が表示される
- `room_bot 実行成功` または投稿結果が表示される
- エラーが出なければ成功

**Ctrl+C** で停止。

### 5-3. ログ確認

```powershell
type logs\scheduler.log
```

---

## STEP 6: 本番運用開始

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai
python scheduler.py
```

> 06:00 / 12:00 / 18:00 に自動実行されます。
> PowerShell ウィンドウを閉じないでください。

### watchdog も起動（別の PowerShell ウィンドウ）

```powershell
cd C:\Users\砂田　紘幸\solarworks-ai
python watchdog.py
```

---

## 運用条件まとめ

### 投稿設定

| 項目 | 値 | 設定場所 |
|------|-----|---------|
| 日次投稿数 | 90〜100件（ランダム） | `config.py` POST_DAILY_MIN/MAX |
| 投稿間隔 | 60〜180秒（ランダム） | `config.py` POST_INTERVAL_MIN/MAX |
| バッチ分割 | 50件 + 残り | `config.py` POST_BATCH_1_COUNT |
| 連続失敗停止 | 3件連続で自動停止 | `batch_runner.py` MAX_CONSECUTIVE_FAILURES |

### 06:00 / 12:00 / 18:00 の意味

- scheduler は1日3回 `run.py daily` を呼び出します
- **初回（06:00）**: source_items.json から90〜100件の計画を生成 → 投稿実行
- **2回目（12:00）**: 未投稿分があれば続行（初回で全件完了していれば0件）
- **3回目（18:00）**: 同上
- 1回で全件完了すれば、2回目以降は0件で正常終了します

### データ不足時

- source_items.json が50件しかなければ → 50件だけ投稿
- source_items.json がなければ → `planned: 0` で正常終了
- scheduler はエラーにならず次回実行を待ちます

### ログイン切れ時

- 投稿時にログインリダイレクトを検知 → 失敗として記録
- 3件連続失敗 → **自動停止**（他の投稿を無駄にしない）
- 対処: `python run.py login` で再ログイン

### 毎日の運用フロー

```
[自動] 06:00 scheduler → room_bot daily
         ├ source_items.json から計画生成（重複自動除外）
         ├ SQLiteキューに登録
         └ 1件ずつ投稿実行（60〜180秒間隔）

[自動] watchdog → scheduler.log 監視（60秒間隔）

[CEO]  必要な時だけ:
         - source_items.json の商品候補を補充
         - ログ確認: type logs\scheduler.log
```
