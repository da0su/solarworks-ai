@echo off
REM VM 内 trigger 用 batch: comment_edit mode を VM 内で 1 回実行
REM
REM 配置場所 (VM 内):
REM   %USERPROFILE%\Desktop\rakuten_room_bot\trigger_comment_edit.bat
REM
REM 使い方:
REM   1. VM コンソール開く
REM   2. このファイル ダブルクリック → 5 件ずつ処理
REM   3. 終了したら戻り値で結果確認 (0=成功 / 4=失敗あり)
REM
REM 再実行:
REM   pending_comment_edit=1 が残っている限り 何度も実行 OK
REM   30 件 / 5 件 = 6 回程度の trigger で全件処理

setlocal
set BASE=%USERPROFILE%\Desktop\rakuten_room_bot
set PYTHONIOENCODING=utf-8
set PY=python

echo [trigger_comment_edit] start %DATE% %TIME%
echo [trigger_comment_edit] BASE=%BASE%

cd /d "%BASE%"
%PY% -m runner.rakuten_room_runner --mode comment_edit > "%BASE%\logs\comment_edit_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%.log" 2>&1

set RC=%ERRORLEVEL%
echo [trigger_comment_edit] done rc=%RC%
exit /b %RC%
