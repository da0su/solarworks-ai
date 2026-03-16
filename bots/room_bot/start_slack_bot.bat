@echo off
timeout /T 30 /NOBREAK >nul
cd /d "%USERPROFILE%\solarworks-ai\bots\room_bot"
start "" /B "%USERPROFILE%\AppData\Local\Programs\Python\Python312\pythonw.exe" slack_room_bot.py
