# クロスマシン E2E テスト ランブック

実施日想定: 2026-03-29 以降
担当: キャップ（cap）+ サイバー（cyber）
目的: git-pull → ebay-search → ebay-review → approve → ceo-report を実Slack上で1本通す

---

## 前提チェック

### cap側
- [ ] `python slack_bridge.py state-summary` が正常に出力される
- [ ] `SENDER=cap` が設定されている (`data/sender.txt` の内容を確認)
- [ ] Slack接続確認: `python slack_bridge.py send --message "cap疎通確認" --sender cap`

### cyber側
- [ ] `python slack_bridge.py state-summary` が正常に出力される
- [ ] `SENDER=cyber` が設定されている (`data/sender.txt` の内容を確認)
- [ ] `coin_business/.env` に EBAY_APP_ID, EBAY_CERT_ID が設定されている
  - 未設定の場合: capから `python slack_bridge.py send-task set-env --to cyber` でpush
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

キーが missing の場合、cap側から set-env を送信:
```bash
# cap側で実行
python slack_bridge.py send-task set-env --to cyber
```

---

## E2E テスト手順

### Step 1: 両機 watch 起動確認

```bash
# cyber側（別ターミナル）
python slack_bridge.py watch

# cap側（別ターミナル）
python slack_bridge.py watch
```

両機で `Slack監視開始` ログを確認。

---

### Step 2: git-pull をキック

```bash
# cap側で実行
python slack_bridge.py send-task git-pull --to cyber
```

**確認ポイント:**
- Slackの #ai-bridge に `[cap] [TASK] git-pull` が投稿される
- cyber側ログに `ACK送信: git-pull` が出る
- cap側で `python slack_bridge.py state-summary` を実行:
  ```
  system_status: busy
  current_tasks: [queued/acknowledged] git-pull
  ```
- cyberで git pull が実行されて完了すると `[cyber] [DONE] git-pull 完了` がSlackに投稿される
- cyberの state-summary に `recent_history: [done] git-pull` が出る

---

### Step 3: ebay-search の自動起動確認

git-pull の DONE受信後、cap 側で自動的に ebay-search が enqueue される。

**確認ポイント (cap側):**
```bash
python slack_bridge.py state-summary
```
```
system_status: busy
current_tasks: [queued] ebay-search  src=auto  wf=<同じworkflow_id>
```

Slackに `[cap] [TASK] ebay-search [A]` が投稿されること (`[A]`=auto)。

---

### Step 4: ebay-search 完了 → ebay-review 自動起動

cyberで `ebay_auction_search.py` が実行される（最大10分）。
完了後 `[cyber] [DONE] ebay-search` がSlackに投稿される。

**候補あり場合:**
- `[cyber] [TASK] ebay-review` が自動でcap宛てに送信される
- cap側: `python slack_bridge.py state-summary` で ebay-review が QUEUED になっているか確認

**候補なし場合:**
- ebay-search は DONE だがebay-reviewは送信されない（正常）
- ceo-report は実行不要 → テスト終了

---

### Step 5: ebay-review 確認

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
# → system_status: idle (ceo-report はブロック済みで待機)
```

---

### Step 6: approve → ceo-report 自動起動

```bash
# cap側で実行
python slack_bridge.py approve --task ebay-review --by cap
```

**確認ポイント:**
```
[OK] ebay-review approved by cap
[AUTO] ceo-report -> cap (id=xxxxxxxx)
```

Slackに `[cap] [TASK] ceo-report [A]` が自動投稿される。

```bash
python slack_bridge.py state-summary
# → current_tasks: [queued] ceo-report  src=auto  review=approved
```

---

### Step 7: ceo-report 実行確認

cap側の watch が ceo-report を受信・実行する。
`coin_business/data/ebay_review_candidates.json` の内容を #ceo-room に送信。

**確認ポイント:**
- #ceo-room に候補リストが投稿される
- Slackに `[cap] [DONE] ceo-report 完了` が出る

```bash
python slack_bridge.py state-summary
# → recent_history: [done] ceo-report  report=sent  ch=C0ALSAPMYHY
```

---

### Step 8: workflow_id 一貫性確認

```bash
python slack_bridge.py state-summary
```

`recent_history` に表示される全タスク（git-pull/ebay-search/ebay-review/ceo-report）の `wf=` が同一8桁であることを確認。

---

### Step 9: 最終 state-audit

```bash
python slack_bridge.py state-audit
```

期待出力:
```
[1] Internal consistency: 0 issue(s) - OK
[2] Stuck DONE tasks: 0 issue(s) - OK
...
OVERALL: CLEAN
```

---

## 障害対応

| 症状 | 原因候補 | 対処 |
|------|---------|------|
| cyberがACK返さない | watch未起動 / Slack token不正 | cyber側 watch 確認・再起動 |
| ebay-search CONFIG_MISSING | cyber .env に EBAY_APP_ID 未設定 | cap から set-env タスク送信 |
| ebay-search Script not found | ebay_auction_search.py が存在しない | git pull 再実行 |
| ceo-report BLOCKED のまま | approve 未実施 | `python slack_bridge.py approve --task ebay-review --by cap` |
| state-audit に WARN | timeout超過タスクが残存 | 対象タスクを確認・手動 done/error 移行 |
| workflow_id が途中で変わる | ebay-search の review_msg で wf未継承 | v3.1以降で修正済み |

---

## 24時間 watch 稼働確認ポイント

- `data/bridge.log` のサイズ: 5MB でローテーション（bridge.log.1 等が作成される）
- `data/events.jsonl` の行数: 10,000行超で自動削除（古い半分を削除）
- `python slack_bridge.py state-summary` が壊れていないこと
- `python slack_bridge.py state-audit` が CLEAN であること
- プロセスが生きていること（watchdog 経由で自動再起動する設計）

---

## 完了条件

- [ ] Slack上でACK/DONE/BLOCKED/approve/report sentが確認できた
- [ ] workflow_id が git-pull から ceo-report まで同一だった
- [ ] 最終 state-audit が CLEAN だった
- [ ] bridge.log が肥大していない（5MB未満 or ローテーション済み）
