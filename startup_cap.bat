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

echo.
echo [OK] CAP watch 起動完了
echo      ログ: logs\cap_watch.log
echo      ウィンドウ: CAP-WATCH [SolarWorks AI]
echo.
echo 定時チェック自動発火: 07:30 / 12:30 / 18:30 JST
echo.
pause
