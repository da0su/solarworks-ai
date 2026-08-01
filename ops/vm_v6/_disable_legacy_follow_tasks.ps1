# Disable the legacy RoomFollow_XX tasks on the VM.
#
# Why (measured 2026-08-01):
#   - 20 tasks (RoomFollow_01..23) fire hourly and call follow_scheduler.bat
#   - follow_scheduler.bat runs follow_rpa_vm.py which dies instantly with
#     ModuleNotFoundError: No module named 'numpy'  (100% failure, 33/33 in log)
#   - They have produced a 10MB follow_scheduler.log of nothing but tracebacks
#   - The real FOLLOW work is done by the Plan v6 path
#     (HOST RoomBotFollow_Hourly -> VM HTTP /run -> runner_follow), which is
#     confirmed working (183+ follows today)
#   => pure waste: hourly process spawns + log growth + misleading "FOLLOW is
#      scheduled on the VM" impression.
#
# Disable (not delete) so it is trivially reversible.
$ErrorActionPreference = "Continue"
$names = Get-ScheduledTask |
    Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -match '^RoomFollow_\d+$' } |
    Select-Object -ExpandProperty TaskName

"found: {0}" -f $names.Count
foreach ($n in $names) {
    try {
        Disable-ScheduledTask -TaskName $n -ErrorAction Stop | Out-Null
        $t = Get-ScheduledTask -TaskName $n
        "{0} -> {1}" -f $n, $t.State
    } catch {
        "{0} -> FAILED: {1}" -f $n, $_.Exception.Message
    }
}
