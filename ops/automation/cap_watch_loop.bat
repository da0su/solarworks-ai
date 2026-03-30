@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

:: ログディレクトリ確保
if not exist "logs" mkdir logs

echo ========================================
echo  CAP WATCH LOOP  -  SolarWorks AI
echo ========================================

:loop
echo [%date% %time%] ---- cap watch 起動 ---- >> logs\cap_watch.log
echo [%date% %time%] cap watch 起動中...

python slack_bridge.py watch >> logs\cap_watch.log 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo [%date% %time%] cap watch 終了 (exit=%EXIT_CODE%) >> logs\cap_watch.log
echo [%date% %time%] cap watch 終了 (exit=%EXIT_CODE%) - 5秒後に再起動...

timeout /t 5 /nobreak >nul
goto loop
