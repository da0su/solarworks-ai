@echo off
chcp 65001 >nul
title ROOM BOT - 投稿計画生成

cd /d "%~dp0.."
echo ============================================================
echo   ROOM BOT - 投稿計画生成 (plan)
echo   %date% %time%
echo ============================================================
echo.

python run.py plan

echo.
echo 投稿計画の生成が完了しました。
pause
