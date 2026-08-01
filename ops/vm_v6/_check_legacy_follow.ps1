# Inventory helper: is the legacy VM follow_scheduler still doing real work?
$ErrorActionPreference = "SilentlyContinue"
$log = "C:\Users\cyber\Desktop\bot\follow_scheduler.log"
$py  = "C:\Users\cyber\Desktop\bot\follow_rpa_vm.py"

"=== follow_rpa_vm.py exists: {0}" -f (Test-Path $py)
if (Test-Path $log) {
    $li = Get-Item $log
    "=== log sizeKB={0} lastWrite={1}" -f [math]::Round($li.Length/1KB,1), $li.LastWriteTime
    "=== last 12 lines ==="
    Get-Content $log -Tail 12
    "=== breakdown of last 200 lines ==="
    $tail = Get-Content $log -Tail 200
    "START        : {0}" -f ($tail | Select-String -Pattern "START" -SimpleMatch).Count
    "SKIP existing: {0}" -f ($tail | Select-String -Pattern "SKIP: existing" -SimpleMatch).Count
    "SKIP stopflag: {0}" -f ($tail | Select-String -Pattern "SKIP: stop_flag" -SimpleMatch).Count
} else {
    "=== no log file"
}
