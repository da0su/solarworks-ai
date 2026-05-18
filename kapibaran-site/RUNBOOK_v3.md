# KAPIBARAN v3.1 — Runbook (ドライラン / デプロイ / ロールバック)

Codex review (2026-05-18) #9 反映。

## 1. 事前確認 (ドライラン)

```bash
cd kapibaran-site

# REST 全件 snapshot のみ実行 (本番 content は触らない)
python automation/run_v3_1_full.py --dry-run --verbose

# 出力物
ls state/v3_backups/<YYYYMMDD_HHMMSS>/
# pages.json / posts.json / media_index.json /
# customizer_css.txt / settings.json / manifest.json
```

manifest.json で各 file の sha256 と件数を確認。`pages` >= 8 件、`status="ok"` を期待。

## 2. 本番デプロイ

```bash
# silent mode (default): 失敗時のみ Slack 集約通知
python automation/run_v3_1_full.py

# 進捗を逐次見たいとき
python automation/run_v3_1_full.py --verbose
```

実行順:
1. backup_v3_snapshot.py  (失敗 → ABORT)
2. deploy_v3_media.py
3. deploy_v3_css.py
4. deploy_v3_pages.py
5. deploy_v3_journal.py
6. deploy_v3_compliance.py
7. verify_v3.py

exit code: 0=ALL PASS / 1=verify fail / 2=backup fail (ABORT)

## 3. 検証

```bash
python automation/verify_v3.py
cat logs/verify_v3_result.json | python -m json.tool | head -80
```

`overall: "PASS"`、`count_match: true`、各 URL `ok: true` であること。

## 4. ロールバック

バックアップから復旧する手順:

```bash
# 4-1. 復元元の snapshot ディレクトリを特定
ls -t state/v3_backups/
# 例: 20260518_142000  ← 最新を選ぶ

# 4-2. 復元 (注: 現状は手動 — REST PATCH script は復旧用に作成可能)
# 各 page の content は backup pages.json の id 別に rest.patch(`/wp/v2/pages/{id}`, {...})

# 簡易: 緊急時は WP 管理画面から手動でリビジョン (Revisions) を 1 つ前に戻すのが最速
# WP は ページ更新ごとにリビジョンを自動保存しているため、過去 5〜10 版を戻せる
```

復旧手順自動化スクリプト (`automation/restore_v3_snapshot.py`) を将来 追加予定。
現時点では:
- 致命的問題発生時は WP 管理画面 → 該当ページ → リビジョン → 1 つ前に Revert
- それでも復旧不可なら state/v3_backups/<ts>/pages.json から content を手動で復元

## 5. 緊急停止

deploy 進行中の止め方:
```bash
# orchestrator は subprocess を順次実行するので Ctrl+C で停止
# 一度 verify が走ってしまった後は WP 状態が更新されているので backup から復元
```

## 6. 通知設定 (Codex #10)

default は silent (失敗時のみ通知)。

環境変数:
- `KAPIBARAN_V3_SLACK_WEBHOOK` (専用)
- `SLACK_WEBHOOK_URL` (汎用フォールバック)

どちらも未設定なら Slack 通知は完全に発生しない (依存 0)。

## 7. taxonomy 復旧手順 (Codex #6 2 回目)

`backup_v3_snapshot.py` は v3.1 から下記も保存:
- `categories.json` (全 category, description 含む)
- `tags.json` (全 tag)
- `menus.json` (REST 経由で取れる範囲)

復旧時は WP 管理画面 → 投稿 → カテゴリー / タグ から description を手動で復元。
将来 REST PATCH 自動化スクリプトを `automation/restore_v3_snapshot.py` で追加予定。

## 8. EXPECTED_* 値 override

検証 URL 件数 or 公開 page ID リストは env 変数で override 可能 (運用変更時のみ使用):

```bash
# verify URL 件数を 20 に変更
KAPIBARAN_V3_EXPECTED_URL_COUNT=20 python automation/verify_v3.py

# 公開 page ID リストを変更
KAPIBARAN_V3_EXPECTED_PUBLISH_IDS=25,26,27,28,200,201 \
  python automation/deploy_v3_compliance.py
```

## 9. 既知の制約

- **ConoHa WAF** が `wp db export` (SQL dump) を block。バックアップは REST 全件 snapshot で代替。
- **画像バイナリ** は backup に含めない (media_index.json で URL/id のみ記録)。
  WP メディアライブラリ自体が永続なので、過去画像が削除されない限り URL 経由で取得可能。
- **Customizer CSS** は wp/v2/custom_css エンドポイント経由で取得 (権限なしのとき空)。
- **markup strip** は draft / LEGACY_DRAFT_PAGE_IDS のみ適用。公開ページに対しては
  `deploy_v3_pages.py` の v3 content 上書きが正規ルート。
