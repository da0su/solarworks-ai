@echo off
timeout /T 30 /NOBREAK >nul
cd /d "%USERPROFILE%\Documents\solarworks-ai\rakuten-room\bot"
start "" /B "%USERPROFILE%\AppData\Local\Programs\Python\Python312\pythonw.exe" slack_room_bot.py
