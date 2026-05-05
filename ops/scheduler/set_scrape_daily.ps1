# Phase C-4: Daily seed scrape (00:30)
# vm_follow_launcher.py --scrape を毎日 00:30 に自動実行し、seed_users.json を補充する
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_SeedScrape_Daily"
$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$script = "$repoRoot\ops\vm_follow_launcher.py"

$ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($ex) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task"
}

$action = New-ScheduledTaskAction -Execute $pyExe -Argument "$script --scrape" -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "00:30"
# scrape は 30-60分かかる可能性
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Phase C-4: Daily seed users scrape at 00:30" -Force | Out-Null

Write-Host "[OK] $taskName registered"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, State
