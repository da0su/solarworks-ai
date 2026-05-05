# 楽天ROOM フォロー source の SSOT 方針

**確定日**: 2026-05-05 | **Phase B-4** | **指示者**: CEO「礎の心構え」

## 結論: SSOT は `seed_users.json`

| ファイル | 役割 | SSOT 立場 | 件数 (2026-05-05) |
|---------|------|----------|-------------------|
| `rakuten-room/bot/data/seed_users.json` | **新規フォロー候補の唯一の source** | ✅ **SSOT** | 447件 (11ジャンル均等) |
| `rakuten-room/bot/data/follow_candidates.db` | フォロー試行の archive (履歴) | ❌ archive のみ | 3576件 (全 skipped/failed・**pending=0**) |
| `rakuten-room/bot/data/follow_candidates_raw.json` | scrape 中間ファイル | ❌ 中間 | - |
| `rakuten-room/bot/data/follow_history.json` | 成功 follow の履歴 | ❌ 履歴 | - |

## 経緯

第三者調査で「candidates.db に 1468件 pending あるが誰も読まない」という指摘を受けたが、実機調査の結果:

```python
follow_status counts: {'failed': 40, 'skipped': 3536}
pending count by source_type: {}
```

→ **pending=0**。candidates.db は「過去に scrape されたが、すべて already_in_history で skip された archive」だった。
1468件 pending 説は誤情報。

## 設計の根拠

1. **executor (follow_rpa_vm.py / follow_host_runner.py / follow_executor.py)** は seed_users.json のみを参照
2. **scrape script (planner/follow_candidates.py)** が新規候補を candidates.db に書き、その中で已 skipped を除いた pending のみを seed_users.json に export する 2 段構成
3. candidates.db は scrape 履歴として残し、重複 scrape を avoid する役割
4. 運用上、SSOT は seed_users.json 1本

## 運用ルール

### 新規追加禁止事項
- ❌ candidates.db を SSOT として executor から直接参照しない
- ❌ candidates.db への手動 INSERT 禁止 (scrape script 経由のみ)
- ❌ seed_users.json を deprecate しない

### 推奨運用 (Phase C-4 で実装)
- 毎日 00:30 に Task Scheduler で `python rakuten-room/bot/planner/follow_candidates.py --scrape` 自動実行
- scrape 成功時、新 candidates のうち pending のみ seed_users.json に merge
- pending=0 が3日連続 → Slack alert (scrape 失敗の早期検知)

## 関連ルール

- `memory/rakuten_follow_account_rule.md`: 第2アカウント禁止、scrape は同一アカウント内のみ
- `memory/rakuten_follow_daily_target.md`: 毎日 2000 件以上 → seed_users.json が枯渇すると目標未達
- `docs/vm_chrome_relogin_runbook.md`: ログイン失効時の復旧

## Phase C-4 への引き継ぎ

- 自動 scrape Task: 毎日 00:30 → seed_users.json 補充
- pending<200 の場合 alert
- candidates.db の archive サイズが過大 (1万件超) になれば古いものを vacuum
