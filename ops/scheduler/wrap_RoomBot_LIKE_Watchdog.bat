@echo off
REM LIKE Watchdog - 15分ごとに heartbeat チェック・stuck時は VM HTTP /run で自動復旧
REM 実装: ops/like_watchdog.py

setlocal
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
python ops\like_watchdog.py >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_like_watchdog.log" 2>&1
exit /b %errorlevel%
