@echo off

cd /d "%~dp0\.."

python run.py plan --count 10
python run.py execute --limit 10

call "%~dp0notify_post_done.bat"

pause