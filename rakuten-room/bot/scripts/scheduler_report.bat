@echo off
chcp 65001 >nul
cd /d "%~dp0.."

if "%1"=="night" (
    echo [%date% %time%] scheduler_report (night) 開始 >> data\logs\scheduler.log
    python run.py report --type night --slack >> data\logs\scheduler.log 2>&1
    echo [%date% %time%] scheduler_report (night) 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
) else (
    echo [%date% %time%] scheduler_report (morning) 開始 >> data\logs\scheduler.log
    python run.py report --type morning --slack >> data\logs\scheduler.log 2>&1
    echo [%date% %time%] scheduler_report (morning) 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
)
