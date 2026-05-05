# 楽天ROOM Bot パトロール強化マスタープラン (完全モード)

**確定日**: 2026-05-05 | **指示者**: CEO
**指示原文**: 「二度と同じことを起こさないように様々なパトロールの強化手順書」

> ⚠️ **本書は再発防止の集大成**。過去の障害パターン全てに対する patrol 検知 + 自動復旧 + escalation を網羅。
> `docs/vm_full_completion_design.md` (VM 完結案) と連動。

---

## 1. 過去の障害パターン (本書で網羅対象)

### 1.1 2026-04 〜 05 で発生した重大障害 TOP 10

| # | 発生日 | 障害 | 影響 | 検知遅延 |
|---|--------|-----|------|---------|
| 1 | 4/14-19 | フォロー連続 6日間 0件 (seed 枯渇 + login失効) | 12,000件 follow 機会損失 | 6日間気付かず |
| 2 | 4/21 11:00 | launcher cmd 非 foreground bug 4回再発 | 1日停止 | 各回 1-2h |
| 3 | 5/2-4 | フォロー 3日間ほぼ 0件 (verify 座標問題) | 3日分損失 | 3日気付かず |
| 4 | 5/5 11:00 | VM 停止 + Windows OOBE 画面 | 2h 停止 | patrol で検知 |
| 5 | 5/5 16:43 | HOST cmd window flash バグ | CEO 視認 | 即時 |
| 6 | 5/5 17:14 | VM reset 後 IME 切替不全 → launcher 失敗 | 30分 | launcher fail |
| 7 | 5/5 ログイン失効 | 第3者調査で発覚 (login_expired_flag が立たず) | 部分稼働 | 数時間 |
| 8 | 4/21 同時並列 trap | VM+HOST 同時 → 997件 trap | 1日停止 | 翌日 |
| 9 | 5/5 Chrome profile 衝突 | POST Batch1 09:00 失敗 | 1 batch loss | windows_task_log |
| 10 | 4/13-14 rate_limit cluster | 26 session 連続失敗 | 1日 0件 | rate_limit log |

### 1.2 障害の **根本層別** 分類 (本 patrol で検知すべき層)

```
Layer 7: ビジネス層 (目標未達・スプシ乖離)
Layer 6: アプリケーション層 (機能異常・session stuck)
Layer 5: 楽天 API 層 (rate_limit / login 失効)
Layer 4: bot プロセス層 (Python crash / heartbeat stale)
Layer 3: Chrome 層 (profile 破損 / cookie 期限)
Layer 2: VM 層 (起動失敗 / OOBE / リソース不足)
Layer 1: HOST 層 (PC 再起動 / Task Scheduler 停止)
Layer 0: 環境層 (ネットワーク / ディスク / メモリ)
```

**現行 patrol_hourly.py は Layer 4-6 のみカバー**。Layer 0-3, 7 は未検知。

---

## 2. 強化された patrol アーキテクチャ

### 2.1 多層 patrol 設計

```
patrol_v6 (新)
├── patrol_layer0_environment.py  (Disk / Memory / Network)
├── patrol_layer1_host.py         (Task Scheduler 健全性 / HOST CPU)
├── patrol_layer2_vm.py           (VM 起動状態 / GuestAdditionsRunLevel)
├── patrol_layer3_chrome.py       (4 profile の SingletonLock / cookie 鮮度)
├── patrol_layer4_process.py      (VM HTTP server / 各機能 process)
├── patrol_layer5_rakuten.py      (rate_limit 残量 / login 状態)
├── patrol_layer6_session.py      (各機能の heartbeat / 進捗)
├── patrol_layer7_business.py     (スプシ目標達成率 / 累計実績)
└── patrol_orchestrator.py        (全 layer 統合 + 自動復旧 + Slack escalation)
```

### 2.2 実行頻度

| Layer | 実行頻度 | 理由 |
|-------|---------|------|
| L0 (環境) | 毎時 | 変動少 |
| L1 (HOST) | 15分 | Task Scheduler 監視 |
| L2 (VM) | 5分 | VM 停止検知優先 |
| L3 (Chrome) | 15分 | profile 状態 |
| L4 (Process) | 3分 | session 進行監視 |
| L5 (Rakuten) | 15分 | rate_limit 累計 |
| L6 (Session) | 3分 | heartbeat staleness |
| L7 (Business) | 1時間 | 達成率は遅れて反映 |

### 2.3 全 patrol 起動方法 (Task Scheduler)

```
RoomBot_Patrol_L0_Hourly      毎時 0分
RoomBot_Patrol_L1_15min       毎15分
RoomBot_Patrol_L2_5min        毎5分
RoomBot_Patrol_L3_15min       毎15分
RoomBot_Patrol_L4_3min        毎3分 (3回/15分)
RoomBot_Patrol_L5_15min       毎15分
RoomBot_Patrol_L6_3min        毎3分
RoomBot_Patrol_L7_Hourly      毎時 30分
RoomBot_Patrol_Orchestrator   毎15分 (全 layer 統合判定)
```

---

## 3. 各 Layer の検知項目 (詳細)

### 3.1 Layer 0: 環境層

**検知項目**:
| SLI | SLO | 違反 alert |
|-----|-----|-----------|
| C: ドライブ空き | ≥10GB | <5GB CRITICAL |
| HOST メモリ Free | ≥8GB | <4GB WARN |
| Network 疎通 (rakuten.co.jp) | OK | NG WARN |
| 現在時刻と NTP 差 | <1秒 | ≥10秒 WARN |

**実装**:
```python
# patrol_layer0_environment.py
def check_disk():
    free_gb = shutil.disk_usage("C:\\").free / (1024**3)
    return {"sli": "disk_free_gb", "value": free_gb, "ok": free_gb >= 10}

def check_memory():
    free_gb = psutil.virtual_memory().available / (1024**3)
    return {"sli": "memory_free_gb", "value": free_gb, "ok": free_gb >= 8}

def check_network():
    try:
        r = requests.get("https://room.rakuten.co.jp/", timeout=5)
        return {"sli": "rakuten_reachable", "value": r.status_code, "ok": r.status_code == 200}
    except: return {"sli": "rakuten_reachable", "value": "timeout", "ok": False}
```

### 3.2 Layer 1: HOST 層

**検知項目**:
| SLI | SLO | 違反 alert |
|-----|-----|-----------|
| Task Scheduler サービス | Running | 停止 CRITICAL |
| 必須 Task 存在数 | 16/16 | 1個欠落 WARN / 3個 CRITICAL |
| 直近 LastTaskResult エラー比率 | <20% | ≥50% WARN |
| HOST PC uptime | ≥1時間 | <30分 INFO (再起動直後) |

**実装**: 既存 `ops/scheduler/healthcheck_tasks.py` を拡張

### 3.3 Layer 2: VM 層

**検知項目**:
| SLI | SLO | 違反 alert | 自動復旧 |
|-----|-----|-----------|---------|
| VMState | running | poweroff CRITICAL | startvm 自動 |
| GuestAdditionsRunLevel | 3 | <3 WARN (起動中) | 待機 |
| VM uptime | <72時間 | ≥72時間 INFO (定期再起動推奨) | 計画再起動 |
| VM CPU 負荷 | <80% | ≥95% WARN | swap 検査 |
| VM メモリ usage | <80% | ≥95% CRITICAL | bot abort |

**実装**:
```python
# patrol_layer2_vm.py
def check_vm():
    rc, out = run_vbox("showvminfo", "RoomBot", "--machinereadable")
    state = parse_kv(out, "VMState")
    run_level = parse_kv(out, "GuestAdditionsRunLevel")
    if state != "running":
        return {"alert": "CRITICAL", "auto_recover": "startvm"}
    if int(run_level) < 3:
        return {"alert": "WARN", "auto_recover": "wait"}
    return {"alert": None}
```

### 3.4 Layer 3: Chrome 層

**検知項目** (4 profile × 各項目):
| SLI | SLO | 違反 alert |
|-----|-----|-----------|
| profile dir 存在 | True | False CRITICAL |
| Cookies file 存在 | True | False CRITICAL |
| SingletonLock 残置時間 | <5分 | ≥30分 WARN |
| cookie 最終更新 | <90日前 | ≥90日 WARN |
| profile size | 500MB-3GB | 範囲外 INFO |

**実装**: 既存 `ops/checks/chrome_health_check.py` を拡張 + VM 内で同等チェック

### 3.5 Layer 4: Process 層

**検知項目**:
| SLI | SLO | 違反 alert | 自動復旧 |
|-----|-----|-----------|---------|
| VM HTTP server 応答 | <1秒 | timeout CRITICAL | server 再起動 |
| 各機能の Python process 数 | 0 or 1 | ≥2 (重複) WARN | duplicate kill |
| Python メモリリーク | <1GB | ≥2GB WARN | restart |
| `auto_relaunch_watcher.py` alive | True | False CRITICAL | manual |

**実装**:
```python
# patrol_layer4_process.py
def check_vm_http():
    try:
        r = requests.get("http://localhost:18765/", timeout=1)
        return {"alert": None if r.status_code == 200 else "CRITICAL"}
    except:
        return {"alert": "CRITICAL", "auto_recover": "vm_http_restart"}
```

### 3.6 Layer 5: 楽天 API 層

**検知項目**:
| SLI | SLO | 違反 alert | 対応 |
|-----|-----|-----------|------|
| login_status (各 profile) | "ok" | "expired" CRITICAL | CEO Slack <!channel> |
| follow 24h cumulative | <833 | ≥800 WARN (上限近) | session abort |
| rate_limit count 24h | 0 | ≥3 WARN / ≥10 CRITICAL | cooldown 90分 |
| API HTTP 429 受信 | 0/h | ≥1/h WARN | rate_limit_detector |

**実装**: VM 内 runner が rate_limit / login_expired を検知 → webhook で HOST に push → patrol が DB 集計

### 3.7 Layer 6: Session 層

**検知項目** (4機能 × 各):
| SLI | SLO | 違反 alert | 自動復旧 |
|-----|-----|-----------|---------|
| heartbeat age | <180秒 | ≥300秒 CRITICAL | session abort + relaunch |
| session 開始から進捗 0件時間 | <10分 | ≥20分 WARN | analyze fail_stats |
| session ごとの success | ≥30件/session | <10件 WARN | seed 入替検討 |
| 連続 fail | <10回 | ≥20回 WARN | abort |

**実装**: 既存 `patrol_hourly.py` の heartbeat 検知を流用 + 全 4機能対応に拡張

### 3.8 Layer 7: Business 層

**検知項目** (スプシ目標との比較):
| SLI | SLO | 違反 alert |
|-----|-----|-----------|
| 当日 POST 達成率 | ≥80% (08:00 以降) | <50% WARN / 0% CRITICAL |
| 当日 LIKE 達成率 | ≥80% (15:00 以降) | <50% WARN |
| 当日 FOLLOW 達成率 | ≥80% (21:00 以降) | <50% WARN |
| 当日 FB 達成率 | ≥80% (19:00 以降) | <50% WARN |
| 累計 7日 達成率 | ≥85% | <70% CRITICAL |

**実装**:
```python
# patrol_layer7_business.py
def check_targets():
    targets = load_ssot_targets()  # スプシから取得
    actuals = query_db_actuals()   # SQLite から取得
    for func in ["post", "like", "follow", "followback"]:
        achievement = actuals[func] / targets[func]
        if achievement < 0.5 and time_passed_threshold(func):
            alert(f"{func} 達成率 {achievement:.0%} < 50%")
```

---

## 4. 自動復旧マトリクス

### 4.1 復旧アクション一覧

| アクション | 適用条件 | 効果 |
|----------|---------|------|
| `vm_startvm` | VMState=poweroff | VM 起動 |
| `vm_http_restart` | server 応答なし | server 再起動 (VBoxManage guestcontrol) |
| `session_abort` | heartbeat stale 5分以上 | 該当 session の python kill |
| `session_relaunch` | abort 後 | webhook 経由で再起動 |
| `cooldown_90min` | rate_limit 検知 | 90分 silence |
| `seed_rotate` | already_followed 高比率 | 別 seed group へ切替 |
| `chrome_profile_unlock` | SingletonLock 30分超 | lock file 削除 |
| `disk_cleanup` | C: 空き <5GB | logs/screenshots の古い分削除 |
| `escalate_ceo` | CRITICAL かつ 自動復旧失敗 | Slack <!channel> |

### 4.2 復旧フロー (障害ごと)

#### 4.2.1 VM 停止 → 自動起動

```
patrol_layer2 検知 (VMState=poweroff)
  ↓
patrol_orchestrator が判定: 自動復旧可能
  ↓
1. VBoxManage startvm RoomBot --type gui
2. wait_vm_ready (GuestAdditionsRunLevel=3 + IME 切替 30秒)
3. VM HTTP server alive 確認
4. 直近 session の log を VM から取得 (debug)
5. 復旧成功 → patrol 通常モード復帰
6. 失敗 → CEO Slack escalate
```

#### 4.2.2 ログイン失効 → CEO 手動依頼

```
patrol_layer5 検知 (login_status=expired)
  ↓
1. 該当 profile の session abort
2. CEO Slack <!channel> 通知 (どの profile が失効したか・runbook URL)
3. 30分間 該当機能の launch を停止
4. CEO が手動再ログイン後、login_expired_flag 削除
5. patrol が flag 不在を検知 → 該当機能 launch 再開
```

#### 4.2.3 rate_limit cluster (3+ session 連続)

```
patrol_layer5 検知 (rate_limit_count >= 3 in 1h)
  ↓
1. 該当機能の自動 cooldown 90分
2. CEO Slack <!here> 通知 (上限到達)
3. 90分後 自動再開
4. 再発 → CEO 判断求める (上限引き上げ or 戦略変更)
```

#### 4.2.4 silent stuck (heartbeat stale)

```
patrol_layer6 検知 (hb_age >= 300秒)
  ↓
1. VM HTTP /abort で該当 session 強制終了
2. fail_stats を保存
3. webhook で同じ機能を再起動 (limit 半分で安全モード)
4. 2回連続再発 → CEO Slack escalate
```

---

## 5. 再発防止チェックリスト (毎週末実行)

### 5.1 矛盾検出

```bash
# weekly_consistency_check.py (新規・cron で毎週日曜 23:00)
1. 設計書 (docs/*.md) と実装の乖離検出
   - phase_c9_vm_webhook_plan.md 「shared folder 廃止予定」 vs 実装で shared folder 追加
2. 旧コード参照検出 (deprecated 関数を呼んでいる箇所)
3. config.py の hard-code 値が SSOT (スプシ) と乖離していないか
4. memory/ ルールの矛盾 (例: 旧 2000件 vs 新スプシ 1095)
```

### 5.2 障害再発判定

```bash
# weekly_recurrence_detector.py (新規)
過去 7日間の patrol log を分析:
1. 同じ alert が 3回以上発生 → root cause 未解決と判定
2. CEO Slack に「再発 alert」レポート送信
3. 該当 root cause の詳細調査タスク化
```

### 5.3 SLO レビュー (月次)

```bash
# monthly_slo_review.py (新規・毎月 1日)
1. 全 SLI の月次達成率を集計
2. SLO 未達 SLI を identify
3. CEO Slack に月次レポート送信
4. SLO 値の見直しが必要なら CEO に提案
```

---

## 6. 既存 patrol_hourly.py からの移行

### 6.1 段階移行 (1機能ずつ)

| 週 | 作業 |
|----|------|
| W1 | patrol_layer0_environment / L1_host / L7_business 新規実装 |
| W2 | patrol_layer2_vm / L3_chrome 新規実装 (既存 healthcheck_tasks.py 統合) |
| W3 | patrol_layer4_process / L5_rakuten / L6_session 新規実装 |
| W4 | patrol_orchestrator 統合 + 旧 patrol_hourly.py archive |
| W5 | 自動復旧マトリクス完全実装 |
| W6 | 再発防止チェックリスト (weekly/monthly) 実装 |

### 6.2 並行運用期間 (W1-W4)

新 patrol と旧 patrol_hourly を並行運用。Slack 通知は重複しないよう新側のみ有効化。

---

## 7. ダッシュボード強化

### 7.1 既存 dashboard_report.py の拡張

```python
# 1日3回 (07:00 / 12:00 / 21:00) 投稿に加えて:
1. SLO 違反検知時 即時投稿 (CRITICAL のみ)
2. 1週間 health チェックサマリ (毎週月曜 09:00)
3. 自動復旧アクション履歴 (1日1回)
```

### 7.2 新 dashboard_v6.py (任意拡張)

| metric | 表示内容 |
|--------|---------|
| 4機能の SLO 達成状況 | 信号機 (緑/黄/赤) |
| 自動復旧 24h 件数 | 数値 |
| 累計 idle 時間 (前日) | 時間 |
| rate_limit 24h 件数 | 数値 |
| HOST Chrome 占有時間 | 0時間 (理想) |

---

## 8. 通知の階層化

### 8.1 Alert Level

| Level | 通知方法 | 例 |
|-------|---------|------|
| **CRITICAL** | Slack `<!channel>` (全員 mention) + voicevox 音声 | VM 停止・login 失効・disk <5GB |
| **WARN** | Slack `<!here>` (オンライン mention) | rate_limit cluster・SLO 未達 |
| **INFO** | Slack thread + dashboard | 通常進捗・統計 |
| **DEBUG** | DB 記録のみ | 各 patrol の実行 log |

### 8.2 通知の重複防止 (throttling)

- 同じ alert は 2時間 throttle
- 同じ機能の同じ root cause は 1日に 3回まで

---

## 9. 関連ファイル

### 9.1 新規作成

```
ops/patrol_v6/
├── patrol_orchestrator.py          (メイン)
├── layer0_environment.py
├── layer1_host.py
├── layer2_vm.py
├── layer3_chrome.py
├── layer4_process.py
├── layer5_rakuten.py
├── layer6_session.py
├── layer7_business.py
├── auto_recovery.py                (復旧マトリクス実装)
├── consistency_check.py            (週次矛盾検出)
├── recurrence_detector.py          (週次再発検知)
└── slo_review.py                   (月次 SLO レビュー)
```

### 9.2 既存 archive 予定

| 旧ファイル | 移動先 | 理由 |
|----------|-------|------|
| ops/patrol_hourly.py | archive/v5_legacy/ | patrol_orchestrator で代替 |
| ops/patrol_with_sheet_sync.py | archive/v5_legacy/ | layer7 で代替 |
| ops/follow_watchdog.py | archive/v5_legacy/ | layer2 で代替 |

---

## 10. SLI/SLO 一覧表 (既存 docs/sli_slo.md を本書で拡張)

合計 SLI: 50+ (8 Layer × 各 5-10 項目)

詳細は別途 `docs/sli_slo_v6.md` で完全一覧化予定。

---

## 11. 完了基準

1. ✅ 8 Layer 全実装
2. ✅ 自動復旧マトリクス 9 アクション実装
3. ✅ 過去 10 障害パターン全てが検知可能
4. ✅ 再発防止チェックリスト 3種 (consistency / recurrence / SLO) 稼働
5. ✅ 1ヶ月連続 zero CRITICAL 達成
6. ✅ CEO 「同じ問題を二度と起こさない」状態の実現

---

## 12. 関連設計書

- `docs/vm_full_completion_design.md` (VM 完結案・本書と連動)
- `docs/sli_slo.md` (現行 SLI/SLO 文書・本書で v6 化)
- `docs/phase_c8_sqlite_ssot_plan.md` (DB SSOT・patrol が読む先)
- `memory/rakuten_room_targets_ssot.md` (Layer 7 の参照源)
- `memory/patrol_4functions_rule.md` (現行 patrol ルール・本書で deprecated)

---

## 13. 実装スケジュール (vm_full_completion_design と並行)

| 週 | VM 完結 | パトロール強化 |
|----|---------|---------------|
| W1 | VM 環境構築 | L0 / L1 / L7 実装 |
| W2 | POST/FB を VM 移行 | L2 / L3 実装 |
| W3 | LIKE を VM 移行 | L4 / L5 / L6 実装 |
| W4 | FOLLOW を VM 移行 (Playwright化) | orchestrator 統合 |
| W5 | HOST archive | 自動復旧完全実装 |
| W6 | 検証 | 再発防止チェックリスト |
| W7 | 礎完成 | 1ヶ月連続 zero downtime 検証開始 |

合計: **6-7週間** (1.5ヶ月)
