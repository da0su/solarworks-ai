@echo off
REM CEO 最後の 1 アクション: VM 内で 1 度だけ実行する setup script
REM
REM 配置: 共有フォルダ \\vboxsvr\vm_v6\setup_watcher_once.bat
REM
REM 効果:
REM   1. vm_watcher.bat を VM Startup folder に登録 (再起動後自動起動)
REM   2. 即時 vm_watcher を起動
REM   → 以降 host から `python ops/vm_v6/host_trigger.py --mode <X>` で完全自立 trigger

setlocal
echo === setup_watcher_once.bat ===
echo VM 内 watcher を Startup folder に登録します.

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set TARGET=%STARTUP%\rakuten_vm_watcher.bat

REM Startup bat: 共有フォルダの最新 vm_watcher.bat を起動 (常に最新版が走る)
echo @echo off > "%TARGET%"
echo start "" /B cmd /c "\\vboxsvr\vm_v6\vm_watcher.bat" >> "%TARGET%"

echo Startup 登録完了: %TARGET%

REM 即時起動
start "" /B cmd /c "\\vboxsvr\vm_v6\vm_watcher.bat"
echo vm_watcher 起動中 (background)

echo.
echo === 完了 ===
echo これで host から host_trigger.py 経由で trigger pulse を打てば、
echo VM 内 vm_watcher が pickup して rakuten_room_runner --mode X が走ります.
echo VM 再起動後も自動起動するので、CEO 介入はこの 1 度だけです.
echo.
pause
