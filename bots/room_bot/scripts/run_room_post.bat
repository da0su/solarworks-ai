@echo off
cd /d "%USERPROFILE%\solarworks-ai\bots\room_bot"
"%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" run.py room plus post 10
if %ERRORLEVEL% EQU 0 (
    call "%~dp0notify_post_done.bat"
) else (
    echo [ERROR] 投稿処理でエラーが発生しました (code=%ERRORLEVEL%)
)
