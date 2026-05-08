@echo off
REM 2026-05-08: HOST follow_via_seeds.py via Task Scheduler (CEO ok 11:30)
REM ASCII-only to avoid cp932 mis-parse of Japanese comments

cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
python rakuten-room\bot\scripts\follow_via_seeds.py --target 50 --duration-min 30 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_follow_host.log" 2>&1
exit /b %errorlevel%
