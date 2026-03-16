# フォロー機能 設計メモ（次フェーズ用）

Phase 2 で実装予定。現時点ではコード変更なし。

---

## 実装ファイル案

```
bots/room_bot/
├── executor/
│   └── follow_executor.py    ← 新規作成
├── run.py                    ← follow / daily-follow コマンド追加
└── config.py                 ← 設定済み（変更不要）
```

## 実行フロー案

```
run.py follow
  └→ FollowExecutor
       ├→ ROOMトップ → おすすめユーザー一覧
       ├→ 1ユーザーずつフォローボタンをクリック
       ├→ 1〜3秒間隔（ランダム）
       ├→ 10〜15件ごとに10〜30秒休憩
       ├→ 50件で1セッション終了 → 5〜15分休憩
       └→ 4セッション繰り返し → 日次完了
```

## 1日200件対応の設計（config.py に設定済み）

| 設定 | 値 | 意味 |
|------|-----|------|
| FOLLOW_DAILY_MIN | 150 | 日次最小フォロー数 |
| FOLLOW_DAILY_MAX | 250 | 日次最大フォロー数 |
| FOLLOW_SESSION_MAX | 50 | 1セッション最大件数 |
| FOLLOW_SESSIONS_PER_DAY | 4 | 1日のセッション数 |
| FOLLOW_INTERVAL_MIN | 1.0秒 | フォロー間隔（最小） |
| FOLLOW_INTERVAL_MAX | 3.0秒 | フォロー間隔（最大） |
| FOLLOW_REST_EVERY_MIN | 10件 | 休憩を入れる間隔 |
| FOLLOW_REST_DURATION_MIN | 10秒 | 休憩時間（最小） |
| FOLLOW_SESSION_REST_MIN | 300秒 | セッション間休憩（最小） |

## scheduler 統合案

```python
# scheduler.py に追加
FOLLOW_TIMES = ["07:00", "14:00"]  # フォロー実行時刻

# run.py に追加
# python run.py follow         # フォロー実行
# python run.py daily-all      # 投稿 + フォロー 一括
```

## 実装時の注意

- フォロー済みユーザーの重複防止（follow_history.json）
- アカウント凍結リスク → 人間的な間隔・休憩の徹底
- フォロー上限到達時の自動停止
- フォローバック率のログ記録（将来の分析用）
