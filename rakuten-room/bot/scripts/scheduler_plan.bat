@echo off
chcp 65001 >nul
REM === ROOM BOT - タスクスケジューラ用: 朝の計画生成 ===
REM Windows タスクスケジューラから呼ばれる（毎朝 8:00 等）
REM pause なし・自動終了

cd /d "%~dp0.."

echo [%date% %time%] scheduler_plan 開始 >> data\logs\scheduler.log

python run.py plan >> data\logs\scheduler.log 2>&1

echo [%date% %time%] scheduler_plan 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
