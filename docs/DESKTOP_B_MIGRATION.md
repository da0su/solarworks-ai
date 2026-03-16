# Desktop A → Desktop B 移行手順書

Desktop A（開発機）から Desktop B（本番サーバー）への移行手順。

---

## 移行概要

```
Desktop A（開発・テスト）
  ↓ git push
GitHub（da0su/solarworks-ai）
  ↓ git clone / pull
Desktop B（24時間稼働サーバー）
```

---

## 前提

- Desktop B のセットアップが完了していること（→ DESKTOP_B_SETUP.md）
- Desktop A で最新コードが git push 済みであること

---

## 移行手順

### Phase 1: Desktop A 側の準備

#### 1-1. 最新状態を push
```powershell
# Desktop A で実行
cd C:\Users\砂田　紘幸\solarworks-ai
git add -A
git commit -m "Desktop B 移行準備"
git push origin main
```

#### 1-2. 環境変数・.env をメモ
以下の値を Desktop B に転送する準備:
```
bots/room_bot/.env
  - RAKUTEN_APP_ID
  - RAKUTEN_ACCESS_KEY

bots/slack/.env
  - SLACK_BOT_TOKEN
  - SLACK_APP_TOKEN

システム環境変数
  - SOLARWORKS_SLACK_WEBHOOK（設定済みの場合）
```

> ⚠ .env ファイルは .gitignore で除外されているため Git経由で転送されない。
> 手動コピーまたは安全な方法で転送すること。

#### 1-3. Chrome プロファイルのバックアップ（room_bot用）
```powershell
# Desktop A の chrome_profile をコピー
# bots/room_bot/data/chrome_profile/ フォルダをUSB等で転送
```

---

### Phase 2: Desktop B 側の作業

#### 2-1. リポジトリ取得
```powershell
# 初回
cd C:\solarworks
git clone https://github.com/da0su/solarworks-ai.git
cd solarworks-ai

# 2回目以降
cd C:\solarworks\solarworks-ai
git pull origin main
```

#### 2-2. 依存パッケージインストール
```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

#### 2-3. .env ファイル配置
```powershell
# Desktop A からコピーした内容を配置
notepad bots\room_bot\.env
notepad bots\slack\.env
```

#### 2-4. Chrome プロファイル配置（room_bot用）
```powershell
# Desktop A からコピーした chrome_profile を配置
# → bots\room_bot\data\chrome_profile\
```

#### 2-5. config.py の確認
```powershell
# Chrome実行パスが Desktop B と一致するか確認
notepad bots\room_bot\config.py
# CHROME_EXECUTABLE_PATH のパスを Desktop B に合わせる
```

---

### Phase 3: 動作確認

#### 3-1. scheduler テスト
```powershell
cd C:\solarworks\solarworks-ai
python scheduler.py --test
# 成功を確認 → Ctrl+C
```

#### 3-2. watchdog テスト
```powershell
python watchdog.py
# 起動確認 → Ctrl+C
```

#### 3-3. ログ確認
```powershell
type logs\scheduler.log
type logs\watchdog.log
```

---

### Phase 4: 自動起動設定

#### 方法 A: Windows タスクスケジューラ（推奨）

最もシンプル。Windows標準機能のみで実現。

##### scheduler の登録
```powershell
# PowerShell（管理者）で実行
$action = New-ScheduledTaskAction `
    -Execute "C:\solarworks\solarworks-ai\venv\Scripts\python.exe" `
    -Argument "scheduler.py" `
    -WorkingDirectory "C:\solarworks\solarworks-ai"

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "SolarWorks-Scheduler" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Solar Works BOT スケジューラー" `
    -RunLevel Highest
```

##### watchdog の登録
```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\solarworks\solarworks-ai\venv\Scripts\python.exe" `
    -Argument "watchdog.py" `
    -WorkingDirectory "C:\solarworks\solarworks-ai"

$trigger = New-ScheduledTaskTrigger -AtStartup -RandomDelay (New-TimeSpan -Seconds 30)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "SolarWorks-Watchdog" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Solar Works スケジューラー監視" `
    -RunLevel Highest
```

**利点**: Windows標準、追加インストール不要、GUI管理可能
**注意**: ログイン不要で実行するには「ユーザーがログオンしているかに関わらず実行」を選択

---

#### 方法 B: NSSM（Non-Sucking Service Manager）

Python スクリプトを Windows サービスとして登録する方法。

##### インストール
```powershell
# winget でインストール
winget install nssm

# または https://nssm.cc/download からダウンロード
```

##### scheduler のサービス登録
```powershell
nssm install SolarWorks-Scheduler "python.exe" "C:\solarworks\solarworks-ai\scheduler.py"
nssm set SolarWorks-Scheduler AppDirectory "C:\solarworks\solarworks-ai"
nssm set SolarWorks-Scheduler AppStdout "C:\solarworks\solarworks-ai\logs\scheduler_service.log"
nssm set SolarWorks-Scheduler AppStderr "C:\solarworks\solarworks-ai\logs\scheduler_service_err.log"
nssm set SolarWorks-Scheduler AppRestartDelay 5000
nssm start SolarWorks-Scheduler
```

##### watchdog のサービス登録
```powershell
nssm install SolarWorks-Watchdog "python.exe" "C:\solarworks\solarworks-ai\watchdog.py"
nssm set SolarWorks-Watchdog AppDirectory "C:\solarworks\solarworks-ai"
nssm set SolarWorks-Watchdog AppStdout "C:\solarworks\solarworks-ai\logs\watchdog_service.log"
nssm set SolarWorks-Watchdog AppStderr "C:\solarworks\solarworks-ai\logs\watchdog_service_err.log"
nssm set SolarWorks-Watchdog AppRestartDelay 5000
nssm start SolarWorks-Watchdog
```

**利点**: 自動再起動、サービス管理、ログ分離
**注意**: room_bot は GUI（Chrome）を使うため、サービスモードではヘッドレス設定が必要

---

#### 方法 C: スタートアップバッチ（最もシンプル）

```powershell
# start_solarworks.bat を作成
notepad C:\solarworks\start_solarworks.bat
```

内容:
```bat
@echo off
echo Starting Solar Works...

cd /d C:\solarworks\solarworks-ai

echo Starting Scheduler...
start "SolarWorks-Scheduler" python scheduler.py

timeout /t 5 /nobreak >nul

echo Starting Watchdog...
start "SolarWorks-Watchdog" python watchdog.py

echo Solar Works started.
```

スタートアップフォルダに配置:
```powershell
# ショートカットをスタートアップに配置
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SolarWorks.lnk")
$Shortcut.TargetPath = "C:\solarworks\start_solarworks.bat"
$Shortcut.WorkingDirectory = "C:\solarworks\solarworks-ai"
$Shortcut.Save()
```

**利点**: 最もシンプル、bat 1つで管理
**注意**: ユーザーログインが必要

---

### 自動起動方法の比較

| 方法 | 難易度 | 自動再起動 | ログイン不要 | GUI対応 |
|------|--------|-----------|-------------|---------|
| **タスクスケジューラ** | ★★☆ | ○（設定で可） | ○ | △ |
| **NSSM** | ★★★ | ○（自動） | ○ | × |
| **スタートアップbat** | ★☆☆ | × | × | ○ |

**COO推奨**: room_bot が Chrome GUI を使用するため、**方法 C（スタートアップbat）** で開始し、安定稼働を確認後に **方法 A（タスクスケジューラ）** へ移行。

---

### Phase 5: 切り替え

#### 5-1. Desktop A の scheduler を停止
```powershell
# Desktop A で Ctrl+C
```

#### 5-2. Desktop B の scheduler を起動
```powershell
# Desktop B で起動
cd C:\solarworks\solarworks-ai
python scheduler.py
```

#### 5-3. Desktop B の watchdog を起動
```powershell
python watchdog.py
```

#### 5-4. 初回定時実行を確認
```
翌日の 06:00 に scheduler.log を確認
→ room_bot が正常実行されていれば移行完了
```

---

## 移行チェックリスト

### Desktop A 側
- [ ] 最新コードを git push
- [ ] .env の値をメモ
- [ ] chrome_profile をバックアップ
- [ ] scheduler を停止

### Desktop B 側
- [ ] git clone 完了
- [ ] pip install 完了
- [ ] playwright install 完了
- [ ] .env 配置
- [ ] chrome_profile 配置
- [ ] config.py の Chrome パス確認
- [ ] scheduler --test 成功
- [ ] watchdog 起動確認
- [ ] 自動起動設定完了
- [ ] 初回定時実行成功を確認

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `ModuleNotFoundError` | pip install 未実行 | `pip install -r requirements.txt` |
| `playwright` エラー | ブラウザ未インストール | `python -m playwright install chromium` |
| room_bot ログイン失敗 | chrome_profile 未コピー | Desktop A から chrome_profile をコピー |
| scheduler.log 更新なし | scheduler 未起動 | `python scheduler.py` で起動確認 |
| Chrome パスエラー | Desktop B の Chrome 位置が違う | config.py の `CHROME_EXECUTABLE_PATH` を修正 |
