@echo off
chcp 65001 >nul
title ROOM BOT - ログイン

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - ログイン
echo   %date% %time%
echo ============================================================
echo.

python run.py login

echo.
echo ログイン処理が完了しました。
pause
