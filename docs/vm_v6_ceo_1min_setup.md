# VM v6 セットアップ — CEO 1分手動協力

**所要時間**: VM 内操作 1分 + 自動進行 30-60分待機

## 前提

私 (Claude) が **既に完了済**:
- VM RoomBot 起動済 (Memory 16GB / CPU 4 cores / RunLevel=3)
- 新 sharedfolder 追加済 (`vm_v6` / `vm_data`)
- `\\vboxsvr\vm_v6\setup_vm_v6.bat` が VM 内から見える状態
- HOST 側コード完全実装済
- Task Scheduler 全 disabled (VM 完結移行のため)

## CEO 手動作業 (1分)

### 1. VM RoomBot ウィンドウをクリックして foreground にする

VirtualBox 上で `RoomBot` の VM ウィンドウを 1回クリック。

### 2. VM 内で cmd を起動 (キーボードで)

```
Win + R → 「cmd」と入力 → Enter
```

### 3. setup bat を実行

VM 内 cmd で以下を入力 → Enter:

```
\\vboxsvr\vm_v6\setup_vm_v6.bat
```

(コピペ可)

### 4. 完了通知を待つ (15-30分)

bat が以下を順次自動実行:
- mkdir / コード copy / pip install / Playwright install / 4 profile copy / HTTP server 起動

最後に `SETUP DONE - HTTP server on port 8765` と表示されれば完了。

### 5. CEO は何もしないで OK

完了後、HOST 上で私が自動で以下を実行:
- HTTP server 疎通確認
- Task Scheduler refactor
- 全 task 再有効化
- patrol_v6 動作確認
- CEO Slack 報告 #362

## CEO 報告 trigger

CEO は完了後 Slack で「**v6 setup ok**」とだけ送信、または何も送信しなくても私が定期 polling で完了検知 (HTTP healthz 経由)。

---

## トラブル時

### 「指定されたパスが見つかりません」
- VM 再起動: VirtualBox メニュー → コントロール → 再起動 (Ctrl+H で送信可)
- 再起動後 1分待ってから Step 2-3 やり直し

### bat 実行中エラー
- screenshot を CEO Slack に共有
- 私が即対応

### 30分以上経っても完了しない
- VM 内 cmd window で Enter 1回押下 (画面更新)
- screenshot 取得して状況確認依頼
