# 2026-05-05 Phase B-1: replenish responsibility clarification
# Register Windows Task to run replenish daily at 06:00 (ASCII-only)
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_Replenish_Daily"
$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$script = "$repoRoot\ops\scheduler\orchestrator_v5.py"

$ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($ex) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task"
}

$action = New-ScheduledTaskAction -Execute $pyExe -Argument "$script --action replenish --skip-preflight" -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Phase B-1: Daily product pool replenish at 06:00" -Force | Out-Null

Write-Host "[OK] $taskName registered"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, State
