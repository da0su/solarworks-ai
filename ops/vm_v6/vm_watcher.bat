@echo off
REM VM 内 watcher loop: host からの GuestProperty trigger を pickup して runner 起動
REM
REM 配置: 共有フォルダ \\vboxsvr\vm_v6\vm_watcher.bat (host の ops\vm_v6\ がマウント)
REM
REM VM 内 1 度だけ実行 (CEO に依頼する最後の 1 アクション):
REM   1. VM コンソール開く
REM   2. \\vboxsvr\vm_v6\setup_watcher_once.bat ダブルクリック
REM     → Startup folder に shortcut 追加 + 即時 watcher 起動
REM   3. 以降 VM 再起動でも自動起動. host_trigger.py で完全自立

setlocal
set BASE=%USERPROFILE%\Desktop\rakuten_room_bot
set PROP=\RakutenBot\Trigger
set VBC="%ProgramFiles%\Oracle\VirtualBox Guest Additions\VBoxControl.exe"
set LOG=%BASE%\logs\vm_watcher.log
set LAST_ID_FILE=%BASE%\logs\.vm_watcher_last_trigger_id

if not exist "%BASE%\logs" mkdir "%BASE%\logs"

echo [vm_watcher] start %DATE% %TIME% >> "%LOG%"

:loop
REM VBoxControl で /RakutenBot/Trigger の変化を 30 秒以内に待機 (timeout 後 loop 再開)
%VBC% guestproperty wait %PROP% --timeout 30000 > "%TEMP%\vm_trigger_pulse.txt" 2>&1
if errorlevel 1 (
    REM timeout → loop 継続
    goto loop
)

REM 値読み取り
for /f "tokens=2 delims=:" %%v in ('%VBC% guestproperty get %PROP%') do (
    set RAW=%%v
)
set RAW=%RAW: =%

echo [vm_watcher] %DATE% %TIME% trigger: %RAW% >> "%LOG%"

REM JSON parse は Python で
python -X utf8 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('mode','')); print(d.get('trigger_id',''))" "%RAW%" > "%TEMP%\vm_trigger_parsed.txt" 2>&1

set /p MODE=<"%TEMP%\vm_trigger_parsed.txt"
for /f "skip=1 delims=" %%i in (%TEMP%\vm_trigger_parsed.txt) do (
    set TRIGGER_ID=%%i
    goto :got_id
)
:got_id

REM 重複防止: 同じ trigger_id は無視
if exist "%LAST_ID_FILE%" (
    set /p LAST=<"%LAST_ID_FILE%"
    if "%LAST%"=="%TRIGGER_ID%" (
        echo [vm_watcher] skip duplicate %TRIGGER_ID% >> "%LOG%"
        goto loop
    )
)
echo %TRIGGER_ID%> "%LAST_ID_FILE%"

REM mode dispatch
if "%MODE%"=="comment_edit" (
    echo [vm_watcher] run comment_edit %TRIGGER_ID% >> "%LOG%"
    cd /d "%BASE%"
    python -m runner.rakuten_room_runner --mode comment_edit >> "%BASE%\logs\comment_edit_%TRIGGER_ID%.log" 2>&1
    echo [vm_watcher] comment_edit done rc=%ERRORLEVEL% >> "%LOG%"
) else if "%MODE%"=="post" (
    cd /d "%BASE%"
    python -m runner.rakuten_room_runner --mode post --batch 1 >> "%BASE%\logs\post_%TRIGGER_ID%.log" 2>&1
) else if "%MODE%"=="like" (
    cd /d "%BASE%"
    python -m runner.rakuten_room_runner --mode like >> "%BASE%\logs\like_%TRIGGER_ID%.log" 2>&1
) else if "%MODE%"=="follow" (
    cd /d "%BASE%"
    python -m runner.rakuten_room_runner --mode follow >> "%BASE%\logs\follow_%TRIGGER_ID%.log" 2>&1
) else if "%MODE%"=="followback" (
    cd /d "%BASE%"
    python -m runner.rakuten_room_runner --mode followback >> "%BASE%\logs\followback_%TRIGGER_ID%.log" 2>&1
) else (
    echo [vm_watcher] unknown mode: %MODE% >> "%LOG%"
)

goto loop
