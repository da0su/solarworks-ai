# 2026-05-06 CEO指示: 24時間稼働ルール - POST 深夜 03:00 batch 追加
# 既存 Batch1 09:00 / Batch2 15:00 / Batch3 21:00 に加え Batch4 03:00 で 6h 間隔の 4 batch 化
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_POST_Batch4"
$batPath = "C:\Users\infoa\Documents\solarworks-ai\ops\orchestrator_run.bat"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"

$ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($ex) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task"
}

# Action: orchestrator_run.bat post --batch 1 (Batch1/2/3 と同じパターン)
$action = New-ScheduledTaskAction -Execute $batPath -Argument "post --batch 1" -WorkingDirectory $repoRoot

# Trigger: 毎日 03:00
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "24h operation: POST deep-night batch 03:00 (CEO 2026-05-06)" -Force | Out-Null

Write-Host "[OK] $taskName registered (Daily 03:00)"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, State
