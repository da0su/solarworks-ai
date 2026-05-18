@echo off
chcp 65001 >nul 2>&1
REM CEO 2026-05-17 指示: 毎朝 9:00 Codex (GPT-5) 朝の戦略 briefing
REM run_hidden.vbs 経由で起動 = cmd window 非表示
set "PYTHON=C:\Users\infoa\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "SCRIPT=C:\Users\infoa\Documents\solarworks-ai\ops\codex_daily_briefing.py"
set "WORKDIR=C:\Users\infoa\Documents\solarworks-ai"
cd /d "%WORKDIR%"
"%PYTHON%" -X utf8 "%SCRIPT%"
