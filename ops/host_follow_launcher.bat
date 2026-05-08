@echo off
REM 2026-05-08: HOST follow_via_seeds.py で Task Scheduler から自動 follow
REM CEO 指示「100/15min・行き当たりばったり禁止・data-driven」対応
REM VM lock 中も HOST 側で確実に follow を実行する fallback 経路

cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8

REM 1 hour task で 50 件目標 / 30分以内・残り時間に余裕持たせる
python rakuten-room\bot\scripts\follow_via_seeds.py --target 50 --duration-min 30 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_follow_host.log" 2>&1
exit /b %errorlevel%
