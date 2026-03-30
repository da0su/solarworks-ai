# クロスマシン E2E テスト ランブック

実施日想定: 2026-03-29 以降
担当: キャップ（cap）+ サイバー（cyber）

---

## workflow 範囲の定義

```
【事前準備】
  git-pull  (cyber)
    └─ 目的: 最新コードをcyberに反映
    └─ workflow: 独立（ビジネスworkflowには含まない）

【本業務 workflow】  ←── workflow_id が一貫して引き継がれる範囲
  ebay-search  (cyber)
    └─ eBay落札候補をスキャン
    └─ 候補ファイル保存: ebay_candidates_YYYYMMDD_HHMMSS.json
    └─ Slackには上位10件スリム + ファイルパスのみ送信
  ↓  [auto]
  ebay-review  (cap)
    └─ 候補を受領・保存・表示
    └─ capの承認待ち
  ↓  [approve → auto]
  ceo-report  (cap)
    └─ #ceo-room に候補リスト送信
    └─ report_status=sent を記録
```

> **補足**: git-pull は事前デプロイ作業。ebay-search 送信時に新規 workflow_id が発番され、
> ebay-review → ceo-report まで同一 workflow_id が引き継がれる。

---

## Slack 長文対策 正式仕様

| 項目 | ルール |
|------|--------|
| Slack送信 | **要約のみ**（上位10件スリム候補 + カウント + ファイルパス） |
| 詳細保存 | **サイバーさん上**の `~/.slack_bridge/ebay_candidates_YYYYMMDD_HHMMSS.json` に全候補を保存 |
| Slackペイロード上限 | **3,500 chars** を超えないこと（上位10件スリムで ≈ 3,258 chars） |
| ファイル参照 | `payload.candidates_file` にサイバーさん上のパスを含める |
| workflow_id | `payload.workflow_id` に必ず含める |

---

## 前提チェック

### cap側
- [ ] `python slack_bridge.py state-summary` が正常に出力される
- [ ] `SENDER=cap` が設定されている (`~/.slack_bridge/sender.txt` の内容を確認)
- [ ] Slack接続確認: `python slack_bridge.py send --message "cap疎通確認" --sender cap`

### cyber側
- [ ] `python slack_bridge.py state-summary` が正常に出力される
- [ ] `SENDER=cyber` が設定されている (`~/.slack_bridge/sender.txt` の内容を確認)
- [ ] `coin_business/.env` に EBAY_APP_ID, EBAY_CERT_ID が設定されている
  - 未設定の場合: cyberで直接 `coin_business/.env` に追記
- [ ] watch ループが起動していること (`python slack_bridge.py watch`)

---

## eBay APIキー確認（cyber側）

```bash
# cyber側で実行
cd solarworks-ai
python -c "
from dotenv import load_dotenv; import os
load_dotenv('coin_business/.env')
for k in ['EBAY_APP_ID','EBAY_CERT_ID','EBAY_DEV_ID']:
    val = os.environ.get(k,'(missing)')
    print(f'{k}: {val[:20]}...' if len(val)>20 else f'{k}: {val}')
"
```

---

## E2E テスト手順

### Step 0: 事前デプロイ（git-pull）

> **これは本業務 workflow の外。事前準備として実施。**

```bash
# cap側で実行（cyberに最新コードを反映させる）
python slack_bridge.py send-task git-pull --to cyber
```

- cyber側ログに `ACK → DONE` が出たら完了
- この git-pull の workflow_id は本業務 workflow に引き継がれない

---

### Step 1: 両機 watch 起動確認

```bash
# cyber側（別ターミナル）
python slack_bridge.py watch

# cap側（別ターミナル）
python slack_bridge.py watch
```

両機で `Slack監視開始` ログを確認。

---

### Step 2: 本業務 workflow 開始 — ebay-search をキック

```bash
# cap側で実行（cyberにebay-searchをリクエスト）
python slack_bridge.py send-task ebay-search --to cyber
```

**確認ポイント:**
- Slackの #ai-bridge に `[cap] [TASK] ebay-search` が投稿される
- cyber側ログに `ACK送信: ebay-search` が出る
- **この時点で新規 workflow_id が発番される**

```bash
# cap側
python slack_bridge.py state-summary
# → current_tasks: [queued/acknowledged] ebay-search  wf=<新規ID>
```

---

### Step 3: ebay-search 完了 → ebay-review 自動起動

cyberで `ebay_auction_search.py` が実行される（最大10分）。
完了後 `[cyber] [DONE] ebay-search` がSlackに投稿される。

**候補あり場合（正式仕様確認）:**
- cyber側: 全候補を `~/.slack_bridge/ebay_candidates_YYYYMMDD_HHMMSS.json` に保存
- Slackには `[cyber] [TASK] ebay-review` が送信（上位10件スリム + ファイルパス）
- cap側 state-summary: ebay-review が QUEUED になる

**候補なし場合:**
- ebay-review は送信されない（正常）→ テスト終了

---

### Step 4: ebay-review 確認

```bash
# cap側
python slack_bridge.py state-summary
# → current_tasks に [acknowledged/running] ebay-review が見えるはず
```

ebay-review が完了すると cap側で DONE になる。
この時点では ceo-report は **未承認のためブロック** 状態。

```bash
# cap側で確認
python slack_bridge.py state-summary
# → system_status: idle  (ceo-report ブロック待機中)
```

ebay-review DONE 時に cap の `~/.slack_bridge/ebay_review_candidates.json` に保存されること、
`payload.candidates_file` にcyber上の詳細ファイルパスが含まれることを確認:

```bash
python -c "
import json
with open(r'%USERPROFILE%\.slack_bridge\ebay_review_candidates.json', encoding='utf-8') as f:
    d = json.load(f)
print('total_count:', d['total_count'])
print('displayed:', d['displayed_count'])
print('file_on_cyber:', d['candidates_file_on_cyber'])
print('workflow_id:', d['workflow_id'])
"
```

---

### Step 5: approve → ceo-report 自動起動

```bash
# cap側で実行
python slack_bridge.py approve --task ebay-review --by cap
```

**確認ポイント:**
```
[OK] ebay-review approved by cap
[AUTO] ceo-report -> cap (id=xxxxxxxx)
```

```bash
python slack_bridge.py state-summary
# → current_tasks: [queued] ceo-report  src=auto  review=approved
```

---

### Step 6: ceo-report 実行確認

cap側の watch が ceo-report を受信・実行する。
`~/.slack_bridge/ebay_review_candidates.json` の内容を #ceo-room に送信。

**確認ポイント:**
- #ceo-room に候補リストが投稿される
- `[cap] [DONE] ceo-report 完了` が Slack に出る

```bash
python slack_bridge.py state-summary
# → recent_history: [done] ceo-report  report=sent  ch=C0ALSAPMYHY
```

---

### Step 7: workflow_id 一貫性確認

```bash
python -c "
from slack_bridge import StateManager
mgr = StateManager()
state = mgr.load()
history = state.get('recent_history', [])
for name in ['ebay-search', 'ebay-review', 'ceo-report']:
    t = next((x for x in history if x['task_name']==name and x['status']=='done'), None)
    wf = (t or {}).get('workflow_id','NONE')[:16]
    print(f'{name:<14} wf={wf}')
"
```

**期待値: ebay-search / ebay-review / ceo-report の wf= が同一8桁**

> git-pull は事前準備のため wf= が異なるのは正常。

---

### Step 8: 最終 state-audit

```bash
python slack_bridge.py state-audit
```

期待出力:
```
[1] Internal consistency: 0 issue(s) - OK
[2] Stuck DONE tasks: 0 issue(s) - OK
[3] Retriable errors not retried: 0 - OK
[4] Slack DONE not in state: 0 - OK
[5] Blocked tasks resolvable: 0 - OK
[6] Duplicate auto-enqueue: 0 - OK
Result: CLEAN
```

---

## 障害対応

| 症状 | 原因候補 | 対処 |
|------|---------|------|
| cyberがACK返さない | watch未起動 / Slack token不正 | cyber側 watch 確認・再起動 |
| ebay-search CONFIG_MISSING | cyber .env に EBAY_APP_ID 未設定 | cyber側で coin_business/.env に直接追記 |
| ebay-search Script not found | ebay_auction_search.py が存在しない | git pull 再実行 |
| TASK がSlack上で分割される | ペイロードが4000文字超 | candidates_file 仕様で修正済み（上位10件スリム） |
| ebay-review BLOCKED のまま | 旧コードで競合状態発生 | cyber watchを再起動（最新コードをロード） |
| ceo-report BLOCKED のまま | approve 未実施 | `python slack_bridge.py approve --task ebay-review --by cap` |
| state-audit に WARN | timeout超過タスクが残存 | 対象タスクを確認・手動で retry_count=max_retries に設定 |
| workflow_id が途中で変わる | 稀に enqueue_next で parent_wf_id 未検索 | v3.1以降で修正済み（parent_workflow_id 優先継承） |

---

## 24時間 watch 安定稼働確認ポイント

```bash
# 定期確認コマンド（cap / cyber 共通）

# 1. watch プロセス生存確認
tasklist | findstr python    # Windows
# または ps aux | grep slack_bridge  # Linux/Mac

# 2. ログサイズ確認（5MB でローテーション）
python -c "
import os
p = os.path.expanduser(r'~/.slack_bridge/bridge.log')
s = os.path.getsize(p) if os.path.exists(p) else 0
print(f'bridge.log: {s//1024}KB')
"

# 3. events.jsonl 行数確認（10,000行で自動刈り込み）
python -c "
import os
p = os.path.expanduser(r'~/.slack_bridge/events.jsonl')
n = sum(1 for _ in open(p, encoding='utf-8')) if os.path.exists(p) else 0
print(f'events.jsonl: {n}行')
"

# 4. state 健全性
python slack_bridge.py state-audit

# 5. 最終更新時刻
python -c "
from slack_bridge import StateManager
s = StateManager().load()
print('last update:', s.get('updated_at','?'))
print('status:', s.get('system_status','?'))
"
```

---

## 完了条件

- [ ] Slack上でACK/DONE/BLOCKED/approve/report sentが確認できた
- [ ] workflow_id が ebay-search → ebay-review → ceo-report で同一だった
- [ ] 候補ファイルが cyber の `~/.slack_bridge/ebay_candidates_*.json` に保存されていた
- [ ] Slackメッセージは 4000 文字以内に収まっていた
- [ ] 最終 state-audit が CLEAN だった
- [ ] bridge.log が肥大していない（5MB未満 or ローテーション済み）
