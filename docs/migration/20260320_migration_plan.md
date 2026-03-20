# サイバーさん移行計画書
## 作成日: 2026-03-20 | 作成者: キャップさん（移行主担当）

---

## 1. 移行対象マッピング

### 優先度A: 即日移行（本番影響あり）

| 現在の場所 | 移行先 | 状態 |
|-----------|--------|------|
| `08_AUTOMATION/room_bot_v2/` | `rakuten-room/bot/` | 本番稼働中（レッツさん） |
| `08_AUTOMATION/room_bot_v2/scripts/*.bat` | `rakuten-room/bot/scripts/` | bat/vbs含む |
| `scheduler.py` (ルート) | `ops/scheduler/` | 本番稼働中 |
| `watchdog.py` (ルート) | `ops/monitoring/` | 本番稼働中 |
| `ops/notifications/notifier.py` | `ops/notifications/` | Slack通知 |

### 優先度B: 翌日以降（開発系）

| 現在の場所 | 移行先 | 状態 |
|-----------|--------|------|
| `coin_business/` | `coin_business/`（そのまま） | 開発中 |
| `bots/room_bot/` | `archive/` | 旧版（非稼働） |
| `bots/coin_bot/` | `coin_business/bot/` | 開発中 |
| `bots/slack/` | `ops/slack-bot/` | 開発中 |
| `scripts/*.bat` | `ops/scripts/` | ユーティリティ |

### 優先度C: 整理のみ

| 現在の場所 | 対応 |
|-----------|------|
| `00_OPERATIONS` ～ `09_INTELLIGENCE` | 組織フォルダ → サイバーさんにそのままコピー |
| `_SHARED/` | `shared/` に統合 |
| `docs/` | そのままコピー |
| `archive/` | そのままコピー |

---

## 2. 絶対パス依存一覧（要書き換え）

### coin_business/ (17箇所)
全て同一パターン: `Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")`

| ファイル | 行 |
|---------|-----|
| config.py | 14 |
| run.py | 21 |
| scripts/supabase_client.py | 15 |
| scripts/import_yahoo_history.py | 16 |
| scripts/import_ebay_json.py | 16 |
| scripts/fetch_yahoo_closedsearch.py | 31 |
| scripts/fetch_ebay_sold.py | 30 |
| scripts/fetch_ebay_terapeak.py | 33 |
| scripts/ebay_api_client.py | 31 |
| scripts/ebay_monitor.py | 28 |
| scripts/negotiate_candidates.py | 17 |
| scripts/market_stats.py | 15 |
| scripts/cross_market_analysis.py | 18 |
| scripts/setup_supabase.py | 12 |
| scripts/setup_airtable.py | 21 |
| scripts/airtable_client.py | 12 |
| scripts/migrate_excel.py | 15 |
| scripts/collectors/price_collector.py | 15 |
| scripts/analyzers/report_generator.py | 13 |
| scripts/analyzers/trend_analyzer.py | 13 |

**修正方針**: `Path(__file__).resolve().parent.parent` に統一（相対パス化）

### bots/room_bot/ (2箇所)
| ファイル | 内容 |
|---------|------|
| setup_task.py:15 | Python実行パス |
| setup_task.py:16 | プロジェクトパス |

### 外部パス参照 (1箇所)
| ファイル | 内容 |
|---------|------|
| coin_business/scripts/migrate_excel.py:15 | Desktop上のExcelファイル |

### バッチファイル (6箇所)
全て `%USERPROFILE%` 使用 → **修正不要**（環境変数で動的解決済み）

---

## 3. 移行手順（明日実行）

### Phase 1: 安全確認（10分）
1. レッツさんの本番BOT稼働確認（止めない）
2. サイバーさんのWindows/Python環境確認
3. Box Syncの状態確認

### Phase 2: リポジトリコピー（20分）
1. キャップさんでgit push（最新をリモートに）
2. サイバーさんでgit clone
3. ファイル差分確認

### Phase 3: 環境構築（30分）
1. Python 3.12インストール確認
2. `pip install -r requirements.txt`（各プロジェクト）
3. `playwright install chromium`
4. `.env` ファイル作成（各プロジェクト）
5. Supabase接続テスト

### Phase 4: パス書き換え（30分）
1. `coin_business/` の PROJECT_ROOT を相対パス化
   - config.py を `Path(__file__).resolve().parent` に変更
   - 全スクリプトの PROJECT_ROOT を config.py から import に統一
2. CLAUDE.md のパス更新
3. テスト: `python run.py count`（Supabase接続確認）

### Phase 5: 動作テスト（20分）
1. `python coin_business/run.py count` → テーブル件数確認
2. `python coin_business/run.py stats --time` → 集計レポート確認
3. `python coin_business/scripts/cross_market_analysis.py` → 分析確認
4. room_bot_v2は**この日は移行しない**（レッツさんで稼働継続）

### Phase 6: 確認・記録（10分）
1. サイバーさんで全コマンド正常確認
2. dev_progress.logに記録
3. CEO報告

---

## 4. ロールバック方法

| 問題 | ロールバック |
|------|------------|
| サイバーさんでPython動かない | キャップさんで作業継続（今まで通り） |
| git clone失敗 | 手動コピー（Box経由 or USB） |
| .env漏れ | キャップさんから転送 |
| room_bot_v2が壊れた | **レッツさんは一切触らないので影響なし** |
| Supabase接続失敗 | .envのキー確認 → 再設定 |

**最重要**: レッツさんの本番BOTには一切手を入れない。壊れる可能性ゼロ。

---

## 5. サイバーさん環境前提

### 必須ソフトウェア
| ソフト | バージョン | 用途 |
|--------|----------|------|
| Python | 3.12+ | 全スクリプト |
| Git | 最新 | リポジトリ管理 |
| Node.js | 18+ | Playwright依存 |

### 必須Pythonライブラリ

**room_bot_v2用**:
```
playwright>=1.40.0
python-dotenv>=1.0.0
```

**coin_business用**:
```
supabase>=2.0.0
python-dotenv>=1.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
```

### 必須環境変数（.env）

**room_bot_v2/.env**:
```
RAKUTEN_APP_ID=（楽天API）
SLACK_WEBHOOK_URL=（Slack通知）
```

**coin_business/.env**:
```
SUPABASE_URL=（Supabase接続先）
SUPABASE_KEY=（service_role_key）
EBAY_CLIENT_ID=（承認後）
EBAY_CLIENT_SECRET=（承認後）
```

### ディレクトリ構成（サイバーさん側）
```
C:\Users\[サイバーユーザー名]\solarworks-ai\
  ├── coin_business/         ← Phase 4で移行
  ├── 08_AUTOMATION/
  │   └── room_bot_v2/      ← Phase 5以降（初日は触らない）
  ├── ops/
  ├── docs/
  └── ...
```

---

## 6. 禁止事項チェックリスト

- [ ] いきなり全移動 → **しない**（Phase順に段階実行）
- [ ] 本番BOT停止 → **しない**（レッツさんは触らない）
- [ ] 一括パス変更 → **しない**（coin_businessから段階的に）
- [ ] テストなし反映 → **しない**（各Phaseで動作確認）
- [ ] .envの直接コピー → **しない**（手動で値だけ転記）

---

## 7. 明日のゴール

**最低成功**:
- サイバーさんにgit clone完了
- Python環境構築完了
- coin_business の `run.py count` が動く

**上出来**:
- coin_business全コマンド正常動作
- 相対パス化完了
- CLAUDE.md更新済み
