@echo off
chcp 65001 >nul
title ROOM BOT - プレビュー

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - 投稿プレビュー (dry-run)
echo   %date% %time%
echo ============================================================
echo.

python run.py preview --file data/test_posts.json

echo.
pause
