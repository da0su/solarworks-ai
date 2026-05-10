# RoomBot_SeedSheet_Daily Task Scheduler 設定
# CEO 2026-05-10 指示: 「スプシの 06_フォロワー調査 の入力も毎日更新しなさい」
#
# 実行: 毎日 06:30 (DailyReset 06:00 後・Dashboard Morning 07:00 前)
# 内容: update_seed_sheet_daily.py で seed_investigation.json をスプシに同期 (高速・10秒)
# 出力: スプシ 06_フォロワー調査 タブを最新化

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\update_seed_sheet_daily.py >> ops\scheduler\logs\windows_task_seed_sheet.log 2>&1"

$trigger = New-ScheduledTaskTrigger -Daily -At 6:30am

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -RestartCount 2 `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName "RoomBot_SeedSheet_Daily" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily sync of seed_investigation.json to spreadsheet 06_フォロワー調査 (CEO 2026-05-10)" `
    -Force

Write-Output "Registered RoomBot_SeedSheet_Daily. Next run:"
Get-ScheduledTask -TaskName "RoomBot_SeedSheet_Daily" | Select-Object TaskName, State, @{n='NextRun';e={(Get-ScheduledTaskInfo $_).NextRunTime}}
