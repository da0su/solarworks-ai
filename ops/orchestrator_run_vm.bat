@echo off
REM Plan v6 Phase C: VB 完結化版 orchestrator dispatch
REM
REM Phase C-2 cutover 後にこの bat に Task Scheduler を切り替える。
REM 旧 orchestrator_run.bat (HOST 実行版) は緊急時 fallback として retain。
REM
REM Usage: orchestrator_run_vm.bat <action> [--batch N] [--limit N]
REM Actions: post | like | followback | follow

setlocal
cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8

REM HOST 側 vm_controller を呼び出して VM HTTP server 経由で実行
REM (旧: HOST 内で run.py auto / scheduler dispatch)
python -m ops.vm_v6.vm_controller --mode %1 %2 %3 %4 %5 %6 %7 %8 %9 >> "C:\Users\infoa\Documents\solarworks-ai\ops\scheduler\logs\windows_task_vm_%1.log" 2>&1
exit /b %errorlevel%
