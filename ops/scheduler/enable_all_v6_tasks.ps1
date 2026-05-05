# Plan v4 P1 完了後: 全 RoomBot Task を再有効化
$ErrorActionPreference = "Stop"
$enabled = 0
$failed = 0

Get-ScheduledTask | Where-Object {$_.TaskName -like "RoomBot*"} | ForEach-Object {
    try {
        Enable-ScheduledTask -TaskName $_.TaskName -ErrorAction Stop | Out-Null
        Write-Host "[ok] $($_.TaskName)"
        $enabled++
    } catch {
        Write-Host "[fail] $($_.TaskName): $($_.Exception.Message)"
        $failed++
    }
}

Write-Host ""
Write-Host "Enabled: $enabled / Failed: $failed"
Get-ScheduledTask | Where-Object {$_.TaskName -like "RoomBot*"} | Format-Table TaskName, State -AutoSize
