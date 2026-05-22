# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🚨🚨 ROOM 4 機能 状況把握フロー (必須・2026-05-22 CEO 指示・再発防止)

**「ROOM どう?」「フォロー / POST / LIKE / FOLLOWBACK 状況」「停止してる?」**
等の質問を受けたら **必ず最初に** 以下を実行する.

### Step 1: SSOT 確認 (絶対遵守・例外なし)
```bash
python ops/room_status.py --human
```
これだけ. host のレガシー JSON を最初に見ない.

### Step 2: 禁忌ファイル (状況判断目的では絶対禁止)
- ✗ `rakuten-room/bot/data/follow_history.json` (Plan v6 cutover 凍結)
- ✗ `rakuten-room/bot/data/like_history.json` (同上)
- ✗ `rakuten-room/bot/data/post_history.json` (同上)
- ✗ `rakuten-room/bot/data/fl_daily_log.json` (さらに古い)
- ✗ `rakuten-room/bot/data/chrome_profile_post/` 等の Cookies/Preferences mtime

これらの mtime や last_entry を見て「停止」と判断するのは **禁止**.

### Step 3: SSOT (これしか見ない)
- `state/follow_runtime_state.json` (patrol 15分毎 update / 4機能まとめ)
- `state/patrol_v6_state.json`
- `state/daily_targets_ssot.json`
- `state/follow_rate_state.json`
- スプシ `楽天ROOM_検証管理` / シート `楽天ROOM_デイリーログ` (gid=1447646534)

### 過去の失敗例 (印象付け)
- 2026-05-20: chrome_profile_post (host) を見て「全 profile 空アカ」誤判定 → CEO「VBでやってるんだよ」
- 2026-05-22: follow_history.json (5/20 凍結) を見て「FOLLOW 2日停止」誤判定 → CEO「フォローは毎日順調に増えている」

**同じ失敗 2 回**. Codex 相談で再発防止フロー確立 (2026-05-22). 次に間違えたら 3 回目.

### 関連 memory
- `memory/rakuten_machine_split.md` - VM vs host 役割分担
- `memory/vm_headless_only_rule.md` - VM 起動方式
- `memory/rakuten_room_targets_ssot.md` - 目標値 SSOT

---

## Codex (GPT-5) Critical Review (必須・2026-05-16 CEO 指示)

**「君一人では信頼できない。ChatGPT (Codex) と連携してブラッシュアップ」** (CEO 指示)

以下に該当する commit は **必ず push 前に Codex review を通す**:
1. 検証ロジック変更 (success/failure 判定, URL check 等)
2. 既存 fix の revert / 無効化
3. CEO 指摘で投入された commit の上書き
4. 数値報告に影響する集計/フィルタ
5. production bot (POST/FOLLOW/LIKE/FB) の core 動作

```bash
python ops/codex_review.py --commit HEAD --context "<change の背景>"
# exit 0=APPROVE / 1=REVIEW_NEEDED / 2=REJECT

python ops/codex_review.py --usage
# 累計 + 今月の Codex 使用量を表示
```

**【CEO 5/16 追加指示】「コーデックスで使用した場合は、使用した分を毎回報告すること」**
- review 毎に token 数 + USD/JPY 概算 + 月次累計を CEO 報告に含める
- 自動表示: review 実行時に USAGE/COST/MONTH/CUM を console + 保存ファイルに記録
- ログ: `state/codex_reviews/_usage_log.jsonl`

詳細: `memory/codex_review_rule.md`

過去の失敗事例 (Codex があれば防げた):
- 5/12 commit 8f34a76: URL check 撤去 → 450件 false success → 5日間虚偽報告
- 5/16 commit 3c0e3d6: wait_for_function pathname 誤判定 (待機ゼロで通過)

## 作業完了フロー（必須・毎回実行）

**重要タスクに着手するとき（最初に実行）:**
```bash
python ops/notifications/slack_reporter.py --mark-pending "タスク概要"
```

**タスクが完了したら（最後に必ず実行）:**
```bash
# 1. Slack報告送信（自動で未報告フラグも解除される）
python ops/notifications/slack_reporter.py "【サイバー報告 #NNN】..."

# 2. report_numbering.md を更新
#    memory/report_numbering.md の採番テーブルに追記する
```

**ルール:**
- CEO向け提出レポートには `【サイバー報告 #NNN】` 番号を付ける（report_numbering.md参照）
- 送信先デフォルト: `#web-cyber_marke_clow` (C0AQASABVL7)
- 重要タスク（実装・調査・報告）は必ず完了報告を送ること
- 軽微な質問回答・メモ作業は報告不要

**Stop Hook（バックストップ）:**
未報告フラグが残ったままセッションが終了すると、`stop_hook.py` が自動的に
Slackへ「未報告検知」警告を送信する。

## セッション開始時の必須アクション

**毎セッション最初に必ず実行:**
```bash
python ops/slack_monitor/check_unread.py
```
→ 未読Slackメッセージを確認する。件数が0なら作業開始。1件以上あれば内容を読んで対応する。

**未読確認後は既読化:**
```bash
python ops/slack_monitor/check_unread.py --clear
```

---

## 🎯 楽天ROOM 4機能 日次目標値 SSOT (絶対遵守・忘れたら必ず確認)

**確定日**: 2026-05-05 | **CEO 指示**

> 「分からなくなったらスプシを確認する。これを入れておけば覚えていなくても都度確認すればOK」

### ルール (永続)

楽天ROOM 4機能 (POST/LIKE/FOLLOW/FOLLOWBACK) の **日次目標値**は、コードの hard-code (`POST_DAILY_MAX=200` 等) ではなく **必ずスプシを参照** する。CEOが日次で目標を更新するため、コード値は常に古い可能性がある。

### 確認手順 (必ず実行)

CEO 報告・進捗確認・ダッシュボード生成・SLO 違反判定の **どの場面でも** 以下を実行:

```bash
# キャッシュ自動 (6h以内なら再利用、過ぎたら gspread で取得)
python -c "from ops.notifications.dashboard_report import _load_ssot_targets; print(_load_ssot_targets())"

# または直接確認
python _check_target_sheet.py     # gid=1447646534 を読む
```

### SSOT 場所 (固定)

| 項目 | 値 |
|------|-----|
| Spreadsheet | 楽天ROOM_検証管理 |
| ID | `1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0` |
| シート名 | `楽天ROOM_デイリーログ` |
| gid | **`1447646534`** |
| URL | https://docs.google.com/spreadsheets/d/1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0/edit?gid=1447646534 |

### カラム (固定)

| Col | 内容 |
|-----|------|
| A | 日付 (`YYYY/MM/DD`) |
| B | 目標投稿数 |
| E | **目標フォロー数** |
| H | **目標ライク数** |
| K | **目標フォローバック数** |

### 自動 cache

`state/daily_targets_ssot.json` に 6時間 cache。`dashboard_report.py` は自動で SSOT を取得して達成率表示する。**コードの hard-code 数値 (`/200` `/500` `/2000` 等) を見たら疑え** — その箇所は SSOT 連動に修正対象。

### 関連ルール

- 詳細: `memory/rakuten_room_targets_ssot.md`
- 旧 `rakuten_follow_daily_target.md` (2000件必達) は SSOT に統合・スプシの値が優先
- 第2アカウント禁止 (`rakuten_follow_account_rule.md`) は変更なし

---

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
