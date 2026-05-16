@echo off
chcp 65001 >nul 2>&1
REM === Solar Works Auto Start (Cyber-san) ===
REM
REM CEO 2026-05-16 「画面占領するのやめて」指示反映 v2:
REM  PC 起動時に開く cmd window を pythonw.exe (no console) 化.
REM  PUSH_WATCHDOG が 5/14 9:00 から 2.5 日間 cmd window 残存していた事象への対応.
REM  Claude Code COO のみ CEO 確認用に window 残す (これは意図的).

set "LOGFILE=%~dp0logs\startup.log"
set "PYTHON=C:\Users\infoa\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONW=C:\Users\infoa\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "VOICEVOX=C:\Users\infoa\AppData\Local\Microsoft\WinGet\Packages\HiroshibaKazuyuki.VOICEVOX_Microsoft.Winget.Source_8wekyb3d8bbwe\VOICEVOX\VOICEVOX.exe"
set "SCHEDULER=C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\scheduler.py"
set "WORKDIR=C:\Users\infoa\Documents\solarworks-ai"
set "CLAUDE=C:\Users\infoa\AppData\Roaming\Claude\claude-code\2.1.78\claude.exe"

echo [%date% %time%] === Solar Works Auto Start === >> "%LOGFILE%"

REM === 1. VOICEVOX (GUI app・そのまま) ===
echo [%date% %time%] VOICEVOX starting... >> "%LOGFILE%"
start "" "%VOICEVOX%"

REM Wait for VOICEVOX ENGINE (30sec)
echo [%date% %time%] Waiting 30sec for VOICEVOX ENGINE... >> "%LOGFILE%"
ping 127.0.0.1 -n 31 >nul

REM === 2. scheduler.py (pythonw = no console) ===
echo [%date% %time%] scheduler.py starting (pythonw hidden)... >> "%LOGFILE%"
cd /d "%WORKDIR%"
start "" "%PYTHONW%" "%SCHEDULER%"

REM === 3. 裏パトロール (pythonw = no console) ===
set "BG_PATROL=C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\monitor\background_patrol.py"
echo [%date% %time%] Background patrol starting (pythonw hidden)... >> "%LOGFILE%"
start "" "%PYTHONW%" "%BG_PATROL%"

REM === 4. slack_bridge watchdog (pythonw = no console) ===
set "BRIDGE_WATCHDOG=C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\watchdog_bridge.py"
echo [%date% %time%] watchdog_bridge starting (pythonw hidden)... >> "%LOGFILE%"
start "" "%PYTHONW%" "%BRIDGE_WATCHDOG%"

REM === 5. push_watchdog (pythonw = no console) ===
REM   5/14-5/16 で 2.5日 cmd window 残存していた問題への対応.
set "PUSH_WATCHDOG=C:\Users\infoa\Documents\solarworks-ai\ops\slack_monitor\push_watchdog.py"
echo [%date% %time%] push_watchdog starting (pythonw hidden)... >> "%LOGFILE%"
start "" "%PYTHONW%" -X utf8 "%PUSH_WATCHDOG%" --start

REM === 6. Claude Code (CEO 確認用に window 残す) ===
echo [%date% %time%] Claude Code starting (visible for CEO)... >> "%LOGFILE%"
start "Claude Code COO" cmd /k "cd /d "%WORKDIR%" && "%CLAUDE%""

echo [%date% %time%] All services started. >> "%LOGFILE%"
