@echo off
REM 2026-05-06 CEO: 24h operation rule. --force for dead_zone bypass.
cd /d C:\Users\infoa\Documents\solarworks-ai
python ops\vm_follow_launcher.py --force
exit /b %errorlevel%
