@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

:: ログディレクトリ確保
if not exist "logs" mkdir logs

echo ========================================
echo  COIN-WEB  -  coin_business/web port 8502
echo ========================================

:loop
echo [%date% %time%] ---- coin web server 起動 ---- >> logs\coin_web.log

:: ポート確認
netstat -ano | find ":8502 " | find "LISTENING" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [%date% %time%] 8502 既に起動中 - スキップ >> logs\coin_web.log
    timeout /t 30 /nobreak >nul
    goto loop
)

echo [%date% %time%] coin web server 起動中...
cd coin_business\web
python -m http.server 8502 >> ..\..\logs\coin_web.log 2>&1
set EXIT_CODE=%ERRORLEVEL%
cd ..\..

echo [%date% %time%] coin web server 終了 (exit=%EXIT_CODE%) >> logs\coin_web.log
echo [%date% %time%] coin web server 終了 - 5秒後に再起動...

timeout /t 5 /nobreak >nul
goto loop
