# 楽天ROOM BOT — KNOWLEDGE.md

> **最終更新**: 2026-03-30
> **バージョン**: v5.0（完全自動運用）
> **このファイルの目的**: ゼロ知識から楽天ROOM BOTを完全に理解・復元するための知識ベース

---

## 1. 事業概要

楽天ROOMアカウント「**カピバラ癒し**」（ID: `room_e05d4d1c1e`）で、
楽天商品を自動投稿・自動フォローすることで収益を得るアフィリエイト事業。

**収益モデル**: 投稿商品がクリック→購入されると楽天アフィリエイト報酬が発生。
**日次目標**: 90〜100件/日の投稿（BOT対策でランダム化）
**自動化**: Playwright + Python で DOM操作による完全自動化

---

## 2. 技術スタック

| 要素 | 内容 |
|------|------|
| 言語 | Python 3.x |
| ブラウザ自動化 | Playwright (persistent context) |
| データ管理 | SQLite (`data/room_bot.db`) |
| セッション保存 | `data/state/storage_state.json` |
| 設定 | `config.py` + `.env` |
| 通知 | Slack Webhook |
| スケジューラ | `scripts/scheduler_auto.bat` → Windowsタスクスケジューラ |

---

## 3. ディレクトリ構成

```
rakuten-room/bot/
├── run.py                          ← CLIエントリーポイント（全コマンド集約）
├── config.py                       ← 全設定値（ここを読めば全体が分かる）
├── .env                            ← RAKUTEN_APP_ID / SLACK_WEBHOOK_URL
├── data/
│   ├── room_bot.db                 ← SQLite（投稿キュー・商品プール）
│   ├── state/storage_state.json    ← Playwrightセッション（ログイン状態）
│   ├── chrome_profile/             ← Chromeプロファイル（cookie）
│   ├── logs/YYYY-MM-DD.log         ← 日別ログ
│   ├── screenshots/YYYY-MM-DD/     ← 投稿時スクリーンショット
│   ├── operation_mode.json         ← 運用モード（AUTO/SAFE/STOP）
│   ├── audit/audit_results.json    ← 商品監査結果
│   └── reports/                    ← 日次レポート
├── executor/                       ← 実行エンジン
│   ├── browser_manager.py          ← Playwright起動・ログイン管理
│   ├── post_executor.py            ← 投稿DOM操作
│   ├── batch_runner.py             ← バッチ実行制御
│   ├── comment_generator.py        ← AI投稿コメント生成
│   ├── post_scorer.py              ← 商品スコアリング
│   ├── follow_executor.py          ← フォロー自動化
│   ├── like_executor.py            ← いいね自動化
│   └── room_controller.py          ← ROOM UI制御
├── planner/                        ← キュー・計画管理
│   ├── queue_manager.py            ← SQLiteキュー管理
│   ├── queue_executor.py           ← キュー実行
│   ├── daily_planner.py            ← 投稿計画生成
│   ├── daily_schedule.py           ← スケジュール管理
│   ├── pool_manager.py             ← 商品プール管理（重複除去）
│   ├── product_fetcher.py          ← 楽天API商品取得
│   └── item_auditor.py             ← 商品監査（pass/review/fail）
├── monitor/
│   ├── health_checker.py           ← 異常検知
│   ├── daily_report.py             ← 日次レポート生成
│   └── slack_notifier.py           ← Slack通知
└── scripts/                        ← CEO用batファイル
    ├── scheduler_auto.bat          ← 自動運用の起点（最重要）
    ├── run_room_login.bat          ← 初回ログイン
    └── ...（その他bat）

外部ログ:
05_CONTENT/rakuten_room/history/POST_LOG.json   ← 投稿履歴
05_CONTENT/rakuten_room/history/FOLLOW_LOG.json ← フォロー履歴
05_CONTENT/rakuten_room/history/daily/          ← 日別サマリー
```

---

## 4. 主要コマンド

### 完全自動運用（本番）
```bash
cd rakuten-room/bot

# 全自動（replenish→plan→execute→report）
python run.py auto

# バッチ指定
python run.py auto --batch 1          # Batch1のみ（0:00〜）
python run.py auto --batch 2          # Batch2のみ（7:00〜12:00）
```

### 個別コマンド
```bash
python run.py login                   # 初回ログイン（手動・セッション保存）
python run.py replenish               # 商品プール補充（楽天API）
python run.py plan                    # 投稿計画生成
python run.py execute --limit 50      # キューから50件実行
python run.py daily                   # plan + execute 一括
python run.py queue-status            # キュー状況確認
python run.py health                  # 異常検知
python run.py report                  # 朝レポート生成
python run.py report --type night --slack   # 夜レポート+Slack送信
python run.py mode AUTO               # 運用モード変更
python run.py mode SAFE               # セーフモード（20件上限）
python run.py mode STOP               # 停止
```

### BAT経由（CEO用）
```
scripts/scheduler_auto.bat    ← Windowsタスクスケジューラに登録済み
scripts/run_room_login.bat    ← ログイン切れ時
```

---

## 5. 投稿フロー詳細

```
[楽天API] → product_fetcher.py
    ↓ 商品取得（ジャンル別）
[item_auditor.py]
    ↓ 監査（pass/review/fail分類）
[pool_manager.py]
    ↓ プール管理（重複除去、300〜600件維持）
[daily_planner.py]
    ↓ 今日の投稿計画生成（SQLiteキュー登録）
[queue_executor.py]
    ↓ キューからバッチ実行
[post_executor.py] + [browser_manager.py]
    ↓ Playwright DOM操作 → 楽天ROOMに投稿
[slack_notifier.py]
    ↓ Slack通知
[daily_report.py]
    → 日次レポート生成
```

---

## 6. 設定値一覧（config.py）

| 設定項目 | 値 | 説明 |
|---------|-----|------|
| ROOM_ID | `room_e05d4d1c1e` | 楽天ROOMアカウントID |
| ACCOUNT_NAME | `カピバラ癒し` | 表示名 |
| POST_DAILY_MIN/MAX | 90/100 | 日次投稿目標（ランダム） |
| POST_BATCH_1_COUNT | 50 | Batch1固定件数 |
| Batch1開始時刻 | 0:00〜0:30 | ランダム開始 |
| Batch2開始時刻 | 7:00〜12:00 | ランダム開始 |
| POST_INTERVAL_MIN/MAX | 8/15秒 | 投稿間隔（ランダム） |
| POST_MAX_SAME_GENRE | 3 | 同ジャンル連続上限 |
| POOL_MIN/MAX | 300/600 | 商品プール維持件数 |
| FOLLOW_DAILY_MIN/MAX | 150/250 | 日次フォロー目標 |
| BROWSER_HEADLESS | False | Chrome表示あり |

### 投稿導線比率
- `direct` (商品ページから直接): 70%
- `influencer` (インフルエンサー投稿から派生): 30%

---

## 7. 運用モード

| モード | 動作 | 切替コマンド |
|--------|------|------------|
| AUTO | 全自動（90〜100件） | `python run.py mode AUTO` |
| SAFE | 安全モード（最大20件） | `python run.py mode SAFE` |
| STOP | 完全停止 | `python run.py mode STOP` |

ファイル: `data/operation_mode.json`
デフォルト: SAFE（20件）

---

## 8. 環境変数（.env）

```env
RAKUTEN_APP_ID=           # 楽天市場API ID（必須）
RAKUTEN_ACCESS_KEY=       # 楽天APIアクセスキー（任意）
SLACK_WEBHOOK_URL=        # Slack Webhook URL（通知用）
```

---

## 9. ジャンル別検索キーワード

| ジャンル | キーワード例 |
|---------|------------|
| kitchen | フライパン セット、保存容器 耐熱... |
| beauty | スキンケア セット、ヘアオイル 人気... |
| living | 今治タオル セット、収納ボックス... |
| fashion | バッグ レディース 軽量、スニーカー 白... |
| appliance | モバイルバッテリー 軽量、ワイヤレスイヤホン... |
| food | コーヒー ドリップ、ナッツ 素焼き... |
| kids | 知育玩具、水筒 キッズ... |
| book | 自己啓発 ベストセラー、レシピ本... |
| pet | ペットベッド、猫 おもちゃ... |

---

## 10. 商品監査ロジック（item_auditor.py）

商品取得後、以下の基準でpass/review/failに分類:

| 判定 | 条件 |
|------|------|
| pass | 価格・画像・カテゴリ全て正常 |
| review | 要確認（価格異常、画像なし等） |
| fail | 投稿不可（削除済み商品、規約違反等） |

---

## 11. BOT対策設計

楽天ROOMのBOT検知を回避するための設計:

1. **投稿数ランダム化**: 毎日90〜100件（固定にしない）
2. **開始時刻ランダム化**: Batch1は0:00〜0:30、Batch2は7:00〜12:00の範囲でランダム
3. **投稿間隔ランダム化**: 8〜15秒（固定間隔にしない）
4. **人間的遅延**: タイプ速度0.03〜0.12秒/文字
5. **導線分散**: direct 70% / influencer 30%
6. **同ジャンル連続制限**: 最大3件

---

## 12. よくある問題と対処

| 問題 | 原因 | 対処 |
|------|------|------|
| ログイン切れ | セッション期限切れ | `python run.py login` でログイン再実行 |
| 投稿失敗続き | セレクタ変更 | `data/logs/` を確認、スクリーンショット確認 |
| プール不足 | 商品が足りない | `python run.py replenish` でAPI補充 |
| Slack通知なし | .env の SLACK_WEBHOOK_URL 未設定 | .env確認 |
| キュー詰まり | 前回分が残っている | `python run.py queue-status` → 手動クリア |

---

## 13. 現在の運用状態（2026-03-30時点）

- **稼働マシン**: サイバーさん（Desktop B）本番稼働中
- **スケジューラ**: `scheduler_auto.bat` がWindowsタスクスケジューラに登録済み
- **投稿実績**: 日次90〜100件の安定投稿中
- **注意点**: ログイン状態は `data/state/storage_state.json` に保存。
  このファイルが消えると再ログインが必要。
