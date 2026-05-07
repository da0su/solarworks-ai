@echo off
REM Hourly 4-function patrol.
REM Triggered by Task Scheduler: RoomBot_Patrol_Hourly (15min interval).
REM 2026-05-07 P0-3 (Plan v5): --recover ON
REM   Plan v5 真因 #5: --recover 引数がなく follow problem 検知しても recover 試行されなかった
REM   問題が解消したため、auto recover (vm_kill_all + launcher --force) を有効化する。

setlocal
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
python -u ops\patrol_hourly.py --recover >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_patrol.log" 2>&1
exit /b %errorlevel%
