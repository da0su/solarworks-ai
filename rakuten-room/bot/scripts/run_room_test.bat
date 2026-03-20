@echo off
chcp 65001 >nul
title ROOM BOT - テスト投稿 (3件)

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - テスト投稿 (3件, 短縮待機)
echo   %date% %time%
echo ============================================================
echo.

python run.py batch --file data/test_posts.json --count 3 --min-wait 5 --max-wait 10

echo.
echo テスト投稿が完了しました。
pause
