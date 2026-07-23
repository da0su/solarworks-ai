@echo off
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
echo ================ >> state\daily_direction_log.txt
C:\Users\infoa\AppData\Local\Programs\Python\Python312\python.exe ops\daily_direction.py >> state\daily_direction_log.txt 2>&1
