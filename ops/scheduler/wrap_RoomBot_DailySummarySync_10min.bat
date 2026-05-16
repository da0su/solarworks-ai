@echo off
cmd.exe /c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\sync_daily_summary.py >> ops\scheduler\logs\windows_task_sync_daily.log 2>&1
