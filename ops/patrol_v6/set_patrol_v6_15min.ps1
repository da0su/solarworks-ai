# Plan v4 P2: patrol_v6 を 15分間隔で登録
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_Patrol_v6"
$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$module = "ops.patrol_v6.patrol_orchestrator"

$ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($ex) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing $taskName"
}

# 2026-05-07 P0-2 (Plan v5): --auto-recover を明示指定し L4/L5/L6 CRITICAL 時に
# auto_recover (vm_startvm/session_abort/escalate_ceo) を確実に実行させる
$action = New-ScheduledTaskAction -Execute $pyExe -Argument "-m $module --auto-recover" -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -Once -At "00:00:00" -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Plan v4 P2: patrol_v6 8 Layer multi-detection (15min interval)" -Force | Out-Null

Write-Host "[OK] $taskName registered (15min interval)"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, State
