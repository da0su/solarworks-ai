# VM v6 セットアップ手順書 (CEO 手動操作)

**Plan v4 P1 (VB完結化) の VM 内 setup を CEO が手動で完了させる手順**

---

## 前提条件

- [x] VM RoomBot 起動中 (Memory 16GB / CPU 4 cores)
- [x] HOST 側 ops/vm_v6/ 実装完了 (本日 commit 済)
- [x] Phase A-2 で 4 chrome profile 作成済 (chrome_profile_post/like/followback/follow)

---

## Step 1: VM 内に shared folder アクセス確認 (1分)

VM 内 cmd で:
```
dir \\vboxsvr\share
```
→ HOST `rakuten-room/bot/executor/` の中身が見えれば OK

## Step 2: VirtualBox Port Forward 設定 (HOST 側で 1回・2分)

HOST PowerShell (管理者):
```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' modifyvm RoomBot --natpf1 "vmhttp,tcp,,18765,,8765"
```

(VM 再起動が必要)

```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' controlvm RoomBot acpipowerbutton
# 30秒待機
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' startvm RoomBot --type gui
```

## Step 3: VM 内 setup wizard 実行 (CEO 手動 - 30-60分)

VM 内 cmd で:
```cmd
\\vboxsvr\share\..\..\..\ops\vm_v6\setup_vm_v6.bat
```

または HOST 上の path 経由:
```cmd
copy /Y "\\vboxsvr\share\..\..\..\ops\vm_v6\setup_vm_v6.bat" "%USERPROFILE%\Desktop\setup_vm_v6.bat"
"%USERPROFILE%\Desktop\setup_vm_v6.bat"
```

**Wizard が 7 step で自動実行**:
1. directory 作成 (`C:\Users\cyber\Desktop\rakuten_room_bot\`)
2. shared folder からコード copy
3. pip install (playwright, fastapi, uvicorn, requests, gspread, psutil)
4. Playwright Chromium インストール (5-10分)
5. 4 chrome profile を VM 内に copy
6. HTTP server をスタートアップ登録
7. HTTP server 即起動

## Step 4: HOST から動作確認 (5分)

HOST cmd で:
```cmd
python ops/vm_v6/vm_controller.py --status
```

期待出力:
```json
{
  "running": [],
  "heartbeats": {}
}
```

→ VM HTTP server が応答する

## Step 5: 4機能動作テスト (各 5分・順次)

### POST テスト (limit=5 で安全テスト)
```cmd
python ops/vm_v6/vm_controller.py --mode post --limit 5 --batch 1
```

### LIKE テスト
```cmd
python ops/vm_v6/vm_controller.py --mode like --limit 10
```

### FOLLOW テスト
```cmd
python ops/vm_v6/vm_controller.py --mode follow --limit 10 --force
```

### FOLLOWBACK テスト
```cmd
python ops/vm_v6/vm_controller.py --mode followback --limit 5
```

各テスト後:
```cmd
python ops/vm_v6/vm_controller.py --heartbeat <mode>
```
で進捗確認。

## Step 6: Task Scheduler refactor (HOST 側で 30分)

旧 RoomBot_* タスクを順次 vm_controller 経由に置換:

```powershell
powershell -ExecutionPolicy Bypass -File ops\scheduler\refactor_tasks_v6.ps1
```

(refactor_tasks_v6.ps1 は別途提供)

## Step 7: patrol_v6 を Task Scheduler に登録

```powershell
powershell -ExecutionPolicy Bypass -File ops\patrol_v6\set_patrol_v6_15min.ps1
```

## Step 8: 全 task 再有効化

```powershell
Get-ScheduledTask | Where-Object {$_.TaskName -like "RoomBot*"} | Enable-ScheduledTask
```

## Step 9: 1日連続稼働テスト

24時間で:
- HOST Chrome 占有時間: 0時間 (CEO Chrome を CEO が使い続けられる)
- 4機能とも目標達成
- patrol_v6 で CRITICAL 0件

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `vm_controller --status` で connection refused | VM 内 HTTP server 未起動。Step 7 (setup_vm_v6.bat 末尾) を再実行 |
| pip install で ssl error | VM 内 Python の cert 更新: `python -m pip install --upgrade certifi` |
| Playwright install で permission denied | VM 内 cmd を「管理者として実行」 |
| profile copy で disk full | VM disk 拡張 (現状 80GB)。`VBoxManage modifymedium --resize` |
| HTTP server 401 | env var BOT_API_TOKEN 未設定。`set BOT_API_TOKEN=rakuten-room-v6-secret` |

---

## 完了基準

- [ ] VM 内 Python + Playwright インストール完了
- [ ] 4 chrome profile が VM 内に存在
- [ ] HTTP server (port 8765) が常駐
- [ ] HOST から `vm_controller --status` で応答取得
- [ ] 4機能とも 1 セッション成功
- [ ] Task Scheduler が vm_controller 経由
- [ ] HOST Chrome 占有時間 0時間/日 達成
