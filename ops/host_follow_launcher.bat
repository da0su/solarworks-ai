@echo off
REM 2026-05-08: HOST follow_via_seeds.py via Task Scheduler (CEO ok 11:30)
REM 2026-05-09: BOT_HEADLESS=1 to prevent focus stealing (CEO observation)
REM 2026-05-09 18:42: duration 30->15 min to avoid 15-min trigger overlap silent fail
REM ASCII-only to avoid cp932 mis-parse of Japanese comments

cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
set BOT_HEADLESS=1
python rakuten-room\bot\scripts\follow_via_seeds.py --target 200 --duration-min 14 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_follow_host.log" 2>&1
exit /b %errorlevel%
