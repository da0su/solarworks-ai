# Desktop B セットアップ手順書

Solar Works 本番サーバー（Desktop B）の初期セットアップ手順。

---

## 前提条件

| 項目 | 要件 |
|------|------|
| OS | Windows 10 / 11 |
| ネットワーク | インターネット常時接続 |
| 用途 | BOT 24時間稼働サーバー |
| GitHub | https://github.com/da0su/solarworks-ai.git にアクセス可能 |

---

## STEP 1: Windows 初期設定

### 1-1. Windows Update
```
設定 → Windows Update → すべてインストール → 再起動
```

### 1-2. 電源設定（スリープ無効化）
```
設定 → システム → 電源 → 画面とスリープ
  - 画面の電源を切る: なし
  - スリープ: なし
```

### 1-3. 自動更新による再起動の制御
```
設定 → Windows Update → 詳細オプション
  - アクティブ時間: 手動設定 → 0:00〜23:59
```

### 1-4. リモートデスクトップ有効化（任意）
```
設定 → システム → リモートデスクトップ → 有効
```

---

## STEP 2: Python インストール

### 2-1. ダウンロード
```
https://www.python.org/downloads/
→ Python 3.12.x（最新安定版）をダウンロード
```

### 2-2. インストール
```
- [x] Add Python to PATH ← 必ずチェック
- Install Now
```

### 2-3. 確認
```powershell
python --version
# Python 3.12.x

pip --version
# pip 24.x.x
```

---

## STEP 3: Git インストール

### 3-1. ダウンロード
```
https://git-scm.com/download/win
→ 64-bit Git for Windows Setup
```

### 3-2. インストール（デフォルト設定でOK）

### 3-3. 確認
```powershell
git --version
# git version 2.x.x
```

### 3-4. Git 初期設定
```powershell
git config --global user.name "solarworks-bot"
git config --global user.email "bot@solarworks.local"
```

---

## STEP 4: リポジトリ clone

### 4-1. 作業ディレクトリ作成
```powershell
mkdir C:\solarworks
cd C:\solarworks
```

### 4-2. clone
```powershell
git clone https://github.com/da0su/solarworks-ai.git
cd solarworks-ai
```

### 4-3. 確認
```powershell
dir
# scheduler.py, watchdog.py, bots/, docs/, logs/ 等が表示されること
```

---

## STEP 5: Python 依存パッケージ

### 5-1. pip インストール
```powershell
cd C:\solarworks\solarworks-ai
pip install -r requirements.txt
```

### 5-2. Playwright ブラウザインストール（room_bot用）
```powershell
python -m playwright install chromium
```

### 5-3. 確認
```powershell
pip list | findstr "schedule playwright slack"
# schedule        1.2.x
# playwright      1.4x.x
# slack-bolt      1.1x.x
# slack-sdk       3.2x.x
```

---

## STEP 6: 環境変数の設定

### 6-1. room_bot 用 .env
```powershell
# bots/room_bot/.env を作成
notepad C:\solarworks\solarworks-ai\bots\room_bot\.env
```

内容:
```
RAKUTEN_APP_ID=（楽天APIキー）
RAKUTEN_ACCESS_KEY=（楽天アクセスキー）
```

### 6-2. Slack BOT 用 .env
```powershell
notepad C:\solarworks\solarworks-ai\bots\slack\.env
```

内容:
```
SLACK_BOT_TOKEN=xoxb-（SlackボットトークンをDesktop Aからコピー）
SLACK_APP_TOKEN=xapp-（Slackアプリトークン）
```

### 6-3. Watchdog Slack通知（任意）
```powershell
# システム環境変数に追加
[Environment]::SetEnvironmentVariable("SOLARWORKS_SLACK_WEBHOOK", "https://hooks.slack.com/services/xxx", "User")
```

---

## STEP 7: 動作確認

### 7-1. scheduler テスト
```powershell
cd C:\solarworks\solarworks-ai
python scheduler.py --test
# [TEST] モードで起動 → room_bot 試験実行 → 成功を確認 → Ctrl+C
```

### 7-2. watchdog テスト
```powershell
python watchdog.py
# 起動ログが表示されること → Ctrl+C
```

### 7-3. ログ確認
```powershell
type logs\scheduler.log
type logs\watchdog.log
```

---

## STEP 8: Claude Code インストール（任意）

COO（Claude Code）による直接管理が必要な場合:

### 8-1. Node.js インストール
```
https://nodejs.org/ → LTS版をインストール
```

### 8-2. Claude Code インストール
```powershell
npm install -g @anthropic-ai/claude-code
```

### 8-3. 確認
```powershell
claude --version
```

---

## チェックリスト

セットアップ完了時に全項目を確認:

- [ ] Windows Update 完了
- [ ] スリープ無効化
- [ ] Python 3.12+ インストール済み
- [ ] Git インストール済み
- [ ] リポジトリ clone 済み
- [ ] pip install -r requirements.txt 完了
- [ ] playwright install chromium 完了
- [ ] .env ファイル設定済み
- [ ] scheduler --test 成功
- [ ] watchdog 起動確認
