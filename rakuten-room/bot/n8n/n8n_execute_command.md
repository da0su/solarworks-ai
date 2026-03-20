# Execute Command ノード設定

## Windows環境

### Command
```
python "C:\Users\砂田　紘幸\Box\会長\39.AI-COMPANY\08_AUTOMATION\room_bot_v2\run.py" batch --file "{{ $json.filePath }}" --count 10
```

### n8n の Execute Command ノード設定
- **Execute**: Command
- **Command**: 上記のコマンド
- **Timeout**: 600（10件 × 60秒間隔 = 最大600秒）

## 注意
- `{{ $json.filePath }}` は前のCode nodeが出力したJSONファイルパスを参照
- Pythonのパスが通っていない場合はフルパスで指定:
  `C:\Users\砂田　紘幸\AppData\Local\Programs\Python\Python312\python.exe`
- Timeout は投稿件数 × 180秒（最大間隔）を目安に設定
