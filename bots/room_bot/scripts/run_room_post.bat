@echo off
REM === ROOM投稿実行 + 完了通知 ===
REM 使い方: run_room_post.bat [件数]  (デフォルト: 10)
setlocal
set COUNT=%1
if "%COUNT%"=="" set COUNT=10

cd /d "%USERPROFILE%\solarworks-ai\bots\room_bot"
"%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" run.py room plus post %COUNT% > "%TEMP%\room_post_result.txt" 2>&1
set EXIT_CODE=%ERRORLEVEL%
type "%TEMP%\room_post_result.txt"

if %EXIT_CODE% NEQ 0 (
    call "%~dp0notify_error.bat"
    goto :end
)

findstr /C:"完了:   0件" "%TEMP%\room_post_result.txt" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] 投稿0件のため通知をスキップします。
    goto :end
)

call "%~dp0notify_post_done.bat"

:end
del "%TEMP%\room_post_result.txt" >nul 2>&1
endlocal
