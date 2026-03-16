# ROOM BOT v2 - MVP

楽天ROOMに1件の投稿を自動実行するボット。

---

## セットアップ手順

### 1. Pythonの確認
```
python --version
```
Python 3.11以上が必要です。未インストールの場合は https://www.python.org/ からインストールしてください。

### 2. 依存パッケージのインストール
```
cd 08_AUTOMATION/room_bot_v2
pip install -r requirements.txt
```

### 3. Playwrightブラウザのインストール
```
playwright install chromium
```

### 4. 初回ログイン（セッション保存）
```
python run.py login
```
- ブラウザが開きます
- 楽天IDでログインしてください
- ログイン完了後、ターミナルに戻って Enter を押してください
- セッション（cookie）が `data/state/storage_state.json` に保存されます
- **この操作は初回のみ。以降はセッションが再利用されます。**

---

## 投稿の実行

### テキスト直接指定
```
python run.py post --url "https://item.rakuten.co.jp/attenir/166011/" --text "夕方の肌のくすみ、地味に気になってた😌

クレンジングって「落とすだけ」って
思ってたけど、これ使ってから
洗い上がりの肌の感じが全然違う...

#スキンケア
#クレンジング
#アテニア
#大人美容"
```

### ファイルから投稿文を読み込む
```
python run.py post --url "https://item.rakuten.co.jp/attenir/166011/" --file "review.txt"
```

---

## 動作フロー

```
1. ブラウザ起動（保存済みセッションを復元）
2. 楽天ROOMにアクセスしてログイン状態を確認
   └─ 未ログイン → 停止してエラー表示
3. 指定した楽天商品ページを開く
4. 「コレ!」ボタンをクリック
5. 投稿編集画面でレビュー文を入力
6. 「投稿する」ボタンをクリック
7. 投稿成功を確認
8. ログ・スクリーンショットを保存
```

---

## ログとスクリーンショット

| 出力 | 場所 |
|------|------|
| 実行ログ | `data/logs/YYYY-MM-DD.log` |
| スクリーンショット | `data/screenshots/YYYY-MM-DD/` |
| 投稿履歴 | `05_CONTENT/rakuten_room/history/POST_LOG.json` |

スクリーンショットは投稿の各ステップで自動保存されます。
問題が起きた場合、スクリーンショットを確認すればどこで失敗したか分かります。

---

## トラブルシューティング

### 「ログインされていません」と表示される
→ `python run.py login` を実行して再ログインしてください。

### 「コレ!」ボタンが見つからない
→ 商品URLが正しいか確認してください。楽天市場の商品ページURL（`item.rakuten.co.jp/...`）を指定してください。
→ 楽天ROOMに対応していない商品の可能性があります。

### 投稿ボタンが見つからない
→ `executor/selectors.py` のセレクターが古くなっている可能性があります。スクリーンショットを確認して、セレクターを更新してください。

### その他のエラー
→ `data/logs/` のログファイルと `data/screenshots/` のスクリーンショットを確認してください。

---

## ファイル構成

```
room_bot_v2/
├── run.py              ← 実行コマンド
├── config.py           ← 設定
├── requirements.txt    ← 依存パッケージ
├── README.md           ← このファイル
├── executor/
│   ├── browser_manager.py  ← ブラウザ管理・セッション
│   ├── post_executor.py    ← 投稿実行ロジック
│   └── selectors.py        ← DOM要素のセレクター定義
├── logger/
│   └── logger.py           ← ログ・スクリーンショット管理
└── data/
    ├── logs/               ← 実行ログ
    ├── screenshots/        ← スクリーンショット
    └── state/              ← セッションデータ
```
