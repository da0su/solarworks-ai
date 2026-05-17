# 目標必達 Loop 設計案 (CEO 5/17 22:00 指示 + Codex 18回目 提案 反映)

## CEO 指示
> 「スプシの数字が未達成なのに監査ができていない。これもプランニングに入れて、目標必達するまで考え続ける設計にしなさい」

## 既存資産
- `shared/daily_pacer.py`: target/actual/expected_now/per_cycle_target 計算 (passive)
- `state/daily_targets_ssot.json`: SSOT (CEO 確定値・6h cache)
- `shared/follow_history_reader.py`: FOLLOW 実績
- `shared/profile_health.py` (NEW): profile 健全性 watchdog

## 設計

### Phase 1: target_attainment_controller.py (新規)
**60分間隔 Task Scheduler で起動**

```python
def run():
    for fn in ["POST", "FOLLOW", "LIKE", "FB"]:
        d = get_pace_directive(fn)
        if d["action"] == "stop":
            continue  # 達成済
        # 未達閾値: 期待値の 70% 未満 = 異常
        pct = (d["actual"] / max(d["expected_now"], 1)) * 100
        if pct < 70:
            # 原因分類 → playbook → 再 trigger
            cause = classify_root_cause(fn)
            playbook = SELF_HEALING_PLAYBOOKS[cause]
            executed = playbook.execute()
            log_attempt(fn, cause, executed)
            # 30分後に再確認 (子 task)
            schedule_recheck(fn, after_min=30)
```

### Phase 2: 原因分類 (classify_root_cause)
```python
def classify_root_cause(fn):
    # 1. Profile 不一致 (chrome_profile_post が別アカウント等)
    if not check_profile_health(): return "profile_mismatch"
    # 2. Login expired
    if not bm.check_login_status(): return "login_expired"
    # 3. Lock busy
    if has_stale_lock(fn): return "lock_busy"
    # 4. False success (DB posted but ROOM 商品数 不変)
    if false_success_pattern(fn): return "false_success_rakuten_reject"
    # 5. Rate limit
    if recent_rate_limit(fn): return "rate_limited"
    # 6. No items (queue 枯渇)
    if queue_empty(fn): return "queue_empty"
    return "unknown"
```

### Phase 3: Self-healing playbook
| cause | playbook |
|---|---|
| profile_mismatch | CEO に手動 login 要請 Slack |
| login_expired | profile_login.bat 自動 trigger + Slack |
| lock_busy | stale lock cleanup (PID + TTL 確認後) |
| false_success_rakuten_reject | 24h cooldown + 別アカウント切替検討 Slack |
| rate_limited | 60-120min cooldown |
| queue_empty | replenish task 即 trigger |
| unknown | Slack <!channel> CEO 判断 |

### Phase 4: サーキットブレーカ
- 同一 cause が 3 回連続 → 6h cooldown
- 6h 経過で再試行
- 24h 解消しなければ stop + CEO 緊急通知

### Phase 5: Slack 通知ルール (Codex #6)
- 状態変化時のみ (毎時 noisy 通知禁止)
- KPI 未達 + 自動修復実行時: 1通
- サーキット開放時: 1通
- 日次 1 通 summary

## CEO 「目標必達まで考え続ける」の体現
1. 60min 毎に未達 check
2. 未達なら原因分類 + 自動修復 + 再 trigger
3. 失敗ループを circuit breaker で守りつつ continual retry
4. 24h で解消しない場合のみ人間介入要求

## 実装スケジュール
- [完了] shared/profile_health.py (Phase A)
- [完了] post_executor.py v8 (Phase A)
- [次] ops/target_attainment_controller.py 新規 (Phase B)
- [次] Task Scheduler 60min interval 登録 (Phase B)
- [次] Self-healing playbooks 個別実装 (Phase B-C)

## 残課題 (CEO 確認後)
- CEO の手動 login (chrome_profile_post 修復) 後に本格運用開始
- 「未達 70%」閾値の調整 (運用しながら学習)
