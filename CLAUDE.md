# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SolarWorks AI事業統合リポジトリ。楽天ROOM自動投稿BOT + コイン仕入れリサーチを運用する。

### 3台体制
- **サイバーさん（Desktop B）**: 正式本体・本番運用基盤（このリポジトリの正本）
- **キャップさん（ノート型）**: 開発・移行の主担当
- **レッツさん（Desktop A）**: 既存本番維持

### 管理基準
- **Source of Truth**: サイバーさん + GitHub
- **CEOはbatを押すだけ**の設計。全機能は自動化前提（Slack通知含む）

## Architecture

### 楽天ROOM BOT
メインコードは `rakuten-room/bot/`

```
rakuten-room/bot/run.py auto       ← 完全自動運用の一発コマンド
  ├── ヘルスチェック                (monitor/health_checker.py)
  ├── プール補充                    (planner/product_fetcher.py → 楽天API)
  │    └── 監査                     (planner/item_auditor.py)
  ├── 投稿済み商品除去               (planner/pool_manager.py)
  ├── 計画生成                      (planner/daily_planner.py → SQLiteキュー)
  ├── 投稿実行 Batch 1/2            (planner/queue_executor.py)
  │    ├── BrowserManager           (executor/browser_manager.py - Playwright)
  │    ├── PostExecutor             (executor/post_executor.py - DOM操作)
  │    └── BatchRunner              (executor/batch_runner.py)
  └── レポート/アラート              (monitor/daily_report.py + slack_notifier.py)

ops/scheduler/scheduler.py          ← バッチ時刻管理（30秒ポーリング）
ops/scheduler/watchdog.py           ← scheduler.pyの死活監視
ops/notifications/notifier.py       ← Slack/VOICEVOX通知
```

### コイン仕入れリサーチ
メインコードは `coin_business/`

```
coin_business/run.py                ← CLIエントリーポイント
  ├── import-yahoo                  (scripts/import_yahoo_history.py)
  ├── update-yahoo                  (scripts/fetch_yahoo_closedsearch.py)
  ├── update-ebay                   (scripts/fetch_ebay_sold.py)
  ├── ebay-watch                    (scripts/ebay_monitor.py)
  ├── stats / search / count        (scripts/market_stats.py)
  └── Supabase (PostgreSQL)         (scripts/supabase_client.py)
```

## Key Commands

### 楽天ROOM BOT
```bash
cd rakuten-room/bot

# 完全自動運用（replenish→plan→execute→report）
python run.py auto
python run.py auto --batch 1          # Batch1のみ
python run.py auto --batch 2          # Batch2のみ

# 個別コマンド
python run.py login                   # 初回ログイン（手動）
python run.py replenish               # 商品プール自動補充
python run.py plan                    # 投稿計画生成
python run.py execute --limit 50      # キュー実行
python run.py daily                   # plan + execute 一括
python run.py queue-status            # キュー状況確認
python run.py health                  # 異常検知
python run.py report                  # 朝レポート
python run.py report --type night --slack  # 夜レポート+Slack
python run.py mode AUTO               # 運用モード変更
```

### コイン仕入れリサーチ
```bash
cd coin_business

python run.py count                   # 全テーブル件数
python run.py stats --clean --time    # 時間軸4区分レポート
python run.py update-yahoo            # ヤフオク差分更新
python run.py update-ebay             # eBay差分更新
python run.py ebay-watch              # eBay仕入れ監視
python run.py search --country イギリス --grader NGC
```

## Key Design Decisions

- **人間らしい揺らぎ**: 投稿間隔(8-15秒)、日次投稿数(90-100)、バッチ開始時刻すべてランダム化
- **Persistent Context**: Playwright persistent context でcookie管理
- **SQLiteキュー**: post_queue テーブルで投稿管理。重複防止、失敗リトライ、中断再開
- **運用モード3段階**: AUTO / SAFE / STOP（ファイルベース）
- **監査レイヤー**: API取得した商品をpass/review/failに分類
- **atomic write**: .tmp経由で書き込み（破損防止）
- **価格判断ルール**: 直近3か月最重視。4区分集計必須。古い相場を仕入れ判断に混ぜない

## Environment Variables

### rakuten-room/bot/.env
```
RAKUTEN_APP_ID=       # 楽天市場API（必須）
SLACK_WEBHOOK_URL=    # Slack通知
```

### coin_business/.env
```
SUPABASE_URL=         # Supabase接続先
SUPABASE_KEY=         # service_role_key
EBAY_CLIENT_ID=       # eBay API（承認後）
EBAY_CLIENT_SECRET=   # eBay API（承認後）
```

## Config Highlights

| 設定 | 値 | 説明 |
|------|-----|------|
| POST_DAILY_MIN/MAX | 90/100 | 日次投稿目標 |
| POST_BATCH_1_COUNT | 50 | Batch1固定件数 |
| POST_INTERVAL_MIN/MAX | 8/15秒 | 投稿間隔 |
| POOL_MIN/MAX | 300/600 | 商品プール維持件数 |

## Directory Structure

```
solarworks-ai/
├── rakuten-room/bot/      ← 楽天ROOM BOT（本体）
├── coin_business/         ← コイン仕入れリサーチ
├── ops/
│   ├── scheduler/         ← スケジューラー + watchdog
│   ├── notifications/     ← 通知（Slack/VOICEVOX）
│   └── monitoring/        ← 監視
├── 00_OPERATIONS ~ 09_INTELLIGENCE  ← 組織フォルダ
├── docs/                  ← ドキュメント
├── shared/                ← 共有ライブラリ
└── archive/               ← 旧版退避
```

## bat Files

CEO用の起動スクリプトは `rakuten-room/bot/scripts/` にある。`scheduler_auto.bat` が自動運用の起点。

## State Management

セッション依存を排除し、外部ファイルで状態管理する。

### ファイル
- `state/system_state.json` — 全タスクの状態・進捗を一元管理

### ルール
1. **毎セッション開始時**に `state/system_state.json` を読む
2. タスク実行後は `status` / `last_action` / `next_action` を必ず更新
3. 会話履歴には依存しない。stateファイルが唯一の真実
4. `owner` フィールドで担当マシンを明示（cyber / cap / ceo）

### 自動同期
- slack_bridge.py がTASK/ACK/running/DONE/ERRORの各ステージでstateを自動更新
- atomic write（.tmp → rename）で破損防止
- watch再起動時に前回の未完了タスクを検出しerrorに移行

### 状態確認
```bash
python slack_bridge.py state-summary    # CEO/cap/cyber共通ビュー
```

### 役割
- **cyber**: stateを見て実行
- **cap**: stateを見て承認
- **CEO**: stateを見て指示
