# Phase C-9: VM HTTP webhook 化 設計書

**作成日**: 2026-05-05 | **見積工数**: 1週間 | **優先度**: 中

## 目的

shared folder (`\\VBOXSVR\share\`) を VM ↔ HOST の通信路として使う SPOF (single point of failure) を解消し、
HTTP webhook 通信に置き換える。

## 現状の問題

```
HOST (cyber)              VM (RoomBot)
   |                          |
   +--- \\VBOXSVR\share\ <----+
        ├── follow_heartbeat.json   (VM 30秒書き込み, HOST 15分polling)
        ├── follow_rpa_log.json     (VM session 終了時, HOST 15分polling)
        ├── seed_users.json         (HOST → VM copy at launch)
        ├── follow_rpa_vm.py        (HOST → VM copy at launch)
        └── login_expired_flag.json (VM → HOST notify)
```

問題:
- shared folder 接続失敗時、全通信停止 (SPOF)
- file system polling は遅い (heartbeat 検知が 15分後)
- network drive 不安定時に partial write 発生可能性
- VM crash の検知が「heartbeat 更新停止」依存 (3分かかる)

## webhook ベース新設計

### VM 内 HTTP server (FastAPI/Flask)

```python
# VM 上の vm_status_server.py (新規・port 8765)
@app.post("/heartbeat")
def receive_heartbeat(payload: dict):
    # HOST → VM へ send は通常無し。VM が自分の heartbeat を broadcast する逆方向

@app.get("/status")
def get_status():
    return {"phase": ..., "metrics": ..., "screenshot_url": "/screenshot.png"}

@app.get("/screenshot.png")
def get_screenshot():
    # 現時点の VM 画面 PNG
    return FileResponse(...)

@app.post("/abort")
def abort_session(reason: str):
    # HOST → VM への止め命令
    ...
```

### HOST 側 webhook receiver

```python
# ops/scheduler/vm_status_listener.py (新規)
@app.post("/vm/heartbeat")
def vm_heartbeat(payload: dict):
    # VM が push する heartbeat を即時 DB に書く
    save_heartbeat_to_db(payload)
    if payload.get("stuck"):
        trigger_recovery()
```

## 移行ステップ

### Step 1: VM 起動時に webhook server 起動 (3日)
- `vm_follow_launcher.py` で `python vm_status_server.py &` を VM 内 spawn
- Port 8765 を VirtualBox で port forward (host 18765 → guest 8765)

### Step 2: heartbeat を webhook push 化 (2日)
- VM bot 内 `write_heartbeat()` を webhook POST に変更
- patrol_hourly は HOST 上 status DB を read

### Step 3: shared folder 段階削除 (2日)
- seed_users.json は webhook GET で VM が pull
- follow_rpa_log.json は session 終了時 webhook POST
- shared folder は debug/screenshot 専用に縮小

## 期待効果

| 項目 | 現状 | webhook 後 |
|------|------|-----------|
| stuck 検知 | 3分 (file polling) | 30秒 (push notify) |
| SPOF | shared folder | 無し (HTTP retry) |
| VM crash 検知 | heartbeat 停止検知 (3分) | TCP connection lost (即時) |
| screenshot 取得 | shared folder 経由 | webhook GET (即時) |

## リスク

- VirtualBox port forward 設定変更が必要
- VM 内 Python HTTP server の安定性 (Playwright と同居)
- Windows Firewall の port 開放

## 完了基準

- shared folder write が 0 件/時間
- heartbeat lag < 60 秒
- VM stuck 検知 < 60 秒
