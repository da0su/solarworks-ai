# Slack 自動受信 改修案 + 実装指示
作成日: 2026-04-04 | 作成者: キャップ

---

## 現状の問題

| 問題 | 影響 |
|---|---|
| キャップが #coin-cap-marke を手動確認しないと指示に気づかない | 依頼が放置される |
| 送信→終わりで双方向になっていない | 返信漏れが発生 |
| 「いつ確認されたか」が不明 | マーケが再送を繰り返す |

---

## 今すぐできる暫定策（Phase 1 / 手動確認）

### 対応内容
- キャップのセッション開始時に **必ず #coin-cap-marke の最新メッセージを確認**
- 未返信メッセージがあれば受領報告を最優先で返す
- 確認間隔: **セッション開始時 + 作業区切り毎**（目安: 1〜2時間に1回）

### 実装（今すぐ）
```python
# セッション開始スクリプト (coin_business/scripts/check_coin_channel.py)
# python check_coin_channel.py で最新5件を表示
import os, urllib.request, json
from dotenv import load_dotenv
load_dotenv()

token = os.environ.get('SLACK_BOT_TOKEN')
channel = 'C0AMLJU2GRW'  # #coin-cap-marke

req = urllib.request.Request(
    f'https://slack.com/api/conversations.history?channel={channel}&limit=5',
    headers={'Authorization': f'Bearer {token}'}
)
with urllib.request.urlopen(req) as r:
    res = json.loads(r.read())

for m in res.get('messages', []):
    user = m.get('user', m.get('bot_id', ''))
    text = m.get('text', '')[:100]
    print(f"[{m['ts']}] {user}: {text}")
```

---

## Phase 2: 定期自動確認（スケジューラー組み込み）

### 方式: ポーリング（30分毎）

```python
# coin_business/scripts/slack_monitor.py
# 未返信メッセージを検出 → Supabase に task として登録 → キャップに通知

import time
from datetime import datetime, timezone

LAST_READ_TS = None  # ファイルやDBで永続化

def poll_coin_channel():
    global LAST_READ_TS
    messages = fetch_messages(channel='C0AMLJU2GRW', oldest=LAST_READ_TS)

    new_tasks = [m for m in messages if m['user'] != BOT_USER_ID]  # 自分の投稿を除外

    for msg in new_tasks:
        # タスクとして登録
        register_task(msg)
        # 受領スタンプ（:eyes:）を付ける
        add_reaction(msg['ts'], 'eyes')

    LAST_READ_TS = messages[-1]['ts'] if messages else LAST_READ_TS
```

### スケジュール
```
毎30分: python slack_monitor.py
→ 新着メッセージをタスクDBに登録
→ :eyes: スタンプで受領確認
→ キャップの次回セッションで処理
```

---

## Phase 3: Webhook / Socket Mode（本格自動化）

### 方式: Slack Event API (Socket Mode)

```
マーケがメッセージ送信
    ↓ Slack Event API
coin_business/slack_listener.py が受信
    ↓
メッセージ種別を判定（依頼/承認/差し戻し/質問）
    ↓
Supabase の task_queue に登録
    ↓
キャップが次回セッションでタスクを取得・実行
    ↓
完了報告を #coin-cap-marke に投稿
```

### 実装要件
- `SLACK_APP_TOKEN` (xapp-) が必要（Socket Mode用）
- `coin_business/.env` に追加
- スコープ: `channels:history`, `reactions:write`, `chat:write`
- 監視チャンネル: `C0AMLJU2GRW` (#coin-cap-marke) 固定

---

## 監視対象メッセージの種類

| 種別 | 識別方法 | 処理 |
|---|---|---|
| 依頼 | 【マーケ⇒キャップ】で始まる | タスク登録 + :eyes: |
| 承認 | 「承認」「OK」「実行」 | 保留中タスクを APPROVED に更新 |
| 差し戻し | 「差し戻し」「NG」「修正」 | タスクを REJECTED に更新 + 理由記録 |
| 質問 | 「?」「教えて」「確認」 | 優先タスクとして登録 |
| BLOCKED | 「権限不足」「CEO対応」 | CEO_ESCALATION フラグ |

---

## タスクIDとの紐付け方法

```
Slackメッセージ ts: 1775272810.924639
→ task_id: slack_1775272810 （tsのピリオド以前）
→ Supabase task_queue に記録
→ キャップの処理ログにも同じIDを記録
→ 完了報告時に task_id を明記
```

---

## 監視が止まった時の検知方法

### 方法1: ハートビート
```python
# 毎朝8時に自動で #coin-cap-marke に投稿
# "【定時確認】キャップ稼働中 YYYY-MM-DD 08:00"
# マーケが受け取らなければ止まっていると判断
```

### 方法2: 最終確認タイムスタンプ
```python
# data/slack_monitor_state.json
{
  "last_checked_at": "2026-04-04T08:00:00Z",
  "last_message_ts": "1775272810.924639",
  "unread_count": 0
}
# 2時間以上更新がなければ異常とみなす
```

---

## 重い処理と監視処理の分離方針

| 処理 | 分類 | 優先度 |
|---|---|---|
| Slack監視（ポーリング/受信） | 軽量・常時 | 最高 |
| 受領報告・スタンプ | 軽量・即時 | 高 |
| スキャン処理（Noble/Heritage） | 重量・バッチ | 中 |
| HTML生成・アップロード | 重量・バッチ | 中 |
| DB一括更新 | 重量・バッチ | 中 |

**分離ルール:**
- Slack受信は独立プロセスで常時監視
- 重い処理はバックグラウンドで実行
- 監視プロセスが重い処理の完了を受け取ったら報告

---

## 実装ロードマップ

| フェーズ | 内容 | 難易度 | 優先度 |
|---|---|---|---|
| Phase 1（今すぐ） | セッション開始時の手動確認スクリプト | ★☆☆ | 🔴 最高 |
| Phase 2（今週） | 30分ポーリング + :eyes: スタンプ | ★★☆ | 🟡 高 |
| Phase 3（来週以降） | Socket Mode + タスクキュー自動登録 | ★★★ | 🟢 中 |

---

## 今すぐ実施すること（実装指示）

```bash
# 1. check_coin_channel.py を作成・実行確認
cd coin_business
python scripts/check_coin_channel.py

# 2. セッション開始時の確認を習慣化
# → CLAUDE.md に「セッション開始時に必ず check_coin_channel.py を実行」を追記

# 3. SLACK_APP_TOKEN を取得（Phase 2/3 の前提）
# → Slack App設定 → Socket Mode を有効化 → App Token (xapp-) を .env に追加
```
