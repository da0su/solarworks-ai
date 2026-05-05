# Phase C-6: CEO Slack dashboard (07:00 / 12:00 / 21:00)
$ErrorActionPreference = "Stop"

$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$script = "$repoRoot\ops\notifications\dashboard_report.py"

$tasks = @(
    @{Name="RoomBot_Dashboard_Morning"; Time="07:00"; Mode="morning"},
    @{Name="RoomBot_Dashboard_Noon";    Time="12:00"; Mode="noon"},
    @{Name="RoomBot_Dashboard_Night";   Time="21:00"; Mode="night"}
)

foreach ($t in $tasks) {
    $taskName = $t.Name
    $ex = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($ex) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed existing $taskName"
    }
    $action = New-ScheduledTaskAction -Execute $pyExe -Argument "$script --mode $($t.Mode)" -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.Time
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Phase C-6: CEO daily dashboard - $($t.Mode) at $($t.Time)" -Force | Out-Null
    Write-Host "[OK] $taskName registered ($($t.Time) $($t.Mode))"
}

Get-ScheduledTask | Where-Object {$_.TaskName -like "RoomBot_Dashboard*"} | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime, LastRunTime, State -AutoSize
