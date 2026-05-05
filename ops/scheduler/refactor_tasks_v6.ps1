# Plan v4 P1: 既存 RoomBot Task を vm_controller 経由に refactor
# 旧: HOST 上で各 executor を実行
# 新: HOST が vm_controller HTTP で VM 内 runner を起動
$ErrorActionPreference = "Stop"

$pyExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$repoRoot = "C:\Users\infoa\Documents\solarworks-ai"
$ctrl = "$repoRoot\ops\vm_v6\vm_controller.py"

# refactor 対象 task と新 arguments
$mapping = @(
    @{Name="RoomBot_POST_Batch1";       Args="$ctrl --mode post --limit 50 --batch 1"; ExistingTime="09:00"}
    @{Name="RoomBot_POST_Batch2";       Args="$ctrl --mode post --limit 50 --batch 2"; ExistingTime="15:00"}
    @{Name="RoomBot_POST_Batch3";       Args="$ctrl --mode post --limit 50 --batch 3"; ExistingTime="21:00"}
    @{Name="RoomBot_LIKE_Hourly";       Args="$ctrl --mode like --limit 100"; ExistingTime=""}
    @{Name="RoomBot_FOLLOWBACK_Hourly"; Args="$ctrl --mode followback --limit 30"; ExistingTime=""}
    @{Name="RoomBotFollow_Hourly";      Args="$ctrl --mode follow --limit 200 --force"; ExistingTime=""}
)

Write-Host "============================================================"
Write-Host "  RoomBot Task v6 Refactor"
Write-Host "  Old: HOST executor / New: vm_controller via HTTP"
Write-Host "============================================================"
Write-Host ""

foreach ($m in $mapping) {
    $name = $m.Name
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "[skip] $name not found"
        continue
    }

    # 既存 trigger を保持
    $triggers = $task.Triggers
    $oldState = $task.State

    # 新 action
    $newAction = New-ScheduledTaskAction -Execute $pyExe -Argument $m.Args -WorkingDirectory $repoRoot

    # settings (cmd window 抑制)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 60) -MultipleInstances IgnoreNew -Hidden

    # task を re-register
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Register-ScheduledTask -TaskName $name -Action $newAction -Trigger $triggers -Settings $settings -Description "Plan v4 P1: vm_controller -> VM HTTP server" -Force | Out-Null

    Write-Host "[ok] $name refactored to vm_controller"
}

Write-Host ""
Write-Host "[Note] All tasks remain disabled. Re-enable manually after VM HTTP server confirmed alive."
