@echo off
cd /d "C:\Users\砂田　紘幸\solarworks-ai\coin_business"
echo [%DATE% %TIME%] socket_listener starting... >> data\slack_heartbeat.log
python -X utf8 scripts\slack_socket_listener.py
