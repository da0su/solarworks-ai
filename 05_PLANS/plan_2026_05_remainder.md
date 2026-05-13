# 2026年5月 残り 19 日 (5/13-5/31) 達成プラン

**作成日**: 2026-05-12
**CEO 指示**: 「全体的に目標の未達成が続いています。残り 2 週間で毎日達成できるように、最大限プランを考え直し、修正実装してください」

---

## 1. 現状把握 (5/01-5/12)

| 機能 | 月累計目標 | 実績 | 達成率 | 状況 |
|------|-----------|------|--------|------|
| POST | 711 | 934 | **131%** | ✅ 既達 |
| LIKE | 3,589 | 9,712 | **271%** | ✅ 既達 |
| FB | 360 | 358 | **99%** | ≈ 順調 |
| **FOLLOW** | **13,754** | **5,431** | **39%** | ❌ **大幅未達** |

→ **5月全体のボトルネック = FOLLOW のみ**。残 19日で +8,323 件 = **平均 438/日** 必要。

---

## 2. FOLLOW 大幅未達の真因 (確定済)

### 2-A. 真因 1: follow_one が遅い (mean 45-60s/件)
- 5/8 best 1069 件達成日は ~4s/件 ペース
- 現状: page goto 20s + click 3s + 待機 0.3-0.8s + verify = ~25-50s/件
- 14 min trigger で max 15-20 件しか attempt 不可

### 2-A 改善:
- follow_one の **goto timeout を 15→10s に短縮 (5/8 並み)** に戻す
- click → wait → verify の wait を 0.3-0.5s → 0.1-0.3s に短縮
- 失敗時 retry 撤去 (1 試行のみ・素早く次へ)

### 2-B. 真因 2: harvest 時間が長い
- 現状 harvest_time_cap = 5分・15 iter × 0.7s scroll
- 早 follow phase 開始のため harvest 3分に短縮
- pool_target = 60→40 (target × 1.3)

### 2-C. 真因 3: skip 率 50%
- 既フォロー先を harvest pool に入れて wasted attempt
- skip_discover history を harvest 時点で利用するよう load_seeds() で読込

---

## 3. 残 19 日の目標分配

### 日次目標 (CEO スプシ既設定値・1077-1278/日)
| 期間 | POST/day | FOLLOW/day | LIKE/day | FB/day |
|------|---------|-----------|----------|--------|
| 通常日 | 60-64 | **1077-1278** | 261-347 | 30 |

### 達成戦略

#### POST (達成済・現状維持)
- 4 batches × ~30 件 = 120/日 程度を維持
- 商品プール枯渇予防 (RoomBot_Replenish_Daily 監視)

#### LIKE (達成済・現状維持)
- LIKE_Hourly 1cycle ~50-70 件で目標達成中
- intermittent rate limit を許容

#### FB (順調・現状維持)
- 自然到来 follower への自動 followback
- 1 cycle 1-5 件 ペース

#### FOLLOW (★最重要・抜本改革必要)
**目標**: 1077件/日 × 19日 = 8,323 件
**現状ペース**: 30/h × 24h = 720/日 (5/8 1069 達成済なので可能)

##### 実装変更 (5/13 deploy):
1. **follow_one 高速化**:
   - goto timeout 20s → 15s
   - 待機 0.3-0.8s → 0.1-0.3s (FOLLOW_INTERVAL_MIN/MAX)
   - 失敗時 retry 撤去
   → per-follow time 45s → 15-20s 期待

2. **harvest 短縮**:
   - harvest_time_cap 5分 → 3分
   - pool_target 60 → 40

3. **bat target/duration 調整**:
   - target 30 → 60 (上限解放)
   - duration_min 14 → 13 (overlap 余裕)

4. **皆勤評価**:
   - 各 trigger で +20-30 follow 期待 (15分毎 = 1h で 80-120)
   - 1日 24 × 60 = 1440 件 理論上可能

5. **モニタリング**:
   - 毎時 follow count をスプシ累計に反映 (daily_log_writer 拡張)
   - 30/h pace を切ったら Slack 警告 (緊急のみ)

---

## 4. 監視・対応スケジュール

| 時刻 | 監視内容 | アクション |
|------|---------|-----------|
| 0:30/6:30/12:30/18:30 | 4h 累計 follow チェック | 250/4h 未達なら原因究明 |
| 06:00 | DailyReset 起動 | スプシ自動更新確認 |
| 07:00 | Dashboard Morning | 前日 actuals 確認 |
| 21:00 | Dashboard Night | 当日進捗 + 翌日予想 |

CEO 通知:
- **緊急時のみ Slack** (4h 完全停止 / Rakuten アカウント停止 / data 損失)
- 平常 OK 報告は **スプシ累計 + dashboard 自動送信**のみ

---

## 5. 実装手順 (5/12 〜 5/13)

### Step 1 (今すぐ・15min)
- follow_one コード調整 (commit)
- bat target=60, duration=13 (commit)
- harvest_time_cap=180 (commit)

### Step 2 (5/13 朝)
- 1 日通常稼働で実証 (~1000件目標)
- 達成しない場合 Step 3

### Step 3 (必要なら 5/14)
- Task Scheduler trigger 間隔短縮 (15min → 12min)
- または並列 trigger (2 process 並列)

---

## 6. 緊急時の判断基準

production down 級のみ Slack 緊急報告:
- 全 bot 停止 (4機能全部 1h 以上稼働せず)
- Rakuten アカウント suspend 検知
- data 損失 (follow_history.json などの破損)

それ以外 (1 機能 4h 停止等) は自律修復・翌朝 dashboard で報告。

---

## 7. レビュータイミング

- **5/14 朝**: Step 2 結果評価 → 必要なら Step 3 投入
- **5/19 朝**: 1 週間進捗確認 → 月末達成見込み判定
- **5/25 朝**: 残 1 週間判定 → 加速策投入要否
- **5/31 夜**: 月末締め
