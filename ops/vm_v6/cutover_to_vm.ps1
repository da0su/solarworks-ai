# Plan v6 Phase C: HOST Task Scheduler を vm_controller 経由に切替.
#
# CEO 指示「全てをVB内で実装する形なはず」に対する Phase C cutover:
# 旧 ops\orchestrator_run.bat → 新 ops\orchestrator_run_vm.bat に Action 切替。
# 切替対象: POST batch1-4 / LIKE / FOLLOWBACK の 6 task。
# FOLLOW は vm_follow_launcher.bat で既に VM 経由なので変更不要。
#
# 実行前提: VM HTTP server (port 18765) が応答していること。
# このスクリプトは VM 疎通確認 → 全 task の Action 書換 → state ファイル更新まで自律実行。
#
# 旧 Action は state\scheduler_pre_vm_cutover.json に保存し、`-Revert` で復元可能。

param(
    [switch]$Revert,
    [switch]$DryRun,
    [switch]$SkipHealthCheck
)

$ErrorActionPreference = "Stop"
$root = "C:\Users\infoa\Documents\solarworks-ai"
$stateFile = Join-Path $root "state\scheduler_pre_vm_cutover.json"

# 切替対象
$cutoverTasks = @(
    @{ Name = "RoomBot_POST_Batch1";       Args = "post --batch 1" }
    @{ Name = "RoomBot_POST_Batch2";       Args = "post --batch 2" }
    @{ Name = "RoomBot_POST_Batch3";       Args = "post --batch 3" }
    @{ Name = "RoomBot_POST_Batch4";       Args = "post --batch 1" }
    @{ Name = "RoomBot_LIKE_Hourly";       Args = "like" }
    @{ Name = "RoomBot_FOLLOWBACK_Hourly"; Args = "followback" }
)

$oldBat = "$root\ops\orchestrator_run.bat"
$newBat = "$root\ops\orchestrator_run_vm.bat"

function Test-VMHttp {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:18765/healthz" -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if ($Revert) {
    Write-Host "=== Phase C REVERT: vm_controller → orchestrator_run.bat ==="
    if (-not (Test-Path $stateFile)) {
        Write-Error "state file 不在: $stateFile (revert 不能)"
        exit 1
    }
    $saved = Get-Content $stateFile -Raw | ConvertFrom-Json
    foreach ($t in $saved.tasks) {
        Write-Host "  Reverting $($t.name): Execute=$($t.execute) Arguments=$($t.arguments)"
        if (-not $DryRun) {
            $action = New-ScheduledTaskAction -Execute $t.execute -Argument $t.arguments -WorkingDirectory $t.cwd
            Set-ScheduledTask -TaskName $t.name -Action $action | Out-Null
        }
    }
    Write-Host "[OK] reverted $($saved.tasks.Count) tasks"
    exit 0
}

# Forward cutover
Write-Host "=== Phase C CUTOVER: orchestrator_run.bat → orchestrator_run_vm.bat ==="

if (-not $SkipHealthCheck) {
    Write-Host "[1/3] VM HTTP server health check (port 18765)..."
    if (-not (Test-VMHttp)) {
        Write-Error "VM HTTP server (localhost:18765) 応答なし → cutover 中止. -SkipHealthCheck で強行可能"
        exit 2
    }
    Write-Host "  OK"
}

# 旧 state 保存
Write-Host "[2/3] 旧 Action を $stateFile に保存"
$savedTasks = @()
foreach ($cfg in $cutoverTasks) {
    $existing = Get-ScheduledTask -TaskName $cfg.Name -ErrorAction SilentlyContinue
    if (-not $existing) { Write-Warning "task not found: $($cfg.Name)"; continue }
    $a = $existing.Actions | Select-Object -First 1
    $savedTasks += [PSCustomObject]@{
        name      = $cfg.Name
        execute   = $a.Execute
        arguments = $a.Arguments
        cwd       = $a.WorkingDirectory
    }
}
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Split-Path $stateFile) | Out-Null
    @{ saved_at = (Get-Date).ToString("o"); tasks = $savedTasks } | ConvertTo-Json -Depth 5 | Out-File $stateFile -Encoding utf8
    Write-Host "  saved $($savedTasks.Count) tasks"
}

# 切替
Write-Host "[3/3] Action 書換"
foreach ($cfg in $cutoverTasks) {
    $existing = Get-ScheduledTask -TaskName $cfg.Name -ErrorAction SilentlyContinue
    if (-not $existing) { continue }
    Write-Host "  $($cfg.Name): $oldBat → $newBat / Args=$($cfg.Args)"
    if (-not $DryRun) {
        $action = New-ScheduledTaskAction -Execute $newBat -Argument $cfg.Args -WorkingDirectory $root
        Set-ScheduledTask -TaskName $cfg.Name -Action $action | Out-Null
    }
}

Write-Host "[OK] cutover 完了 (revert: cutover_to_vm.ps1 -Revert)"
