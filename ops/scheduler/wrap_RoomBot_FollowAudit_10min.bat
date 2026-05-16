@echo off
cmd.exe /c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\audit_logger_10min.py >> ops\scheduler\logs\windows_task_audit_logger.log 2>&1
