# 楽天ROOM Bot SLI/SLO 定義書

**確定日**: 2026-05-05 | **Phase B-7** | **指示者**: CEO「何年も運用する礎」

## 目的

- 何が「正常」で何が「異常」かの **定量基準** を確立
- patrol が SLO 違反を自動検知して Slack alert
- 障害復旧の優先度判断を SLI で objective に

---

## 4機能の SLI / SLO

### 1. POST (投稿)

| SLI | 定義 | SLO | 違反時 alert level |
|-----|------|-----|-------------------|
| `today_posted` | 当日 post_queue.status='posted' 件数 | ≥80件/日 (目標90-200) | 08:00 で 0 件 → CRITICAL |
| `last_posted_age_hours` | 最後の post から経過時間 | <12h (運用時間内) | >24h → CRITICAL |
| `post_runner_success_rate` | windows_task_post.log の rc=0 比率 (直近30日) | ≥85% | <70% → WARN |
| `pool_count` | source_items.json 件数 | ≥700 (POOL_MIN) | <200 → CRITICAL / <500 → WARN |
| `chrome_profile_collision` | windows_task_post.log の "launch_persistent_context.*closed" 出現頻度 | 0回/日 (Phase A-2 後) | 1回/日 → WARN |

### 2. LIKE (いいね)

| SLI | 定義 | SLO | 違反時 alert level |
|-----|------|-----|-------------------|
| `today_liked` | 当日 like_history.json 件数 | ≥450件/日 (目標 LIKE_DAILY_MIN) | 15:00 で 0 件 → CRITICAL |
| `last_liked_age_hours` | 最後の like から経過時間 | <2h (毎時実行のため) | >4h → WARN / >12h → CRITICAL |
| `like_runner_success_rate` | windows_task_like.log の rc=0 比率 (直近30日) | ≥90% | <80% → WARN |

### 3. FOLLOW (VM・pyautogui)

| SLI | 定義 | SLO | 違反時 alert level |
|-----|------|-----|-------------------|
| `today_followed` | 当日 follow_rpa_log.json success 合計 | ≥2000件/日 (CEO最低目標) | 21:00 で <1500 → WARN |
| `vm_running` | VirtualBox VM "RoomBot" 起動状態 | True | False → CRITICAL (auto-recover) |
| `heartbeat_age_seconds` | follow_heartbeat.json 経過秒 | <180s | >180s → CRITICAL (silent stuck) |
| `login_status` | check_login_status() 結果 | "ok" | "expired" → CRITICAL (CEO手動) |
| `verify_fail_rate` | (verify_sample_out + on_white_area) / total | <20% (Phase A-3 後) | >40% → WARN |
| `seed_pending_count` | seed_users.json 件数 | ≥200 | <100 → WARN / <30 → CRITICAL |

### 4. FOLLOWBACK

| SLI | 定義 | SLO | 違反時 alert level |
|-----|------|-----|-------------------|
| `today_followback` | follow_log.action='followback' DATE=today | ≥30件/日 (FOLLOWBACK_DAILY_TARGET) | 19:00 で 0 件 + pending>0 → WARN |
| `pending_count` | followback_queue.status='pending' | ≥10件 (常時供給) | <3件 → WARN (source 枯渇) |
| `last_followback_age_days` | 最後の followback から経過日 | <2日 | ≥3日 → CRITICAL |

---

## 運用 SLI (横断)

| SLI | SLO | 違反時 alert level |
|-----|-----|-------------------|
| `patrol_age_minutes` | <30分 (15分間隔の2倍以内) | >60分 → CRITICAL |
| `task_scheduler_completeness` | RoomBot_* 必須 task 11個 全存在 | 1個欠落 → CRITICAL (TaskHealthcheck_Daily で検知) |
| `disk_free_gb` | C: ドライブ空き ≥10GB | <5GB → WARN / <2GB → CRITICAL |
| `slack_unread_age_hours` | CEO 未読 Slack <24h | ≥48h → reminder (autonomous) |

---

## 違反時の対応マトリクス

### CRITICAL (即対応)
- **<!channel>** Slack 通知
- 自動復旧試行 (該当する場合: VM startvm / login_expired_flag / Pool replenish)
- 30分以内に CEO 報告 (#NNN)

### WARN (1日以内対応)
- **<!here>** Slack 通知
- 翌日の朝報告で言及
- 再発防止メモを memory/ に追加

### INFO (週次レビュー)
- daily_report に集計記載
- 7日連続発生で WARN 昇格

---

## SLI 計測の実装責任

| SLI | 計測ファイル | 計測関数 |
|-----|------------|---------|
| `today_posted` | room_bot.db | patrol_hourly.check_post() |
| `today_liked` | like_history.json | patrol_hourly.check_like() |
| `today_followed` | follow_rpa_log.json | patrol_hourly.check_follow() |
| `today_followback` | room_bot_v5.db follow_log | patrol_hourly.check_followback() |
| `vm_running` | VBoxManage list runningvms | patrol_hourly.vm_running() |
| `heartbeat_age_seconds` | follow_heartbeat.json | patrol_hourly.check_follow() |
| `login_status` | login_expired_flag.json | patrol_hourly.check_follow() |
| `pool_count` | source_items.json | patrol_hourly.check_post() (Phase B-1 で追加) |
| `task_scheduler_completeness` | schtasks /Query | healthcheck_tasks.py (Phase B-5) |

---

## SLI 履歴の保存

- `state/follow_runtime_state.json` (Phase 2-5 で実装) — patrol 各回の SLI snapshot
- `state/sli_history.jsonl` (新規・Phase C-1 で実装) — 日次 SLI ログ (1行 1日)
- `room_bot_v5.db.execution_log` — action 単位の細粒度 metrics

---

## SLO 違反時の Slack alert 文例

```
<!channel> 【SLO 違反 CRITICAL】
function: follow
SLI: heartbeat_age_seconds = 245s (SLO: <180s)
推定原因: VM bot stuck (silent stuck)
自動対応: vm_follow_launcher.py --force --limit 100
runbook: docs/vm_chrome_relogin_runbook.md
```

---

## 改訂履歴

- 2026-05-05: 初版確定 (Phase B-7)
- Phase C-1 完了時に fail_reason taxonomy を反映
- Phase C-7 (Playwright統一) 後に follow の SLI を update
