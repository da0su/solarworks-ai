# 楽天ROOM BOT 実装計画書

**バージョン**: v4.0（最終運用仕様）
**更新日**: 2026-03-09

---

## 最終目標

| BOT | 日次目標 | BOT対策 |
|-----|----------|---------|
| 投稿BOT | 90〜100件/日 | 日によってランダム変動 |
| フォローBOT | 150〜250件/日 | 日によってランダム変動 |

---

## BOT構造

投稿BOTとフォローBOTは**別エンジン・別ワークフロー**として管理する。

---

## 実装タスク一覧

### 投稿BOT

| # | タスク | 状態 | 詳細 |
|---|--------|------|------|
| 1 | 商品リサーチ自動化 | 一部完了 | 楽天ランキング取得 |
| 2 | 投稿文自動生成 | 完了 | REVIEW_ALGORITHM対応 |
| 3 | 導線A: direct投稿 | **検証済み** | ROOM検索→コレ!→投稿 |
| 4 | 導線B: influencer投稿 | 未着手 | インフルエンサー投稿から派生 |
| 5 | 導線比率のランダム選択 | **実装済み** | config.py: direct 70% / influencer 30% |
| 6 | 日次件数のランダム決定 | **実装済み** | config.py: 90〜100 |
| 7 | バッチ分割（50件+残り） | **設計済み** | バッチ1: 0:00〜 / バッチ2: 12:00〜 |
| 8 | 投稿間隔のランダム化 | **実装済み** | config.py: 60〜180秒 |
| 9 | 件数制御（target_count） | **実装済み** | success_countで停止 |
| 10 | ログ保存 | **実装済み** | target/success/skip/error |

### フォローBOT

| # | タスク | 状態 | 詳細 |
|---|--------|------|------|
| 11 | フォローBOTスクリプト | **完了** | FOLLOW_BOT_SCRIPT.js v4.0 |
| 12 | 件数制御（target_count） | **完了** | success_countで停止 |
| 13 | 日次件数のランダム決定 | **完了** | 150〜250件 |
| 14 | ランダム間隔 | **完了** | 1〜3秒 |
| 15 | FOLLOW_LOG.json | **完了** | ログ保存の仕組み |
| 16 | エラー検知・自動停止 | 設計済み | 「操作が多すぎます」検知 |

### 共通

| # | タスク | 状態 | 詳細 |
|---|--------|------|------|
| 17 | config.py v4.0 | **完了** | 全設定を集約 |
| 18 | エラーログ保存 | 設計済み | error.log |
| 19 | スクリーンショット保存 | 設計済み | screenshots/ |
| 20 | 日次サマリー自動生成 | 設計済み | daily_log.json |

---

## ファイル構成

```
08_AUTOMATION/workflows/rakuten_room_agent/
├── WORKFLOW.md                    v4.0 ワークフロー全体図
├── AUTOMATION_CLASSIFICATION.md   v4.0 自動化分類表
├── TEST_OPERATION_PLAN.md         v4.0 段階テスト・安全ルール
├── POSTING_MANUAL.md              v4.0 投稿BOTマニュアル
├── FOLLOW_MANUAL.md               v4.0 フォローBOTマニュアル
├── FOLLOW_BOT_SCRIPT.js           v4.0 フォローBOTスクリプト
├── IMPLEMENTATION_PLAN.md         v4.0 実装計画（本ファイル）
├── REVIEW_ALGORITHM.md            レビュー生成ルール
└── MASS_PRODUCTION.md             量産設計

08_AUTOMATION/room_bot_v2/
├── run.py                         BOTエントリーポイント
├── config.py                      v4.0 全設定集約
├── executor/
│   ├── post_executor.py           投稿実行ロジック
│   ├── follow_executor.py         フォロー実行ロジック（新規予定）
│   ├── browser_manager.py         ブラウザ管理
│   └── selectors.py               DOMセレクター
└── data/
    ├── logs/                      エラーログ
    ├── screenshots/               スクリーンショット
    └── state/                     セッション

05_CONTENT/rakuten_room/history/
├── POST_LOG.json                  投稿ログ
├── FOLLOW_LOG.json                フォローログ
└── daily/                         日次サマリー
```

---

## 段階テスト実行計画

### Test A → Test B → Test C

| テスト | 投稿 | フォロー | 期間 |
|--------|------|----------|------|
| Test A | 20件/日 | 50件/日 | 3日間 |
| Test B | 50件/日 | 100件/日 | 5日間 |
| Test C | 90〜100件/日 | 150〜250件/日 | 継続 |

---

## 次のアクション

1. **Test Aを実行**（投稿20件 + フォロー50件）
2. 3日間安定したらTest Bへ昇格
3. 5日間安定したらTest C（本番）へ昇格
