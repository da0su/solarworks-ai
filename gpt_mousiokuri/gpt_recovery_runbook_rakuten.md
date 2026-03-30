# 楽天ROOM BOT — GPT 復旧ランブック

> **最終更新**: 2026-03-30
> **目的**: ゼロ知識からでも30分以内に楽天ROOM BOTを復旧・再起動できる手順書

---

## 0. セッション開始時に最初に読むファイル順

1. **このファイル** (`gpt_recovery_runbook.md`) — 復旧手順
2. `KNOWLEDGE.md` — 全体知識ベース
3. `config.py` — 設定値確認（ROOM_ID、投稿数等）
4. `data/logs/YYYY-MM-DD.log` — 最新ログ確認

---

## 1. このプロジェクトの目的

楽天ROOMアカウント「カピバラ癒し」（room_e05d4d1c1e）で商品を自動投稿し、
クリック→購入によるアフィリエイト報酬を得る事業。

**稼働中の自動化:**
- 毎日0:00〜0:30 → Batch1（50件）投稿
- 毎日7:00〜12:00 → Batch2（残り40〜50件）投稿
- 合計: 90〜100件/日（BOT対策でランダム）

---

## 2. 環境確認

### 2-1. 基本確認

```bash
cd C:\Users\砂田　紘幸\solarworks-ai\rakuten-room\bot

# Pythonバージョン確認
python --version
# → Python 3.x.x

# Playwright確認
python -c "import playwright; print('OK')"
# → OK

# 設定確認
python -c "import config; print(config.ROOM_ID, config.ACCOUNT_NAME)"
# → room_e05d4d1c1e カピバラ癒し
```

### 2-2. .env確認

```bash
# .envが存在するか確認（中身は表示しない）
python -c "from pathlib import Path; print('OK' if (Path('rakuten-room/bot/.env').exists()) else 'NG — .env未設定')"
```

**必須キー:**
- `RAKUTEN_APP_ID` — 楽天API（商品取得に必要）
- `SLACK_WEBHOOK_URL` — Slack通知（通知が来ない場合に確認）

### 2-3. セッション確認

```bash
# ログインセッションが存在するか確認
python -c "
from pathlib import Path
p = Path('data/state/storage_state.json')
print('✅ セッションあり' if p.exists() else '❌ セッションなし → ログイン必要')
"
```

---

## 3. ログイン（セッション再構築）

### 状況: ログイン切れ / セッションファイルなし

```bash
cd rakuten-room/bot

# 1. ログイン実行（Chrome画面が開く）
python run.py login

# 2. 開いたChromeで楽天ROOMに手動ログイン
# 3. ログイン完了後、コンソールでEnter
# → data/state/storage_state.json に保存される
```

**注意**: ログインはCEO（またはキャップ）が手動で実施。
`storage_state.json` が存在すれば次回以降は自動。

---

## 4. 状況確認コマンド

```bash
cd rakuten-room/bot

# キュー状況（何件待ちか）
python run.py queue-status

# 異常検知
python run.py health

# 日次レポート確認
python run.py report
```

---

## 5. 手動実行（緊急時）

### Batch1（深夜0時台）を手動実行
```bash
cd rakuten-room/bot
python run.py auto --batch 1
```

### Batch2（朝〜昼）を手動実行
```bash
cd rakuten-room/bot
python run.py auto --batch 2
```

### 全自動（replenish→plan→execute→report）
```bash
cd rakuten-room/bot
python run.py auto
```

---

## 6. 商品プール補充

```bash
cd rakuten-room/bot

# プール確認（queue-statusで現在件数確認）
python run.py queue-status

# 楽天APIから商品補充
python run.py replenish

# 監査結果確認
python -c "
import json
from pathlib import Path
r = json.loads(Path('data/audit/audit_results.json').read_text(encoding='utf-8'))
print(f'pass: {r.get(\"pass_count\",0)}件 / review: {r.get(\"review_count\",0)}件 / fail: {r.get(\"fail_count\",0)}件')
" 2>/dev/null || echo "監査ファイル未生成"
```

**プール管理基準:**
- 最低: 300件（これを下回ると補充）
- 最大: 600件（超過時は低スコア順に削除）

---

## 7. 運用モード管理

```bash
cd rakuten-room/bot

# 現在のモード確認
python -c "
import config, json
from pathlib import Path
m = config.get_operation_mode()
print(f'現在モード: {m[\"mode\"]} / safe_limit: {m[\"safe_limit\"]}件 / 更新: {m[\"updated_at\"]}')
"

# 本番モード（90〜100件/日）に切り替え
python run.py mode AUTO

# セーフモード（20件上限）に切り替え
python run.py mode SAFE

# 完全停止
python run.py mode STOP
```

---

## 8. スケジューラ（自動起動）

**Windowsタスクスケジューラに登録済み:**
- `scripts/scheduler_auto.bat` — 自動運用の起点

```bash
# タスクスケジューラの確認（PowerShell）
Get-ScheduledTask | Where-Object {$_.TaskName -like "*room*"} | Select-Object TaskName, State
```

**手動でBAT実行:**
```
scripts/scheduler_auto.bat  ← ダブルクリックでも可
```

---

## 9. ログ確認

```bash
cd rakuten-room/bot

# 今日のログを確認
type data\logs\2026-03-30.log

# 最新スクリーンショット確認（エクスプローラーで開く）
# data/screenshots/YYYY-MM-DD/ 以下に保存

# 投稿履歴確認
python -c "
import json
from pathlib import Path
p = Path('../../05_CONTENT/rakuten_room/history/POST_LOG.json')
if p.exists():
    logs = json.loads(p.read_text(encoding='utf-8'))
    recent = logs[-5:] if len(logs) > 5 else logs
    for r in recent:
        print(f'{r.get(\"date\",\"?\")} | {r.get(\"status\",\"?\")} | {r.get(\"product_url\",\"\")[:50]}')
else:
    print('POST_LOG.json なし')
"
```

---

## 10. 異常時の停止条件

以下のケースでは**即座に停止**し、CEOに報告すること。

| 条件 | 停止コマンド |
|------|------------|
| **A** 楽天ROOMアカウント凍結疑い | `python run.py mode STOP` |
| **B** 連続投稿失敗（10件以上） | `python run.py mode STOP` |
| **C** ログが異常終了（エラー連続） | `python run.py mode STOP` |
| **D** セレクタ変更で全件失敗 | `python run.py mode STOP` |
| **E** `run.py mode STOP`以外での強制停止 | 報告のみ（既に停止済み） |
| **F** `.env`や`storage_state.json`の外部出力 | 即時停止・報告 |
| **G** 楽天アカウント情報・パスワードを聞かれた場合 | 絶対に入力しない |
| **H** 同一エラーが3回以上連続 | `python run.py mode SAFE` に切り替え |

---

## 11. 典型的トラブルと対処

### ケース1: 「投稿が全件失敗している」

```bash
# 1. ログ確認
type data\logs\YYYY-MM-DD.log

# 2. スクリーンショット確認
# data/screenshots/YYYY-MM-DD/ を開く

# 3. ログイン確認
python -c "
from pathlib import Path
print('✅' if Path('data/state/storage_state.json').exists() else '❌ ログイン必要')
"

# 4. ログイン切れなら再ログイン
python run.py login
```

**失敗パターン:**
- ログイン切れ → `python run.py login`
- セレクタ変更（楽天ROOM UI更新） → 開発者に連絡、`executor/selectors.py` を確認
- ネットワーク問題 → しばらく待ってリトライ

---

### ケース2: 「キューが空で投稿できない」

```bash
# 商品プール状況確認
python run.py queue-status

# 計画を再生成
python run.py plan

# それでも空なら補充
python run.py replenish
python run.py plan
python run.py execute
```

---

### ケース3: 「スケジューラが起動しない」

```bash
# 手動実行でテスト
cd rakuten-room/bot
python run.py auto --batch 1

# エラーがなければ、スケジューラ再登録
python run.py scheduler-setup
```

---

### ケース4: 「Slack通知が来ない」

```bash
# .envのSLACK_WEBHOOK_URL確認（値があるかだけ確認）
python -c "
import os
from pathlib import Path
env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = bool(v.strip())
print('SLACK_WEBHOOK_URL:', '設定済み' if env.get('SLACK_WEBHOOK_URL') else '未設定')
"
```

---

## 12. 復旧確認チェックリスト

復旧後、以下を確認してから運用に戻す:

```
□ python run.py health → エラーなし
□ python run.py queue-status → キューに件数あり
□ data/state/storage_state.json → 存在する
□ data/operation_mode.json → mode: "AUTO"
□ 試験投稿（1件）成功
□ Slack通知 → 届いている
```

---

## クイックリファレンス

```bash
# 全体確認
python run.py health && python run.py queue-status

# 緊急停止
python run.py mode STOP

# 全自動再起動
python run.py mode AUTO && python run.py auto

# ログイン再実行
python run.py login

# プール補充→計画→実行
python run.py replenish && python run.py plan && python run.py execute
```

---

## サイバーさん共有記録

**最終更新: 2026-03-30 キャップさん**

- 楽天ROOM BOT v5.0 本番稼働中
- scheduler_auto.bat でWindowsタスクスケジューラ登録済み
- 投稿: 90〜100件/日 Batch1(0:00〜)/Batch2(7:00〜12:00)
- ログイン状態: `data/state/storage_state.json` で維持
- **注意**: このファイルを削除するとログイン状態が失われる
- KNOWLEDGE.md / gpt_handoff.md / gpt_recovery_runbook.md を新規作成（横展開の一環）
