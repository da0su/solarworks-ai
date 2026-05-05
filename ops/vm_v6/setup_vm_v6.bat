@echo off
REM Plan v4 P1: VM 内セットアップ wizard
REM CEO が VM 内 cmd で 1 回実行することで、Python 環境 + Playwright + 4 profile + HTTP server を準備する
chcp 65001 >nul

echo ============================================================
echo   楽天ROOM Bot VM v6 SETUP wizard
echo   実行先: VM RoomBot 内 cmd
echo ============================================================
echo.

REM Step 1: directory 作成
set BASE=%USERPROFILE%\Desktop\rakuten_room_bot
mkdir "%BASE%" 2>nul
mkdir "%BASE%\runner" 2>nul
mkdir "%BASE%\server" 2>nul
mkdir "%BASE%\data" 2>nul
mkdir "%BASE%\logs" 2>nul
mkdir "%BASE%\credentials" 2>nul
echo [1/7] directory 作成完了: %BASE%
echo.

REM Step 2: shared folder からコード copy
echo [2/7] shared folder からコード copy...
copy /Y "\\vboxsvr\share\..\..\..\ops\vm_v6\runner\*.py" "%BASE%\runner\" >nul 2>&1
copy /Y "\\vboxsvr\share\..\..\..\ops\vm_v6\server\*.py" "%BASE%\server\" >nul 2>&1
echo   完了
echo.

REM Step 3: pip install
echo [3/7] Python パッケージインストール...
python -m pip install --upgrade pip
python -m pip install playwright fastapi uvicorn requests gspread psutil
echo.

REM Step 4: Playwright Chromium インストール
echo [4/7] Playwright Chromium インストール (5-10分かかる)...
python -m playwright install chromium
echo.

REM Step 5: 4 profile を share folder から copy
echo [5/7] 4 chrome profile を share folder から copy...
echo   robocopy "\\vboxsvr\share\..\..\data\chrome_profile_post" "%BASE%\data\chrome_profile_post"
robocopy "\\vboxsvr\share\..\..\data\chrome_profile_post" "%BASE%\data\chrome_profile_post" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\share\..\..\data\chrome_profile_like" "%BASE%\data\chrome_profile_like" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\share\..\..\data\chrome_profile_followback" "%BASE%\data\chrome_profile_followback" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
robocopy "\\vboxsvr\share\..\..\data\chrome_profile_follow" "%BASE%\data\chrome_profile_follow" /E /XF SingletonLock SingletonSocket SingletonCookie /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
echo.

REM Step 6: HTTP server を Windows スタートアップに登録
echo [6/7] HTTP server をスタートアップに登録...
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo @echo off > "%STARTUP%\rakuten_room_bot_v6_server.bat"
echo cd /d "%BASE%\server" >> "%STARTUP%\rakuten_room_bot_v6_server.bat"
echo start "" /B python http_server.py >> "%STARTUP%\rakuten_room_bot_v6_server.bat"
echo   登録: %STARTUP%\rakuten_room_bot_v6_server.bat
echo.

REM Step 7: HTTP server を即起動
echo [7/7] HTTP server を起動...
cd /d "%BASE%\server"
start "rakuten_room_bot_v6_server" /B python http_server.py
echo.

echo ============================================================
echo   SETUP 完了。HTTP server が port 8765 で起動しているはずです。
echo   HOST 側で動作確認:
echo     python ops/vm_v6/vm_controller.py --status
echo ============================================================
pause
