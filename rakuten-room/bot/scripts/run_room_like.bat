@echo off
REM === ROOMいいね実行 + 完了通知 ===
REM 使い方: run_room_like.bat [件数]  (デフォルト: 10)
setlocal
set COUNT=%1
if "%COUNT%"=="" set COUNT=10

cd /d "%USERPROFILE%\Documents\solarworks-ai\rakuten-room\bot"
"%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" run.py room plus like %COUNT% > "%TEMP%\room_like_result.txt" 2>&1
set EXIT_CODE=%ERRORLEVEL%
type "%TEMP%\room_like_result.txt"

if %EXIT_CODE% NEQ 0 (
    call "%~dp0notify_error.bat"
    goto :end
)

findstr /C:"完了:   0件" "%TEMP%\room_like_result.txt" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] いいね0件のため通知をスキップします。
    goto :end
)

call "%~dp0notify_like_done.bat"

:end
del "%TEMP%\room_like_result.txt" >nul 2>&1
endlocal
