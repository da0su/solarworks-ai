@echo off
cmd.exe /c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\update_seed_sheet_daily.py >> ops\scheduler\logs\windows_task_seed_sheet.log 2>&1
