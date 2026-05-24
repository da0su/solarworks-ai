@echo off
REM Host 側 1 分毎実行: VM RoomBot に no-op キーストローク送信で sleep 防止
REM Task Scheduler RoomBot_KeepAwake_1min で 1 分間隔 trigger

"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" controlvm RoomBot keyboardputscancode 38 b8 > nul 2>&1
exit /b 0
