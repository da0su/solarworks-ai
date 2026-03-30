@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  SolarWorks AI  -  CYBER 全サービス起動
echo  %date% %time%
echo ========================================
echo.

:: ログディレクトリ確保
if not exist "logs" mkdir logs

:: -----------------------------------------------
:: STEP 1: sender を cyber に設定
:: -----------------------------------------------
echo [1/5] sender設定 (cyber)...
python slack_bridge.py set-sender cyber >> logs\cyber_startup.log 2>&1
echo       OK

:: -----------------------------------------------
:: STEP 2: VOICEVOX 起動確認 (ポート50021)
:: -----------------------------------------------
echo [2/5] VOICEVOX確認中...
curl -s --max-time 2 http://localhost:50021/version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo       VOICEVOX 起動済み
) else (
    echo       VOICEVOX 未起動 - 起動します...
    start "" "%USERPROFILE%\AppData\Local\Programs\VOICEVOX\VOICEVOX.exe"
    echo       30秒待機中...
    timeout /t 30 /nobreak >nul
    echo       VOICEVOX 起動待ち完了
)

:: -----------------------------------------------
:: STEP 3: 楽天ROOM scheduler 起動（別ウィンドウ）
:: -----------------------------------------------
echo [3/5] 楽天ROOM scheduler 起動中...
start "CYBER-SCHEDULER [SolarWorks AI]" cmd /k "title CYBER-SCHEDULER [SolarWorks AI] && cd /d %~dp0ops\scheduler && python scheduler.py >> %~dp0logs\cyber_scheduler.log 2>&1"
echo       OK  (ウィンドウ: CYBER-SCHEDULER)

:: -----------------------------------------------
:: STEP 4: slack_bridge watch 起動（別ウィンドウ・自動再起動ループ）
:: -----------------------------------------------
echo [4/5] slack_bridge watch 常駐起動中...
start "CYBER-WATCH [SolarWorks AI]" cmd /k "title CYBER-WATCH [SolarWorks AI] && ops\automation\cyber_watch_loop.bat"
echo       OK  (ウィンドウ: CYBER-WATCH)

:: -----------------------------------------------
:: STEP 5: watch-guardian 起動（別ウィンドウ）
:: -----------------------------------------------
echo [5/5] watch-guardian 常駐起動中...
start "CYBER-GUARDIAN [SolarWorks AI]" cmd /k "title CYBER-GUARDIAN [SolarWorks AI] && python slack_bridge.py watch-guardian >> logs\cyber_guardian.log 2>&1"
echo       OK  (ウィンドウ: CYBER-GUARDIAN)

:: -----------------------------------------------
:: 完了
:: -----------------------------------------------
echo.
echo ========================================
echo  全サービス起動完了
echo ========================================
echo  CYBER-SCHEDULER  : 楽天ROOM 自動投稿
echo  CYBER-WATCH      : Slack Bridge 監視
echo  CYBER-GUARDIAN   : watch 自動復旧
echo.
echo  ログ一覧:
echo    logs\cyber_scheduler.log
echo    logs\cyber_watch.log
echo    logs\cyber_guardian.log
echo    logs\watch_self_heal.log
echo ========================================
echo.
python slack_bridge.py state-summary
echo.
pause
