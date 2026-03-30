@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  SolarWorks AI  -  CAP 起動
echo  %date% %time%
echo ========================================

:: ログディレクトリ
if not exist "logs" mkdir logs

:: state確認
echo [cap] state確認中...
python slack_bridge.py state-summary >> logs\cap_startup.log 2>&1

:: cap watch を別ウィンドウで常駐起動（自動再起動ループ付き）
echo [cap] watch 常駐起動中...
start "CAP-WATCH [SolarWorks AI]" cmd /k "title CAP-WATCH [SolarWorks AI] && ops\automation\cap_watch_loop.bat"

:: watch-guardian を別ウィンドウで常駐起動（ACK_TIMEOUT / heartbeat停止 → 自動復旧）
echo [cap] watch-guardian 常駐起動中...
start "CAP-GUARDIAN [SolarWorks AI]" cmd /k "title CAP-GUARDIAN [SolarWorks AI] && python slack_bridge.py watch-guardian >> logs\cap_guardian.log 2>&1"

echo.
echo [OK] CAP watch + guardian 起動完了
echo      watch ログ:    logs\cap_watch.log
echo      guardian ログ: logs\cap_guardian.log
echo      self-heal ログ: logs\watch_self_heal.log
echo      ウィンドウ: CAP-WATCH / CAP-GUARDIAN
echo.
echo 定時チェック自動発火: 07:30 / 12:30 / 18:30 JST
echo.
pause
