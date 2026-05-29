@echo off
rem 2026-05-29 fix: 絶対パスでログ先を指定 (run_hidden.vbs 経由の作業ディレクトリ問題回避)
cmd.exe /c cd /d C:\Users\infoa\Documents\solarworks-ai && set PYTHONIOENCODING=utf-8 && python rakuten-room\bot\scripts\sync_daily_summary.py >> C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_sync_daily.log 2>&1
