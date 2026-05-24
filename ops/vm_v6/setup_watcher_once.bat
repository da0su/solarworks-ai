@echo off
REM ASCII only - avoid encoding issues with Windows cmd cp932
REM Purpose: register vm_watcher.bat in Startup folder + start now

setlocal

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set TARGET=%STARTUP%\rakuten_vm_watcher.bat

echo @echo off > "%TARGET%"
echo start "" /B cmd /c "\\vboxsvr\vm_v6\vm_watcher.bat" >> "%TARGET%"

echo Startup registered: %TARGET%

start "" /B cmd /c "\\vboxsvr\vm_v6\vm_watcher.bat"

echo vm_watcher started in background
echo Done. host_trigger.py can now send pulses.
pause
