@echo off
REM ASCII only - VM watcher loop with auto-sync from shared folder

setlocal

set BASE=%USERPROFILE%\Desktop\rakuten_room_bot
set PROP=/RakutenBot/Trigger
set VBC="%ProgramFiles%\Oracle\VirtualBox Guest Additions\VBoxControl.exe"
set LOG=\\vboxsvr\vm_data\vm_watcher_runtime.log

echo [vm_watcher] start %DATE% %TIME% pid_marker=%RANDOM% >> "%LOG%"

REM === ONE-SHOT: 電源管理 sleep 無効化 (再発防止) ===
set NOSLEEP_FLAG=%BASE%\.nosleep_done
if not exist "%NOSLEEP_FLAG%" (
    echo [vm_watcher] one-shot: disable monitor/standby sleep >> "%LOG%"
    powercfg /change monitor-timeout-ac 0 >> "%LOG%" 2>&1
    powercfg /change monitor-timeout-dc 0 >> "%LOG%" 2>&1
    powercfg /change standby-timeout-ac 0 >> "%LOG%" 2>&1
    powercfg /change standby-timeout-dc 0 >> "%LOG%" 2>&1
    powercfg /change disk-timeout-ac 0 >> "%LOG%" 2>&1
    powercfg /change hibernate-timeout-ac 0 >> "%LOG%" 2>&1
    echo done > "%NOSLEEP_FLAG%"
)

REM === ONE-SHOT: disk extend + pip fix (after VM disk resize) ===
set EXTEND_FLAG=%BASE%\.disk_extend_done
if not exist "%EXTEND_FLAG%" (
    echo [vm_watcher] one-shot: diskpart extend C >> "%LOG%"
    (
        echo select volume C
        echo extend
    ) > "%TEMP%\vw_diskext.txt"
    diskpart /s "%TEMP%\vw_diskext.txt" >> "%LOG%" 2>&1

    echo [vm_watcher] one-shot: pip fix greenlet+playwright >> "%LOG%"
    python -m pip install --upgrade --no-cache-dir greenlet playwright >> "\\vboxsvr\vm_data\pip_fix.log" 2>&1
    echo [vm_watcher] one-shot done rc=%ERRORLEVEL% >> "%LOG%"

    echo done > "%EXTEND_FLAG%"
)


:loop
echo [vm_watcher] enter wait at %DATE% %TIME% >> "%LOG%"
%VBC% guestproperty wait %PROP% --timeout 30000 > "%TEMP%\vw_wait.txt" 2>&1
set RC=%ERRORLEVEL%
echo [vm_watcher] wait returned rc=%RC% at %DATE% %TIME% >> "%LOG%"
type "%TEMP%\vw_wait.txt" >> "%LOG%"

if not "%RC%"=="0" goto loop

echo [vm_watcher] pulse detected at %DATE% %TIME% - syncing runner code >> "%LOG%"

REM Sync latest runner code from shared folder (host side)
echo [vm_watcher] DIAG: source dir listing >> "%LOG%"
dir "\\vboxsvr\vm_v6\runner\*.py" >> "%LOG%" 2>&1
echo [vm_watcher] DIAG: target before copy >> "%LOG%"
dir "%BASE%\runner\rakuten_room_runner.py" >> "%LOG%" 2>&1
echo [vm_watcher] DIAG: running copy >> "%LOG%"
copy /Y "\\vboxsvr\vm_v6\runner\*.py" "%BASE%\runner\" >> "%LOG%" 2>&1
echo [vm_watcher] DIAG: target after copy >> "%LOG%"
dir "%BASE%\runner\rakuten_room_runner.py" >> "%LOG%" 2>&1
findstr /C:"comment_edit" "%BASE%\runner\rakuten_room_runner.py" >> "%LOG%" 2>&1
echo [vm_watcher] DIAG: comment_edit_executor_v6 check >> "%LOG%"
if exist "%BASE%\runner\comment_edit_executor_v6.py" (
    echo found comment_edit_executor_v6.py >> "%LOG%"
) else (
    echo NOT FOUND comment_edit_executor_v6.py >> "%LOG%"
)
echo [vm_watcher] sync done >> "%LOG%"

REM Python 3.12 (winget install 済) を強制使用. 3.14 (Microsoft Store) は DLL load 不可
set PYEXE=%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe
echo [vm_watcher] PYEXE=%PYEXE% >> "%LOG%"

echo [vm_watcher] running comment_edit at %DATE% %TIME% >> "%LOG%"
cd /d "%BASE%"
REM chrome_profile_post は空アカウント (5/20 既知問題). follow profile が本来アカウント.
set COMMENT_EDIT_PROFILE=follow
"%PYEXE%" -m runner.rakuten_room_runner --mode comment_edit > "\\vboxsvr\vm_data\ce_%RANDOM%.log" 2>&1
set RC=%ERRORLEVEL%
echo [vm_watcher] comment_edit done rc=%RC% at %DATE% %TIME% >> "%LOG%"

timeout /t 5 /nobreak > nul

goto loop
