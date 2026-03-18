# 安全移行ルール

作成日: 2026-03-18
目的: 新旧構造の並存状態で、本番影響ゼロの段階移行を保証する

---

## 1. 移行優先順位

### Priority 1: notifications/ → ops/notifications/（低リスク・即移行可）

**理由**: 本番投稿フローに無関係。参照元が少なく、全てローカルスクリプト。

| 参照元 | 変更内容 |
|--------|----------|
| `C:\scripts\notify_approval.bat` | パスを `ops\notifications\notifier.py` に変更 |
| `C:\scripts\notify_dev_done.vbs` | 同上 |
| `C:\scripts\test_vbs_async.vbs` | 削除可（テスト用） |
| `C:\scripts\test_vbs_debug.vbs` | 削除可（テスト用） |
| `.claude/settings.local.json` | パス更新 |
| `notifier.py` 内docstring | コメント修正のみ |

**影響範囲**: 通知が鳴るか鳴らないかだけ。投稿・スケジューラに影響なし。
**推定作業時間**: 15分
**ロールバック**: bat/vbsのパスを戻すだけ

---

### Priority 2: coin_business/ → coin/（中リスク・独立移行可）

**理由**: 他事業と完全独立。外部からの参照ゼロ。内部の絶対パス書き換えのみ。

| 参照元 | 変更内容 |
|--------|----------|
| `coin_business/config.py` | `PROJECT_ROOT` パス変更 |
| `coin_business/run.py` | `sys.path.insert` パス変更 |
| `coin_business/scripts/airtable_client.py` | `PROJECT_ROOT` パス変更 |
| `coin_business/scripts/setup_airtable.py` | `PROJECT_ROOT` パス変更 |
| `coin_business/scripts/collectors/price_collector.py` | `sys.path.insert` パス変更 |
| `coin_business/scripts/analyzers/trend_analyzer.py` | `sys.path.insert` パス変更 |
| `coin_business/scripts/analyzers/report_generator.py` | `sys.path.insert` パス変更 |
| `coin_business/DESIGN.md` | パス表記更新 |

**影響範囲**: コイン事業のみ。楽天ROOMに影響なし。
**推定作業時間**: 30分
**ロールバック**: `git checkout` で coin_business/ を復元

---

### Priority 3: 08_AUTOMATION/room_bot_v2/ → rakuten-room/bot/（高リスク・最後に移行）

**理由**: 本番稼働中。参照元が30+ファイル。scheduler/watchdog/bat全てに影響。

| カテゴリ | 影響ファイル数 | 内容 |
|----------|---------------|------|
| CLAUDE.md | 1 | 全パス表記更新 |
| scheduler.py | 1 | `ROOM_BOT_DIR` パス変更 |
| watchdog.py | 1 | 監視対象パス変更 |
| preflight_check.py | 1 | import先変更 |
| 08_AUTOMATION/room_bot_v2/scripts/*.bat | 15 | cd先・実行パス変更 |
| ルートscripts/*.bat | 8 | 参照パス変更 |
| room_bot_v2内Python | 6+ | sys.path変更 |
| docs/*.md | 8+ | パス表記更新 |
| 00_OPERATIONS/config/ | 2 | 部署参照変更 |

**影響範囲**: 毎日90-100件の本番投稿が止まるリスク。
**推定作業時間**: 2時間+テスト
**ロールバック**: git revert（ただし本番停止中のリカバリが必要）

**移行条件**: notifications と coin_business の移行が問題なく完了した後にのみ着手。

---

## 2. 移行時チェックリスト

### 移行前

- [ ] **占有宣言**: Slackで対象フォルダの占有を宣言
- [ ] **Gitブランチ作成**: `feature/migrate-{事業名}` を切る
- [ ] **本番停止確認**: 移行対象が本番実行中でないことを確認
- [ ] **参照パス全洗い出し**: `grep -r "旧パス" --include="*.py" --include="*.bat" --include="*.vbs" --include="*.md" --include="*.json"`

### 移行実行

- [ ] **ファイル移動**: `git mv 旧パス/ 新パス/`（git履歴を保持）
- [ ] **絶対パス更新**: 各.pyファイルの `PROJECT_ROOT`、`sys.path.insert` を修正
- [ ] **bat/vbs更新**: `C:\scripts\` 内の参照パスを修正
- [ ] **scheduler.py更新**: パス定数を修正（room_bot_v2移行時のみ）
- [ ] **watchdog.py更新**: 監視対象パスを修正（room_bot_v2移行時のみ）
- [ ] **CLAUDE.md更新**: 全パス表記を新構成に合わせる
- [ ] **config.json更新**: 00_OPERATIONS/config/ 内の部署参照

### 移行後テスト

- [ ] **import確認**: `python -c "import sys; sys.path.insert(0, '新パス'); import config"` 等
- [ ] **実行テスト**: `python run.py --help` が通ること
- [ ] **通知テスト**: approval / dev_done が鳴ること（notifications移行時）
- [ ] **batテスト**: 各batファイルをダブルクリックして動作確認
- [ ] **scheduler --test**: スケジューラのテストモード実行（room_bot_v2移行時）
- [ ] **本番1件テスト**: `python run.py execute --limit 1` で投稿1件成功（room_bot_v2移行時）

### Git反映

- [ ] **差分確認**: `git diff --stat` で変更ファイル数を確認
- [ ] **コミット**: `git commit -m "migrate: {旧パス} → {新パス}"`
- [ ] **動作再確認**: コミット後にもう一度テスト実行
- [ ] **占有解放**: Slackで解放宣言

### 失敗時ロールバック

- [ ] **即時復旧**: `git checkout main -- 旧パス/` で旧ファイルを復元
- [ ] **bat/vbs復旧**: 旧パスに戻す
- [ ] **原因記録**: `docs/decisions/` に失敗原因を記録

---

## 3. Slack占有宣言テンプレ

### 占有開始

```
[占有開始] {契約名}
対象: {フォルダ名}/
作業: {作業内容}
予定: {見込み時間}
```

例:
```
[占有開始] Desktop A
対象: coin/
作業: coin_business → coin 移行
予定: 30分
```

### 占有解放

```
[解放] {契約名}
対象: {フォルダ名}/
結果: {完了 / 中断}
commit: {コミットハッシュ（あれば）}
```

例:
```
[解放] Desktop A
対象: coin/
結果: 完了
commit: a1b2c3d
```

### shared/ops 変更申告

```
[共通変更] {契約名}
対象: {shared/ or ops/}{サブパス}
内容: {変更内容}
影響: {影響を受ける事業}
```

例:
```
[共通変更] Desktop A
対象: ops/notifications/notifier.py
内容: dev_done遅延を5秒→3秒に変更
影響: 全事業共通（通知タイミング変更）
```

---

## 4. archive化の条件

### archive送りの条件（全て満たすこと）

1. **新パスで本番稼働が安定**（最低3日間、本番エラーなし）
2. **旧パスへの参照がゼロ**（grep確認で0件）
3. **旧パスのコードが新パスと同一**（差分なし）
4. **CEO承認**（Slackで「archive化OK」の一言）

### archive前の確認チェックリスト

- [ ] `grep -r "旧パス" --include="*.py" --include="*.bat" --include="*.vbs" --include="*.json"` → 0件
- [ ] 新パスでの本番稼働ログ確認（3日分）
- [ ] 旧パスと新パスの `diff -r` → 差分なし
- [ ] `.gitignore` に旧パスが含まれていないこと

### archive実行手順

```bash
# 1. archiveへ移動（履歴保持）
git mv 00_OPERATIONS/ archive/legacy/00_OPERATIONS/
git mv bots/room_bot/ archive/legacy/bots_room_bot/

# 2. コミット
git commit -m "archive: 旧構造を archive/legacy/ へ移動"

# 3. master_map.md を更新
# archive済みの旧パスを「archive済み」に変更
```

### archive対象の優先順位

| 対象 | 条件 | タイミング |
|------|------|------------|
| `bots/room_bot/` | 旧版。現在使われていない | 即archive可 |
| `bots/coin_bot/` | 旧版。coin_business に置換済み | 即archive可 |
| `bots/slack/` | shared/slack への移行後 | 移行完了後 |
| `00-09` 旧部署構造 | 全事業フォルダ移行完了後 | 最後 |
| `_SHARED/` | shared/ への移行後 | 移行完了後 |
| `common/` | shared/utils への移行後 | 移行完了後 |
