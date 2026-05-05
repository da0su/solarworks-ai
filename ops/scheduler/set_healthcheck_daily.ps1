# Phase B-5: Task Scheduler healthcheck daily
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_TaskHealthcheck_Daily"
$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$script = "$repoRoot\ops\scheduler\healthcheck_tasks.py"

$ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($ex) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task"
}

$action = New-ScheduledTaskAction -Execute $pyExe -Argument "$script" -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "00:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Phase B-5: Daily Task Scheduler healthcheck (missing tasks alert)" -Force | Out-Null

Write-Host "[OK] $taskName registered"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, State
