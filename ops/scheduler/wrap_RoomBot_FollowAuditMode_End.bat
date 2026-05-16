@echo off
cmd.exe /c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\follow_audit_mode_toggle.py end >> ops\scheduler\logs\windows_task_audit_toggle.log 2>&1
