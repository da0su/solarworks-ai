@echo off
cd /d C:\Users\infoa\Documents\solarworks-ai
set BOT_HEADLESS=1
set PYTHONIOENCODING=utf-8
python rakuten-room\bot\scripts\investigate_seeds.py >> C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_seed_investigate.log 2>&1
