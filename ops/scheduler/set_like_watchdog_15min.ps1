# 2026-05-28 Plan v5 P1: LIKE Watchdog Task Scheduler 登録
# 15分ごとに like_watchdog.py を実行して LIKE セッション hung を自動復旧する

$ErrorActionPreference = "Stop"

$taskName   = "RoomBot_LIKE_Watchdog"
$batPath    = "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\wrap_RoomBot_LIKE_Watchdog.bat"
$logPath    = "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_like_watchdog.log"

# ログファイルの親ディレクトリを確保
$logDir = Split-Path $logPath
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

# 既存タスクがあれば削除
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName"
}

# トリガー: 毎日0:00から15分ごと (00/15/30/45分)
$trigger = New-ScheduledTaskTrigger -Once -At "00:00:00" `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# アクション: bat ファイルを cmd.exe で実行
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$batPath`""

# 設定: 既実行中でもスキップ (複数重複実行防止)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

# ユーザー: ログオン中ユーザーで実行
$principal = New-ScheduledTaskPrincipal `
    -UserId "CYBER\infoa" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName  $taskName `
    -Trigger   $trigger `
    -Action    $action `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Registered: $taskName"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo |
    Format-List TaskName, NextRunTime, LastRunTime, LastTaskResult
