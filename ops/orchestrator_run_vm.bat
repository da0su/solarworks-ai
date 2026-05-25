@echo off
REM Plan v6 Phase C: orchestrator_run_vm.bat
REM Usage: orchestrator_run_vm.bat <action> [--batch N] [--limit N]
REM Actions: post | like | followback | follow
REM Fixed: CRLF line endings + explicit Python path (2026-05-25)

setlocal
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8

C:\Users\infoa\AppData\Local\Programs\Python\Python312\python.exe -m ops.vm_v6.vm_controller --mode %1 %2 %3 %4 %5 %6 %7 %8 %9 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_vm_%1.log" 2>&1
exit /b %errorlevel%
