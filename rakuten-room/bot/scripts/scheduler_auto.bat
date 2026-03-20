@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo [%date% %time%] scheduler_auto (batch=%1) 開始 >> data\logs\scheduler.log
python run.py auto --batch %1 >> data\logs\scheduler.log 2>&1
echo [%date% %time%] scheduler_auto (batch=%1) 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
