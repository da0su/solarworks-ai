@echo off
chcp 65001 >nul
title ROOM BOT - 本番投稿

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - 本番投稿 (通常間隔)
echo   %date% %time%
echo ============================================================
echo.

python run.py daily --date %date:~0,4%-%date:~5,2%-%date:~8,2%

echo.
echo 投稿処理が完了しました。
echo ログ: data\logs\
echo.
pause
