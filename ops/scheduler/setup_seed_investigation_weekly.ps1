# RoomBot_SeedInvestigation_Weekly Task Scheduler 設定
# CEO 2026-05-10 承認: investigation の最新化を週次で再実行
#
# 実行: 毎週日曜 04:00 (低トラフィック時間帯)
# 内容: investigate_seeds.py で 447 seed の follower_count / my_status を再取得
# 出力: state/seed_investigation.json + spreadsheet 06_フォロワー調査 タブ

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c cd /d C:\Users\infoa\Documents\solarworks-ai && set BOT_HEADLESS=1 && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\investigate_seeds.py >> ops\scheduler\logs\windows_task_seed_investigate.log 2>&1"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4:00am

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName "RoomBot_SeedInvestigation_Weekly" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Weekly re-investigation of 447 seed users to refresh follower counts (CEO 2026-05-10 approval)" `
    -Force

Write-Output "Registered RoomBot_SeedInvestigation_Weekly. Next run:"
Get-ScheduledTask -TaskName "RoomBot_SeedInvestigation_Weekly" | Select-Object TaskName, State, @{n='NextRun';e={(Get-ScheduledTaskInfo $_).NextRunTime}}
