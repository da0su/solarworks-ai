@echo off
REM Unified entry for orchestrator_v5 dispatch
REM Usage: orchestrator_run.bat <action> [--batch N] [--limit N]
REM Actions: post | like | followback | follow | preflight

setlocal
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
REM 2026-05-09: BOT_HEADLESS=1 to prevent focus stealing on HOST (CEO observation)
set BOT_HEADLESS=1
python -m ops.scheduler.orchestrator_v5 --action %1 %2 %3 %4 %5 %6 %7 %8 %9 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_%1.log" 2>&1
exit /b %errorlevel%
