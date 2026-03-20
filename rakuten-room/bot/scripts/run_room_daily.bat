@echo off
chcp 65001 >nul
title ROOM BOT - デイリー自動実行

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - デイリー (plan + execute)
echo   %date% %time%
echo ============================================================
echo.

python run.py daily

echo.
echo デイリー処理が完了しました。
echo ログ: data\logs\
echo.
pause
