@echo off
chcp 65001 > nul
cd /d %~dp0
echo ===================================
echo  SEO 自動化エンジン 起動
echo ===================================
py -3 run.py daemon
pause
