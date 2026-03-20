# ROOM BOT v5.0 - Windows タスクスケジューラ登録（完全自動運用）
# 管理者権限で実行してください
# PowerShell: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$botRoot = Split-Path -Parent $PSScriptRoot

Write-Host "============================================================"
Write-Host "  ROOM BOT v5.0 - 完全自動運用 スケジューラ セットアップ"
Write-Host "  BOT Root: $botRoot"
Write-Host "============================================================"

# --- 旧タスク削除 ---
$oldTasks = @("ROOM_BOT_Plan", "ROOM_BOT_Execute_AM", "ROOM_BOT_Execute_PM")
foreach ($t in $oldTasks) {
    $existing = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "[DELETE] 旧タスク $t を削除"
    }
}

# --- タスク1: Batch 1 (毎日 0:00) - 補充+計画+実行50件 ---
$taskName1 = "ROOM_BOT_Auto_Batch1"
$action1 = New-ScheduledTaskAction `
    -Execute "$botRoot\scripts\scheduler_auto.bat" `
    -Argument "1" `
    -WorkingDirectory $botRoot
$trigger1 = New-ScheduledTaskTrigger -Daily -At "00:00"
$settings1 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $taskName1 `
    -Action $action1 `
    -Trigger $trigger1 `
    -Settings $settings1 `
    -Description "ROOM BOT: Batch1 - 補充+計画+実行(50件)" `
    -Force

Write-Host "[OK] $taskName1 (毎日 0:00)"

# --- タスク2: Batch 2 (毎日 8:00) - 残り実行 ---
$taskName2 = "ROOM_BOT_Auto_Batch2"
$action2 = New-ScheduledTaskAction `
    -Execute "$botRoot\scripts\scheduler_auto.bat" `
    -Argument "2" `
    -WorkingDirectory $botRoot
$trigger2 = New-ScheduledTaskTrigger -Daily -At "08:00"
$settings2 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $taskName2 `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings2 `
    -Description "ROOM BOT: Batch2 - 残り実行" `
    -Force

Write-Host "[OK] $taskName2 (毎日 8:00)"

# --- タスク3: 夜レポート (毎日 23:00) - 日報+明日計画+Slack ---
$taskName3 = "ROOM_BOT_NightReport"
$action3 = New-ScheduledTaskAction `
    -Execute "$botRoot\scripts\scheduler_report.bat" `
    -Argument "night" `
    -WorkingDirectory $botRoot
$trigger3 = New-ScheduledTaskTrigger -Daily -At "23:00"
$settings3 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $taskName3 `
    -Action $action3 `
    -Trigger $trigger3 `
    -Settings $settings3 `
    -Description "ROOM BOT: 夜レポート (日報+明日計画) → Slack" `
    -Force

Write-Host "[OK] $taskName3 (毎日 23:00)"

# --- タスク4: 朝レポート (毎日 9:00) - 投稿結果+Slack ---
$taskName4 = "ROOM_BOT_MorningReport"
$action4 = New-ScheduledTaskAction `
    -Execute "$botRoot\scripts\scheduler_report.bat" `
    -Argument "morning" `
    -WorkingDirectory $botRoot
$trigger4 = New-ScheduledTaskTrigger -Daily -At "09:00"
$settings4 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $taskName4 `
    -Action $action4 `
    -Trigger $trigger4 `
    -Settings $settings4 `
    -Description "ROOM BOT: 朝レポート (投稿結果) → Slack" `
    -Force

Write-Host "[OK] $taskName4 (毎日 9:00)"

Write-Host ""
Write-Host "============================================================"
Write-Host "  セットアップ完了!"
Write-Host ""
Write-Host "  登録タスク:"
Write-Host "    1. $taskName1   -> 毎日 0:00 (補充+計画+実行50件)"
Write-Host "    2. $taskName2   -> 毎日 8:00 (残り実行)"
Write-Host "    3. $taskName3  -> 毎日 23:00 (日報+計画 -> Slack)"
Write-Host "    4. $taskName4  -> 毎日 9:00 (朝レポート -> Slack)"
Write-Host ""
Write-Host "  1日の流れ:"
Write-Host "    0:00 Batch1: プール補充 -> 計画生成 -> 50件投稿"
Write-Host "    8:00 Batch2: 残り40-50件投稿"
Write-Host "    9:00 朝レポート -> Slack (CEOが確認)"
Write-Host "    23:00 夜レポート -> Slack (日報+明日計画)"
Write-Host ""
Write-Host "  確認:  Get-ScheduledTask | Where-Object TaskName -like 'ROOM_BOT*'"
Write-Host "  削除:  Unregister-ScheduledTask -TaskName 'ROOM_BOT_Auto_Batch1' -Confirm:`$false"
Write-Host "============================================================"
