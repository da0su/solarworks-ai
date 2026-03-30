# 楽天ROOM BOT — GPT 引き継ぎファイル

> **最終更新**: 2026-03-30
> **引き継ぎ元**: キャップさん
> **目的**: AIセッション開始時に即座に現状を把握できるようにする

---

## ▶ 現在の状態サマリー

| 項目 | 状態 |
|------|------|
| 稼働状況 | ✅ 本番稼働中（サイバーさんで自動運用） |
| 運用モード | AUTO（90〜100件/日） |
| アカウント | カピバラ癒し（room_e05d4d1c1e） |
| ログイン | storage_state.json で維持中 |
| スケジューラ | scheduler_auto.bat 登録済み |
| Slack通知 | 設定済み |

---

## ▶ 直近のアクション

| 日時 | 作業内容 |
|------|---------|
| 2026-03-30 | KNOWLEDGE.md / gpt_handoff.md / gpt_recovery_runbook.md 作成 |
| - | 本番稼働中。設定変更なし |

---

## ▶ 次にやること

| 優先度 | タスク |
|--------|--------|
| 低（必要時） | ログイン切れ → `python run.py login` |
| 低（必要時） | プール不足 → `python run.py replenish` |
| 監視 | 日次レポートをSlackで確認 |

---

## ▶ 重要ファイルパス

```
rakuten-room/bot/
├── run.py                          ← 全コマンドはここから
├── config.py                       ← 設定値（ROOM_ID, 投稿数等）
├── .env                            ← RAKUTEN_APP_ID / SLACK_WEBHOOK_URL
├── data/room_bot.db                ← SQLiteキュー（投稿管理）
├── data/state/storage_state.json   ← ログインセッション（重要！）
├── data/operation_mode.json        ← 運用モード
├── data/logs/YYYY-MM-DD.log        ← 日別ログ
└── scripts/scheduler_auto.bat      ← タスクスケジューラ起動bat
```

---

## ▶ 緊急時コマンド

```bash
# 停止
python run.py mode STOP

# セーフモード（20件上限）
python run.py mode SAFE

# 状況確認
python run.py queue-status
python run.py health

# ログイン切れ
python run.py login

# 手動実行（Batch1）
python run.py auto --batch 1
```

---

## ▶ 設計上の注意点

1. **Playwright persistent context**: Chrome起動時に `data/chrome_profile/` のcookieを使用。
   `storage_state.json` を削除すると**ログイン状態が失われる**。
2. **BOT対策**: 投稿数・間隔・開始時刻はすべてランダム化。固定値に変更禁止。
3. **SQLiteキュー**: 重複防止と失敗リトライ機能あり。キューが詰まったら `queue-status` で確認。
4. **運用モード**: デフォルトはSAFE（20件上限）。本番はAUTOに設定が必要。
5. **CEOはbatを押すだけ**: 全操作はbat経由。直接Pythonを実行するのはデバッグ時のみ。

---

## ▶ このBOTを担当するAIへ

- まず `KNOWLEDGE.md` を読んで全体像を把握
- 問題発生時は `gpt_recovery_runbook.md` を参照
- コード変更時は必ずBOT対策設計（ランダム化）を維持すること
- セレクタ変更（投稿失敗増加）は楽天ROOM側のUI変更が原因。`data/logs/` + `data/screenshots/` を確認
