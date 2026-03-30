@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  SolarWorks AI  -  CYBER 起動
echo  %date% %time%
echo ========================================

:: ログディレクトリ
if not exist "logs" mkdir logs

:: sender を cyber に設定
echo [cyber] sender設定中...
python slack_bridge.py set-sender cyber >> logs\cyber_startup.log 2>&1

:: state確認
echo [cyber] state確認中...
python slack_bridge.py state-summary >> logs\cyber_startup.log 2>&1

:: cyber watch を別ウィンドウで常駐起動（自動再起動ループ付き）
echo [cyber] watch 常駐起動中...
start "CYBER-WATCH [SolarWorks AI]" cmd /k "title CYBER-WATCH [SolarWorks AI] && ops\automation\cyber_watch_loop.bat"

:: watch-guardian を別ウィンドウで常駐起動（ACK_TIMEOUT / heartbeat停止 → 自動復旧）
echo [cyber] watch-guardian 常駐起動中...
start "CYBER-GUARDIAN [SolarWorks AI]" cmd /k "title CYBER-GUARDIAN [SolarWorks AI] && python slack_bridge.py watch-guardian >> logs\cyber_guardian.log 2>&1"

echo.
echo [OK] CYBER watch + guardian 起動完了
echo      watch ログ:    logs\cyber_watch.log
echo      guardian ログ: logs\cyber_guardian.log
echo      self-heal ログ: logs\watch_self_heal.log
echo      ウィンドウ: CYBER-WATCH / CYBER-GUARDIAN
echo.
echo 次のステップ:
echo   楽天ROOM scheduler は別途 startup_all.bat で起動してください
echo.
pause
