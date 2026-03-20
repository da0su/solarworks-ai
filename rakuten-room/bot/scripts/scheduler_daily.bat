@echo off
chcp 65001 >nul
REM === ROOM BOT - タスクスケジューラ用: plan+execute一括 ===
REM Windows タスクスケジューラから呼ばれる（1日1回: 9:00 等）
REM pause なし・自動終了

cd /d "%~dp0.."

echo [%date% %time%] scheduler_daily 開始 >> data\logs\scheduler.log

python run.py daily >> data\logs\scheduler.log 2>&1

echo [%date% %time%] scheduler_daily 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
