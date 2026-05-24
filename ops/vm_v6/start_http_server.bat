@echo off
REM Start VM HTTP server (FastAPI) in background.
REM Called from HOST via keystroke injection or VM startup.

setlocal

set PYEXE=%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe
set SERVER=\\vboxsvr\vm_v6\server\http_server.py
set LOG=\\vboxsvr\vm_data\http_server_runtime.log

echo [start_http_server] kicked at %DATE% %TIME% >> "%LOG%"

REM Check Python
if not exist "%PYEXE%" (
    echo ERROR: python not found at %PYEXE% >> "%LOG%"
    exit /b 1
)

REM Install deps if needed (idempotent)
"%PYEXE%" -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [start_http_server] installing fastapi+uvicorn >> "%LOG%"
    "%PYEXE%" -m pip install --quiet fastapi uvicorn >> "%LOG%" 2>&1
)

REM Start server detached (so this bat exits but server keeps running)
echo [start_http_server] launching http_server.py >> "%LOG%"
start "rakuten_http_server" /B "%PYEXE%" "%SERVER%" >> "%LOG%" 2>&1

echo [start_http_server] launched at %DATE% %TIME% >> "%LOG%"
exit /b 0
