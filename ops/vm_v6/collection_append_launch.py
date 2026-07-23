#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HOST -> VM ランチャー: 直近24hの投稿をジャンルコレクションへ自動追加 (#27)。

毎日 22:30 に HOST Task Scheduler から呼ばれる (Batch3 21:00 の後)。
VM 内で collection_appender.py を DETACHED 起動する。
実行場所が VM 内 = host_chrome_forbidden_rule 遵守。
"""
import sys, io, urllib.request, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TOKEN = "rakuten-room-v6-secret"
VM_PY = r"C:\Users\cyber\AppData\Local\Programs\Python\Python312\python.exe"
SCRIPT = r"W:\collection_appender.py"


def main():
    passthrough = sys.argv[1:]
    cmd = ["cmd", "/c", "start", "/b", "", VM_PY, SCRIPT] + passthrough
    data = json.dumps({"cmd": cmd, "timeout": 30}).encode("utf-8")
    req = urllib.request.Request("http://localhost:18765/exec", data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=40)
        print("[collection_append] launched rc=", json.loads(r.read()).get("rc"))
    except Exception as e:
        print("[collection_append] launch dispatched (conn drop expected):",
              type(e).__name__)


if __name__ == "__main__":
    main()
