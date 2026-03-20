@echo off
chcp 65001 >nul
title ROOM BOT - キュー実行

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - キュー実行 (execute)
echo   %date% %time%
echo ============================================================
echo.

python run.py execute

echo.
echo キュー実行が完了しました。
pause
