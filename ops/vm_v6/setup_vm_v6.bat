@echo off
REM VM v6 setup wizard - ASCII ONLY (no Japanese, no chcp)

set BASE=%USERPROFILE%\Desktop\rakuten_room_bot
echo [STEP1] BASE=%BASE%
mkdir "%BASE%" 2>nul
mkdir "%BASE%\runner" 2>nul
mkdir "%BASE%\server" 2>nul
mkdir "%BASE%\data" 2>nul
mkdir "%BASE%\logs" 2>nul
mkdir "%BASE%\credentials" 2>nul

echo [STEP2] copy code from vm_v6 share
copy /Y "\\vboxsvr\vm_v6\runner\*.py" "%BASE%\runner\" >nul 2>&1
copy /Y "\\vboxsvr\vm_v6\server\*.py" "%BASE%\server\" >nul 2>&1

echo [STEP3] pip install
python -m pip install --upgrade pip
python -m pip install playwright fastapi uvicorn requests gspread psutil

echo [STEP4] Playwright Chromium install (5-10min)
python -m playwright install chromium

echo [STEP5] copy 4 chrome profiles from vm_data share (10-20min)
robocopy "\\vboxsvr\vm_data\chrome_profile_post" "%BASE%\data\chrome_profile_post" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\vm_data\chrome_profile_like" "%BASE%\data\chrome_profile_like" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\vm_data\chrome_profile_followback" "%BASE%\data\chrome_profile_followback" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\vm_data\chrome_profile_follow" "%BASE%\data\chrome_profile_follow" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS

echo [STEP6] register startup
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo @echo off > "%STARTUP%\rakuten_room_bot_v6_server.bat"
echo cd /d "%BASE%\server" >> "%STARTUP%\rakuten_room_bot_v6_server.bat"
echo start "" /B python http_server.py >> "%STARTUP%\rakuten_room_bot_v6_server.bat"

echo [STEP7] start HTTP server
cd /d "%BASE%\server"
start "vm_v6_server" /B python http_server.py

echo SETUP DONE - HTTP server on port 8765
echo done > "\\vboxsvr\vm_v6\.setup_done"
exit /b 0
