# ROOM BOT v2 - 運用ガイド

## クイックスタート

### 初回セットアップ
1. `scripts/run_room_login.bat` をダブルクリック
2. 開いたChromeで楽天ROOMにログイン
3. ログイン完了後、コンソールでEnter

### 日常運用（ワンクリック）
| やりたいこと | ダブルクリック |
|---|---|
| 投稿内容を事前確認 | `scripts/run_room_preview.bat` |
| テスト投稿(3件) | `scripts/run_room_test.bat` |
| 本番投稿(デイリー) | `scripts/run_room_full.bat` |

### キュー運用（Phase 2）
| やりたいこと | ダブルクリック |
|---|---|
| 投稿計画を作る | `scripts/run_room_plan.bat` |
| キュー実行 | `scripts/run_room_execute.bat` |
| plan+execute一括 | `scripts/run_room_daily.bat` |

---

## コマンド一覧

### 基本コマンド
```powershell
python run.py login                    # ログイン（初回のみ）
python run.py preview --file posts.json  # プレビュー
python run.py batch --file posts.json    # バッチ投稿
```

### キューコマンド（Phase 2）
```powershell
python run.py plan                     # 今日の投稿計画を生成
python run.py plan --date 2026-03-15   # 指定日の計画
python run.py execute                  # キューから実行
python run.py execute --limit 5        # 最大5件実行
python run.py daily                    # plan + execute 一括
python run.py queue-status             # キュー状況確認
```

### スケジューラ（Phase 3）
```powershell
python run.py scheduler-setup          # タスクスケジューラ登録
python run.py scheduler-remove         # タスクスケジューラ解除
```

---

## ファイル構成
```
room_bot_v2/
  run.py                    # メインエントリーポイント
  config.py                 # 設定
  scripts/                  # ワンクリック用bat
    run_room_login.bat
    run_room_preview.bat
    run_room_test.bat
    run_room_full.bat
    run_room_plan.bat
    run_room_execute.bat
    run_room_daily.bat
  executor/                 # 実行エンジン（既存）
    browser_manager.py
    post_executor.py
    batch_runner.py
    comment_generator.py
    post_scorer.py
  planner/                  # キュー管理（Phase 2）
    queue_manager.py        # SQLiteキュー管理
    daily_planner.py        # 投稿候補生成
    source_manager.py       # 商品ソース管理
  data/
    room_bot.db             # SQLiteデータベース
    test_posts.json         # テスト用投稿データ
    source_items.json       # 商品候補プール
    logs/                   # 日別ログ
    screenshots/            # スクリーンショット
```

---

## ログ確認
- ログファイル: `data/logs/YYYY-MM-DD.log`
- スクリーンショット: `data/screenshots/YYYY-MM-DD/`
- DB確認: `python run.py queue-status`

---

## トラブルシューティング

### ログイン切れ
→ `scripts/run_room_login.bat` を再実行

### 連続失敗で停止
→ ログを確認: `data/logs/` の最新ファイル
→ セレクタ変更の可能性 → 開発者に連絡

### 重複スキップが多い
→ `data/source_items.json` に新しい商品を追加
