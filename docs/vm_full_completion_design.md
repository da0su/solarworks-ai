# 楽天ROOM Bot VirtualBox 完結設計書 (完全モード)

**確定日**: 2026-05-05 | **指示者**: CEO
**指示原文**: 「VB内での楽天ROOM実装完結案を完全モードでつくって。やはり、メインPCのクロムをつかわれると不便。」

> ⚠️ **本設計書は phase_c7_playwright_unification_plan.md (HOST 統一案) を上書きする**。
> CEO 指示でメインPC Chrome の bot 使用を禁止し、VirtualBox 内で全機能完結する方針に変更。

---

## 1. 設計目的

### 1.1 CEO の真の要求

| 要求 | 解釈 |
|------|------|
| 「VB内で完結」 | 楽天ROOM 4機能 全部 VM 内で動かす |
| 「完全モード」 | 段階移行ではなく完成形を一気に設計 |
| 「メインPCのChromeを使われると不便」 | HOST PC の Chrome は CEO 専用・bot は使わない |

### 1.2 達成すべき状態 (Definition of Done)

1. **HOST PC** は **monitoring + scheduling 専用**
2. **VM RoomBot** は楽天ROOM 4機能 (POST/LIKE/FOLLOW/FOLLOWBACK) の **実行専用**
3. HOST の Chrome は bot に占有されない (CEO の業務 Chrome と完全分離)
4. 4機能とも VM 内 Playwright で動作 (pyautogui 廃止)
5. VM ↔ HOST 通信は HTTP webhook (shared folder SPOF 排除)

---

## 2. 現行 vs 新設計の比較

### 2.1 現行アーキテクチャ (問題)

```
HOST PC (CEO 業務 + bot 実行混在)
├── Chrome (CEO 業務 + chrome_profile_post + _like + _followback) ← 競合
├── Python: orchestrator_v5 / queue_executor / like_executor / followback_executor
├── Task Scheduler: RoomBot_POST_Batch1/2/3, LIKE_Hourly, FB_Hourly, Patrol
└── shared folder ⇄ VM (heartbeat / log / seed_users)
                        ↓
VM RoomBot (Follow 専用・pyautogui)
├── Chrome (chrome_profile_follow)
├── Python: follow_rpa_vm.py (2398行 pyautogui + OCR + 座標 hardcode)
└── 不安定: 解像度 drift / IME 文字化け / cmd flash
```

**問題**:
- HOST Chrome が bot で占有 (CEO 業務に支障)
- 4機能で **2つの別実装** (Playwright と pyautogui)
- shared folder SPOF
- 5回繰り返したバグの根本構造未解決

### 2.2 新設計 (VM 完結)

```
HOST PC (monitoring 専用)
├── Chrome (CEO 業務専用) ← bot 占有なし
├── Python: monitoring/patrol_v6.py (DB query 専用)
├── Task Scheduler: VM 制御 trigger のみ
│   - VM 起動・停止・再起動
│   - HTTP webhook 経由で VM 内 4機能を起動
└── webhook receiver (port 18765)
                        ↓ HTTP webhook
VM RoomBot (楽天ROOM 4機能 完結)
├── Chrome × 4 profile
│   - chrome_profile_post
│   - chrome_profile_like
│   - chrome_profile_followback
│   - chrome_profile_follow
├── Python: rakuten_room_runner.py (Playwright 統一 unified)
│   - mode=post / like / follow / followback で起動
│   - 各 mode で対応する profile + executor を起動
├── HTTP server (port 8765 → host 18765)
│   - GET /status (現状)
│   - POST /run (機能実行 trigger)
│   - POST /heartbeat (HOST へ push)
│   - GET /screenshot (VM 内画面)
└── 動作: 全機能 Playwright + DOM ベース (座標依存なし)
```

---

## 3. VM 内 環境セットアップ詳細

### 3.1 VM スペック (確定)

| 項目 | 値 |
|------|-----|
| OS | Windows 10 64bit |
| Memory | 16GB |
| CPU | 4 cores |
| VRAM | 128MB |
| Disk | 80GB+ (chrome profile × 4 で 5GB) |
| Network | NAT + Port Forwarding (8765→18765) |
| Shared Folder | `\\VBOXSVR\share` ⇄ HOST `rakuten-room/bot/executor` |
| GuestAdditions | RunLevel=3 (auto-login 有効) |

### 3.2 VM 内 ソフトウェア (新規インストール)

```bash
# VM 内 Windows 10 で順次インストール
1. Python 3.12
2. Chrome (最新版)
3. Playwright (pip install playwright + python -m playwright install chromium)
4. uvicorn + fastapi (HTTP server 用)
5. requests (HOST 通信用)
6. gspread + service_account (SSOT スプシ参照)
```

### 3.3 VM 内 ディレクトリ構造

```
C:\Users\cyber\Desktop\rakuten_room_bot\
├── runner\
│   ├── rakuten_room_runner.py      # ← unified entry point
│   ├── post_executor.py            # POST 実行 (Playwright)
│   ├── like_executor.py            # LIKE 実行
│   ├── follow_executor.py          # FOLLOW 実行
│   ├── followback_executor.py      # FB 実行
│   ├── browser_manager.py          # 共通 Chrome 管理
│   └── shared_logic.py             # rate_limit / retry 等
├── server\
│   ├── http_server.py              # FastAPI server (port 8765)
│   └── webhook_client.py           # HOST への push
├── data\
│   ├── chrome_profile_post\        # POST 専用 (1.2GB cookie 引継ぎ済)
│   ├── chrome_profile_like\        # LIKE 専用
│   ├── chrome_profile_followback\  # FB 専用
│   ├── chrome_profile_follow\      # Follow 専用
│   ├── seed_users.json             # フォロー対象
│   ├── source_items.json           # 投稿候補
│   └── room_bot_v6.db              # SQLite SSOT (Phase 3 で導入)
├── logs\
│   ├── post_session.log
│   ├── like_session.log
│   ├── follow_session.log
│   └── followback_session.log
└── credentials\
    └── sheets_service_account.json  # SSOT スプシ用
```

### 3.4 ポート設定

| Port | 用途 |
|------|------|
| VM Guest 8765 | HTTP server (受信) |
| HOST 18765 | VM への port forward |
| HOST 18766 | HOST 側 webhook receiver (VM からの push) |

### 3.5 自動起動 (Windows VM)

VM 内で Windows 起動時に自動実行:
- 自動ログイン (cyber アカウント)
- スタートアップに `http_server.py` を登録
- スタートアップに `auto_relaunch_watcher.py` を登録 (server crash 時の自動再起動)

---

## 4. rakuten_room_runner.py (VM 内 unified entry point) 設計

### 4.1 起動方法

```bash
# VM 内 cmd
python C:\Users\cyber\Desktop\rakuten_room_bot\runner\rakuten_room_runner.py --mode post --batch 1 --limit 50
python C:\Users\cyber\Desktop\rakuten_room_bot\runner\rakuten_room_runner.py --mode like --limit 100
python C:\Users\cyber\Desktop\rakuten_room_bot\runner\rakuten_room_runner.py --mode follow --limit 200
python C:\Users\cyber\Desktop\rakuten_room_bot\runner\rakuten_room_runner.py --mode followback --limit 30
```

### 4.2 共通フロー (全 mode 共通)

```python
def main():
    args = parse_args()  # mode, limit, batch, force

    # 1. preflight
    config = load_config(args.mode)
    profile = config.get_chrome_profile(args.mode)
    if not is_logged_in(profile):
        notify_host("login_expired", args.mode)
        return 2

    # 2. heartbeat 開始 (30秒スロットル)
    hb = HeartbeatPusher(action=args.mode)
    hb.write(phase="startup")

    # 3. Chrome 起動 (Playwright)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,  # CEO 業務 Chrome ではないので headless でも OK
            executable_path=CHROME,
            args=PLAYWRIGHT_ARGS,
        )
        page = ctx.new_page()

        # 4. mode 別 executor 呼び出し
        executor = get_executor(args.mode)
        result = executor.run(page, limit=args.limit, batch=args.batch, hb=hb)

        # 5. shutdown
        hb.write(phase="shutdown", **result)
        ctx.close()

    # 6. HOST に結果 push
    notify_host("session_complete", args.mode, result)
    return 0
```

### 4.3 各機能の executor (Playwright DOM ベース)

#### 4.3.1 post_executor.py
- profile: chrome_profile_post
- 入力: source_items.json の各 item
- フロー: shop URL → 共有 → ROOM 投稿 → 確認
- DOM セレクタ: `button.share-button` / `a[href*='/mix/collect']` / `textarea.review` 等

#### 4.3.2 like_executor.py
- profile: chrome_profile_like
- 入力: feed URL list
- フロー: feed scroll → like button click → 検証
- DOM セレクタ: `a.icon-like.right:not(.isLiked)`
- **これは現状の HOST 実装をそのまま VM に移植**

#### 4.3.3 follow_executor.py
- profile: chrome_profile_follow
- 入力: seed_users.json
- フロー: seed の `/followers` ページ → フォローボタン click → 検証
- DOM セレクタ: `span.follow.icon-follow:not(.ng-hide)`
- **既存 follow_host_runner.py を移植** (動作実績あり)

#### 4.3.4 followback_executor.py
- profile: chrome_profile_followback
- 入力: 自分のフォロワーリスト (まだフォロー返ししていない)
- フロー: `/discover/followers` → フォローバック click
- DOM セレクタ: 既存 followback_executor.py から移植

### 4.4 共通モジュール (shared_logic.py)

```python
class HeartbeatPusher:
    """30秒スロットル + HOST webhook push."""
    def write(self, phase, success=0, fail=0, extra={}, force=False): ...

class RateLimitDetector:
    """rate_limit 検知 (DOM ベース・色判定不要)."""
    def is_rate_limited(self, page) -> bool:
        # `[data-rate-limit-modal]` or text "ご利用上限数に達しています"
        return page.locator("text=ご利用上限").count() > 0

class FailReasonClassifier:
    """fail_reason taxonomy (Phase C-1 流用)."""
    def classify(self, exception, context) -> str: ...

class SessionLogger:
    """session log を logs/<mode>_session.log に append."""
```

---

## 5. HTTP server (VM 側) 設計

### 5.1 endpoints

```python
# VM 内 server (FastAPI)
GET  /                  # ヘルスチェック ("ok")
GET  /status            # 現在の bot 稼働状態
POST /run               # bot 起動 trigger ({"mode":"post","limit":50,"batch":1})
POST /abort             # 緊急停止 ({"mode":"post"} or {"all":true})
GET  /screenshot        # VM 画面 screenshot (debug 用)
GET  /heartbeat/<mode>  # 各 mode の最新 heartbeat
GET  /logs/<mode>/<n>   # 最新 N 件の log
```

### 5.2 認証

- HOST → VM: 簡易 token (env var `BOT_API_TOKEN`)
- 楽天 API は当然認証必要 (Chrome cookie で持続)

### 5.3 起動方式

```python
# server\http_server.py
from fastapi import FastAPI
import subprocess

app = FastAPI()

@app.post("/run")
async def run(payload: dict):
    mode = payload["mode"]
    limit = payload.get("limit", 100)
    # subprocess で runner を起動 (非同期)
    proc = subprocess.Popen([
        "python",
        r"C:\Users\cyber\Desktop\rakuten_room_bot\runner\rakuten_room_runner.py",
        "--mode", mode,
        "--limit", str(limit),
    ])
    return {"status": "launched", "pid": proc.pid}

@app.get("/status")
async def status():
    # heartbeat ファイルから現状返却
    ...

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
```

---

## 6. HOST 側 (monitoring 専用) 設計

### 6.1 簡略化されたコード base

| ファイル | 削除 / 残す |
|---------|------------|
| ops/vm_follow_launcher.py | 🗑️ 削除 (HTTP webhook で置換) |
| ops/follow_host_runner.py | 🗑️ 削除 (VM 内に移植済) |
| rakuten-room/bot/executor/follow_rpa_vm.py | 🗑️ archive (pyautogui 廃止) |
| rakuten-room/bot/executor/follow_executor.py | 🗑️ archive (HOST 上 Playwright 廃止) |
| rakuten-room/bot/executor/like_executor.py | 🗑️ archive (VM 内に移植) |
| rakuten-room/bot/executor/queue_executor.py (POST) | 🗑️ archive |
| rakuten-room/bot/executor/followback_executor.py | 🗑️ archive |
| ops/patrol_hourly.py | ✅ 残す (大幅 refactor) |
| ops/notifications/dashboard_report.py | ✅ 残す (HTTP 経由で VM status 取得) |
| ops/scheduler/orchestrator_v5.py | ✏️ 大幅 refactor (subprocess → HTTP) |

### 6.2 新規 vm_controller.py (HOST 側)

```python
# ops/vm_controller.py (新規)
"""HOST から VM の HTTP server に commands を送る。"""
import requests

VM_API = "http://localhost:18765"  # port forward

class VMClient:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}

    def is_alive(self) -> bool:
        try:
            r = requests.get(f"{VM_API}/", timeout=3, headers=self.headers)
            return r.status_code == 200
        except: return False

    def run(self, mode: str, limit: int = 100, batch: int = 1):
        return requests.post(f"{VM_API}/run", json={
            "mode": mode, "limit": limit, "batch": batch,
        }, headers=self.headers).json()

    def status(self):
        return requests.get(f"{VM_API}/status", headers=self.headers).json()
```

### 6.3 Task Scheduler の refactor

| Task | 旧 (subprocess) | 新 (HTTP) |
|------|----------------|-----------|
| RoomBot_POST_Batch1 | `python run.py auto --batch 1` | `python ops/vm_controller.py --mode post --batch 1` |
| RoomBot_LIKE_Hourly | `python orchestrator_v5.py --action like` | `python ops/vm_controller.py --mode like --limit 100` |
| RoomBot_FOLLOWBACK_Hourly | 同上 | `python ops/vm_controller.py --mode followback --limit 30` |
| RoomBotFollow_Hourly | `python ops/vm_follow_launcher.py` | `python ops/vm_controller.py --mode follow --limit 200` |

### 6.4 patrol_hourly.py の refactor (詳細は別文書 patrol_enhancement_master_plan.md)

```python
# 旧: 各種 file/DB を分散読み込み
# 新: VM HTTP server / HOST DB を一括 query
def main():
    vm = VMClient(token)
    if not vm.is_alive():
        recover_vm()
        return

    status = vm.status()  # HTTP 1 call で全機能の現状取得
    for mode in ["post", "like", "follow", "followback"]:
        check_health(mode, status[mode])
```

---

## 7. データフロー (定常運用時)

```
[Task Scheduler 17:00]
   ↓
[HOST] vm_controller.py --mode follow
   ↓ HTTP POST /run
[VM] http_server.py が rakuten_room_runner.py を subprocess.Popen で起動
   ↓
[VM] runner が Chrome (chrome_profile_follow) を起動
   ↓ Playwright で 楽天ROOM 操作
[VM] 30秒ごとに heartbeat
   ↓ HTTP POST :18766/heartbeat (HOST 側 receiver へ push)
[HOST] receiver が DB に書込
   ↓
[HOST patrol] 15分ごとに DB query で全機能の現状把握
   ↓
[HOST patrol] 異常検知 → vm_controller.run("recovery") 等
```

---

## 8. 移行スケジュール (現運用 stop なしで段階移行)

### Phase 1: VM 内環境構築 (3-5日)
- VM 内に Python + Playwright インストール
- chrome_profile を HOST → VM にコピー (4 profile)
- HTTP server を VM 内で起動・接続テスト
- HOST 側 vm_controller.py 実装
- **HOST 上 bot は引き続き稼働** (VM 内は dry-run)

### Phase 2: 機能ごとに VM へ移行 (1機能 / 1-2日)

優先順位:
1. **POST**: HOST → VM (1日) — 失敗しても CEO 業務影響少
2. **FOLLOWBACK**: HOST → VM (1日)
3. **LIKE**: HOST → VM (2日) — 一番件数多い
4. **FOLLOW**: pyautogui → Playwright on VM (3日) — 最大改造

### Phase 3: HOST 側コード archive (1日)
- 旧 executor ファイルを `archive/v5_legacy/` へ移動
- import エラー出ないか確認

### Phase 4: 最終検証 (1週間)
- 1週間連続稼働
- HOST Chrome を CEO が普通に使える状態を維持
- 4機能とも目標達成 (スプシ目標 vs 実績)

**合計**: 3-4週間

---

## 9. リスクと対策

### 9.1 VM 故障時の影響

| リスク | 対策 |
|--------|------|
| VM crash | watchdog (ops/vm_health_check.py 毎15分) で自動再起動 |
| HTTP server crash | VM 内 auto_relaunch_watcher.py で 60秒以内に再起動 |
| Chrome crash | runner が Playwright reconnect or new context |
| shared folder 切断 | webhook 経由なので影響なし (debug 用のみ) |
| profile 破損 | 日次 backup (chrome_profile_<mode>_backup_YYYYMMDD) |

### 9.2 楽天 API の rate limit

| ケース | 対策 |
|--------|------|
| FOLLOW 833件/日 上限 | rate_limit_detector が DOM 検知 → 69分待機 |
| 同時実行 (POST+LIKE+FB+FOLLOW) | 4 profile 独立なので Cookie 干渉なし。ただし楽天サーバー側の IP rate は VM 1台で集約 → 必要なら時間 ずらし |
| login 失効 | check_login_status() で起動時検知 → CEO Slack 通知 |

### 9.3 移行時のリスク

| リスク | 対策 |
|--------|------|
| 移行中 LIKE が止まる | Phase 2 で 1機能ずつ移行・他は HOST 稼働継続 |
| Cookie 引継ぎ失敗 | Phase A-2 と同じ robocopy で 4 profile を VM にコピー |
| DOM セレクタ古い | 移行時に楽天 ROOM 最新 UI で動作確認 |

---

## 10. 期待効果

### 10.1 定量指標

| 指標 | 現行 | 新設計 |
|------|------|-------|
| HOST Chrome 占有時間 | 6-12h/日 | **0時間/日** ✅ |
| pyautogui 関連バグ | 3-5件/週 | **0件** ✅ |
| follow 失敗率 | 11% | <5% (DOM ベース) |
| 同時実行能力 | 1-2機能 | 4機能 同時 |
| HOST PC リソース消費 | Chrome 4個 + Python 4個 | monitoring のみ |
| 移行後の保守工数 | 14 commit/日 (5/5実績) | 1-2 commit/週 |

### 10.2 CEO 業務への影響

- ✅ メインPC Chrome を CEO が完全占有可能
- ✅ HOST PC のリソース余裕 (現状 24GB 使用 → monitoring のみで 8GB 程度に減)
- ✅ bot 動作と CEO 操作が完全分離

---

## 11. 移行後の運用ルール

### 11.1 開発時

- VM 内 runner コードの修正は **VM 内で git pull** (or shared folder 経由)
- HOST 側 monitoring コードの修正は HOST 上で
- VM 内 Python パッケージ更新は requirements.txt で管理

### 11.2 障害時

```
1. patrol が VM HTTP server から no response 受信
2. HOST が VM 状態確認 (VBoxManage showvminfo)
3a. VM running なら: server 再起動 trigger
3b. VM 停止なら: VBoxManage startvm + auto-login + server 自動起動
4. 再接続できなければ CEO Slack alert
```

### 11.3 セキュリティ

- VM の cyber アカウントは外部からアクセス不可 (NAT)
- HTTP server token は env var で管理 (.env / repo に書かない)
- credentials/ は VM 内のみ (HOST との shared folder には置かない)

---

## 12. 関連設計書 / 整合性

### 旧設計書との関係

| 旧設計書 | 本案との関係 |
|---------|-------------|
| docs/phase_c7_playwright_unification_plan.md (HOST 統一) | 🗑️ **本案で上書き** (HOST → VM 統一に方向転換) |
| docs/phase_c8_sqlite_ssot_plan.md (SQLite SSOT) | ✅ 本案でも採用 (VM 内 room_bot_v6.db) |
| docs/phase_c9_vm_webhook_plan.md (VM webhook) | ✅ 本案で全面採用・拡張 |
| docs/vm_chrome_relogin_runbook.md | ✅ 4 profile 用に拡張 (post/like/fb/follow) |
| memory/rakuten_machine_split.md (VB=Follow / メイン=他) | 🗑️ **本案で deprecated** (VM=4機能全部) |
| memory/launcher_cmd_foreground_rule.md (pyautogui ルール) | 🗑️ pyautogui 廃止により不要 |

### 新設計の前提

- スプシ SSOT (memory/rakuten_room_targets_ssot.md) は **継続使用**
- 第2アカウント禁止 (memory/rakuten_follow_account_rule.md) は **継続遵守**

---

## 13. 完了基準 (Definition of Done)

1. ✅ メインPC で CEO が Chrome を 24時間使用可能
2. ✅ VM 内で 4機能とも Playwright DOM ベースで動作
3. ✅ HOST PC は monitoring + scheduling 専用 (bot 実行なし)
4. ✅ 1週間連続稼働で zero downtime
5. ✅ スプシ目標達成 (FOLLOW 1095/日 等) 7日連続
6. ✅ HOST Chrome 占有時間 0時間/日
7. ✅ docs/patrol_enhancement_master_plan.md (別文書) と連動した強化 patrol が稼働

---

## 14. 次のステップ

本設計書承認後、以下の順で実装着手:
1. 別途作成: `docs/patrol_enhancement_master_plan.md` (パトロール強化詳細)
2. Plan v3 → Plan v4 への改訂 (P1 を「VM 完結化」に書き換え)
3. Phase 1 (VM 環境構築) 着手
