# VM 側スケジュールタスクの棚卸し (HOST から /exec で実行する)
# 出力: 名前 | 状態 | 最終実行 | 戻り値 | 実行コマンド
$ErrorActionPreference = "SilentlyContinue"
Get-ScheduledTask |
    Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -notmatch 'Microsoft|Adobe|Google|Update|OneDrive' } |
    ForEach-Object {
        $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
        $act = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " ; "
        "{0}|{1}|last={2}|rc={3}|{4}" -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult, $act
    }
