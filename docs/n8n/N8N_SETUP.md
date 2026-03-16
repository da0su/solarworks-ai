# n8n → room_bot_v2 連携ガイド

## ワークフロー構成（最小構成）

```
[楽天API] → [Code: payload整形] → [Code: JSON書出し] → [Execute Command: batch実行]
```

### 各ノードの役割

| # | ノード | タイプ | 役割 |
|---|--------|--------|------|
| 1 | 楽天API | HTTP Request | 楽天市場APIから商品データ取得 |
| 2 | payload整形 | Code | title/url/image/comment の4項目に絞る |
| 3 | JSON書出し | Code | 一時ファイルに保存 |
| 4 | batch実行 | Execute Command | `python run.py batch` を実行 |

---

## ノード設定

### ノード1: 楽天API（HTTP Request）
- Method: GET
- URL: `https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601`
- Query Parameters:
  - applicationId: (楽天APIキー)
  - keyword: (検索キーワード)
  - hits: 10

### ノード2: payload整形（Code node）
→ `n8n_code_payload.js` を貼り付け

### ノード3: JSON書出し（Code node）
→ `n8n_code_write_json.js` を貼り付け

### ノード4: Execute Command
- Command:
  - Windows: `python "C:\Users\砂田　紘幸\Box\会長\39.AI-COMPANY\08_AUTOMATION\room_bot_v2\run.py" batch --file "C:\Users\砂田　紘幸\AppData\Local\Temp\room_posts.json" --count 10`
  - Mac/Linux: `python /path/to/room_bot_v2/run.py batch --file /tmp/room_posts.json --count 10`

---

## 20MB エラーを避けるための注意点

1. **楽天APIの hits は最大10件にする**（30件だとdescription等で肥大化）
2. **payload整形 Code node は必ずAPIノード直後に置く**
3. **Code node 間で Binary Data を渡さない**（Send Binary Data = OFF）
4. **楽天APIレスポンスの不要フィールドを捨てる**（整形後は4項目のみ）
5. **image は URL文字列のみ**（base64変換しない）
6. **1ワークフロー内でノード間を流れるデータは常に最小**

---

## --stdin モード（ファイル不要）

ファイル書出しを省略して直接パイプで渡す：

```
[Code: payload整形] → [Execute Command]
  echo '${JSON}' | python run.py batch --stdin --count 10
```

→ run.py の --stdin オプションで対応（後述）

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| Request too large (max 20MB) | ノード間データが大きすぎる | payload整形 Code node を確認 |
| セッション切れ | cookie期限切れ | `python run.py login` で再ログイン |
| 投稿0件成功 | セレクター変更 | selectors.py を更新 |
| Execute Command タイムアウト | 投稿件数が多い | --count を減らす / n8nのtimeoutを延長 |
