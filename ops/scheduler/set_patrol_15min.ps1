# 2026-05-05 Phase 2-3: パトロール毎時30分→15分間隔へ変更
# RoomBot_Patrol_Hourly タスクを 15分ごと実行に修正
$ErrorActionPreference = "Stop"

$taskName = "RoomBot_Patrol_Hourly"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Error "Task '$taskName' not found"
    exit 1
}

# 既存トリガー保持: StartBoundary は維持して、RepetitionInterval を15分に
$trigger = New-ScheduledTaskTrigger -Once -At "00:00:00" `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# StartBoundary を翌0時に設定（毎日0,15,30,45分に発火）

Set-ScheduledTask -TaskName $taskName -Trigger $trigger

Write-Host "Updated $taskName to 15-minute interval"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, State
