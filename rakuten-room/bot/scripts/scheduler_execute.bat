@echo off
chcp 65001 >nul
REM === ROOM BOT - タスクスケジューラ用: キュー実行 ===
REM Windows タスクスケジューラから呼ばれる（日中複数回: 10:00, 13:00, 16:00 等）
REM 1回の起動で queued のうち --limit 件だけ処理する
REM pause なし・自動終了

cd /d "%~dp0.."

echo [%date% %time%] scheduler_execute 開始 >> data\logs\scheduler.log

python run.py execute --limit 5 >> data\logs\scheduler.log 2>&1

echo [%date% %time%] scheduler_execute 終了 (exit=%ERRORLEVEL%) >> data\logs\scheduler.log
