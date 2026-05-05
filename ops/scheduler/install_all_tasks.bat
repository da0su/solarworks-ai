@echo off
REM 2026-05-05 Phase B-5: 全 RoomBot Task を一括 install (冪等)
REM CEO が新マシン or 環境変更時に実行する 1 つの bat
chcp 65001 >nul

echo ============================================================
echo   RoomBot Task Scheduler - Install ALL
echo ============================================================
echo.

REM 既存 setup_scheduler.ps1 (post Batch / follow / like 系)
echo [1/6] setup_scheduler.ps1 (post/like/follow base tasks)
call powershell -ExecutionPolicy Bypass -File "%~dp0..\..\rakuten-room\bot\scripts\setup_scheduler.ps1"
echo.

REM Phase 2-3: patrol 15-min interval
echo [2/6] set_patrol_15min.ps1 (patrol every 15 min)
call powershell -ExecutionPolicy Bypass -File "%~dp0set_patrol_15min.ps1"
echo.

REM Phase B-1: replenish daily
echo [3/6] set_replenish_daily.ps1 (06:00)
call powershell -ExecutionPolicy Bypass -File "%~dp0set_replenish_daily.ps1"
echo.

REM Phase B-5: task healthcheck daily
echo [4/6] set_healthcheck_daily.ps1 (00:00)
call powershell -ExecutionPolicy Bypass -File "%~dp0set_healthcheck_daily.ps1"
echo.

REM Phase C-4: seed scrape daily
echo [5/6] set_scrape_daily.ps1 (00:30)
call powershell -ExecutionPolicy Bypass -File "%~dp0set_scrape_daily.ps1"
echo.

REM Phase C-6: CEO dashboard 3 times
echo [6/6] set_dashboard_3times.ps1 (07:00 / 12:00 / 21:00)
call powershell -ExecutionPolicy Bypass -File "%~dp0set_dashboard_3times.ps1"
echo.

REM 最後に healthcheck で確認
echo ============================================================
echo   Final healthcheck
echo ============================================================
python "%~dp0healthcheck_tasks.py" --check-only

echo.
echo ============================================================
echo   Install completed. Press any key to exit...
echo ============================================================
pause >nul
