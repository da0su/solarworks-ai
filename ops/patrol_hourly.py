#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""毎時30分・4機能統合パトロール（CEO指示 2026-04-23 11:57）:

監視対象4機能:
  1. follow     (VM専用 / follow_rpa_log.json + VM screenshot)
               ※ HOST(メインPC)フォローは 2026-04-30 CEO指示で停止。VMのみ。
  2. post       (メインPC / room_bot.db post_queue + daily_summary)
  3. like       (メインPC / room_bot_v5.db like_log + like_history.json)
  4. followback (VB機同居 / room_bot_v5.db followback_queue + follow_log action='followback')

各機能の判定:
  - follow:      log_age>180min(VM専用・3h間隔) or delta=0 AND age>120min → problem
  - post:        (今日posted=0 AND JST時刻>=08:00) → problem (scheduler停止疑い)
  - like:        (今日liked=0 AND JST時刻>=15:00) → problem
  - followback:  operational (152件/日実績、2026-05-05 Phase B-3 で integrated)。今日0件 AND >=19:00 → problem

Usage:
    python ops/patrol_hourly.py           # 観測のみ
    python ops/patrol_hourly.py --recover # 異常検出時に自動復旧（follow のみ対応）
"""
from __future__ import annotations
import argparse
import io
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(r"C:\Users\infoa\Documents\solarworks-ai")
VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "RoomBot"
LOG_FOLLOW = ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
LOG_FOLLOW_HOST = ROOT / "rakuten-room" / "bot" / "executor" / "follow_host_log.json"
SS_DIR = ROOT / "ops" / "patrol_screenshots"
PATROL_STATE = ROOT / "ops" / "_patrol_state.json"
PATROL_LOG = ROOT / "ops" / "patrol_log.txt"

# 2026-05-05 Phase 2-2: heartbeat 同期 (VM→HOST)
# VirtualBox shared folder 'share' は rakuten-room/bot/executor にマップ:
#   `VBoxManage list -l vms`で確認: Host path C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\executor
SHARE_DIR = ROOT / "rakuten-room" / "bot" / "executor"
HEARTBEAT_SHARE = SHARE_DIR / "follow_heartbeat.json"
LOGIN_EXPIRED_FLAG = SHARE_DIR / "login_expired_flag.json"
HEARTBEAT_STALE_SEC = 180   # 180秒（3分）heartbeat 更新がなければ stuck と判定

DATA_DIR = ROOT / "rakuten-room" / "bot" / "data"
DB_LEGACY = DATA_DIR / "room_bot.db"
DB_V5 = DATA_DIR / "room_bot_v5.db"
LIKE_HISTORY = DATA_DIR / "like_history.json"
HEARTBEAT = DATA_DIR / "state" / "heartbeat.json"


# 2026-05-05 礎: Windows console subprocess の cmd window flash 抑制
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def run(*args, timeout=30):
    try:
        r = subprocess.run(list(args), capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def take_screenshot(tag: str) -> Path | None:
    SS_DIR.mkdir(parents=True, exist_ok=True)
    path = SS_DIR / f"_patrol_{tag}.png"
    rc, out = run(VBOXMANAGE, "controlvm", VM_NAME, "screenshotpng", str(path))
    if rc == 0 and path.exists() and path.stat().st_size > 0:
        return path
    return None


def vm_running() -> bool:
    rc, out = run(VBOXMANAGE, "list", "runningvms")
    return VM_NAME in out


def load_state() -> dict:
    if PATROL_STATE.exists():
        try:
            return json.loads(PATROL_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    tmp = PATROL_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATROL_STATE)


def append_patrol_log(line: str):
    PATROL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PATROL_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================
# 機能別観測
# ============================================================

def check_follow() -> dict:
    """follow (VM専用・2026-04-30〜 HOST停止) 観測"""
    info: dict = {"function": "follow", "machine": "VM", "problem": False, "reasons": []}
    info["vm_running"] = vm_running()

    # VM log のみ使用（HOST runner は 2026-04-30 CEO指示で無効化）
    if not LOG_FOLLOW.exists():
        info["log_exists"] = False
        info["problem"] = True
        info["reasons"].append("log_missing")
        return info

    data: list = []
    try:
        data = json.loads(LOG_FOLLOW.read_text(encoding="utf-8"))
    except Exception:
        pass
    info["log_entries"] = len(data)

    # mtime = VM log のみ
    mtime = datetime.fromtimestamp(LOG_FOLLOW.stat().st_mtime)
    age_min = (datetime.now() - mtime).total_seconds() / 60
    info["log_age_min"] = round(age_min, 1)

    if data:
        # Sort by timestamp to find the latest entry
        data_sorted = sorted(data, key=lambda e: e.get("timestamp", ""), reverse=True)
        last = data_sorted[0]
        info["last_entry"] = {
            "ts": last.get("timestamp", "?"),
            "success": last.get("success", "?"),
            "stop_reason": last.get("stop_reason", "?"),
        }

    # 直近12h集計
    cutoff = datetime.now() - timedelta(hours=12)
    runs = 0
    total = 0
    reasons: dict[str, int] = {}
    fail_aggregate: dict[str, int] = {}  # Phase C-1: fail_reason 集計
    for e in data:
        try:
            dt = datetime.fromisoformat(str(e.get("timestamp", "")).replace("Z", ""))
        except Exception:
            continue
        if dt < cutoff:
            continue
        runs += 1
        total += int(e.get("success", 0) or 0)
        r = e.get("stop_reason", "unknown")
        reasons[r] = reasons.get(r, 0) + 1
        # Phase C-1: fail_stats を group別 SLO 違反検知用に集約
        for fr, fc in (e.get("fail_stats") or {}).items():
            if not isinstance(fc, int):
                continue
            fail_aggregate[fr] = fail_aggregate.get(fr, 0) + fc
    info["last_12h"] = {"runs": runs, "success_total": total, "stop_reasons": reasons}

    # Phase C-1: fail_reason taxonomy で group SLO 違反検知
    try:
        sys.path.insert(0, str(ROOT))
        from shared.fail_reason_taxonomy import aggregate_by_group, evaluate_slo
        if fail_aggregate:
            info["fail_by_group"] = aggregate_by_group(fail_aggregate)
            slo_violations = evaluate_slo(fail_aggregate, duration_hours=12.0)
            if slo_violations:
                info["slo_violations"] = slo_violations
                # CRITICAL を problem 化
                critical_groups = [v["group"] for v in slo_violations if v["level"] == "CRITICAL"]
                if critical_groups:
                    info["problem"] = True
                    info["reasons"].append(f"slo_critical:{','.join(critical_groups)}")
    except Exception as _e:
        info["taxonomy_error"] = str(_e)

    if not info["vm_running"]:
        info["problem"] = True
        info["reasons"].append("vm_not_running")

    # 2026-05-25 Plan v6 HOST follow 対応:
    # follow_via_seeds.py (HOST) が稼働中なら VM log stale check をスキップ。
    # follow_rate_state.json の mtime が 3h 以内 = HOST follow active と判断。
    _host_rate_state = ROOT / "state" / "follow_rate_state.json"
    _host_follow_active = False
    if _host_rate_state.exists():
        try:
            _host_age_min = (datetime.now() - datetime.fromtimestamp(
                _host_rate_state.stat().st_mtime)).total_seconds() / 60
            _host_follow_active = _host_age_min < 180
            info["host_follow_active"] = _host_follow_active
            info["host_rate_state_age_min"] = round(_host_age_min, 1)
        except Exception:
            pass

    # VM専用: 3時間(180min)以内に更新があれば正常（VM実行間隔 ~1-3h）
    # HOST follow が active の場合は VM log stale は誤報 → skip
    if not _host_follow_active and age_min > 180:
        info["problem"] = True
        info["reasons"].append(f"log_stale({age_min:.0f}min)")

    # 2026-05-05 Phase 2-2: heartbeat 検知（VM稼働中だがbot 進行不能を3分で検知）
    if HEARTBEAT_SHARE.exists():
        try:
            hb = json.loads(HEARTBEAT_SHARE.read_text(encoding="utf-8"))
            hb_ts = datetime.fromisoformat(hb.get("ts", ""))
            hb_age = (datetime.now() - hb_ts).total_seconds()
            info["heartbeat_age_sec"] = round(hb_age, 1)
            info["heartbeat_phase"] = hb.get("phase", "?")
            info["heartbeat_seed"] = hb.get("current_seed", "")
            info["heartbeat_success"] = hb.get("success_count", 0)
            # VM稼働中 かつ heartbeat が古い → bot stuck
            if info["vm_running"] and hb_age > HEARTBEAT_STALE_SEC:
                # shutdown phase なら正常終了済み
                if hb.get("phase") not in ("shutdown",):
                    info["problem"] = True
                    info["reasons"].append(f"vm_internal_stuck(hb_age={hb_age:.0f}s phase={hb.get('phase')})")
        except Exception as e:
            info["heartbeat_age_sec"] = None
            info["heartbeat_error"] = str(e)[:80]
    else:
        info["heartbeat_age_sec"] = None

    # 2026-05-05 Phase 2-1: login_expired_flag 検知
    if LOGIN_EXPIRED_FLAG.exists():
        try:
            flag = json.loads(LOGIN_EXPIRED_FLAG.read_text(encoding="utf-8"))
            flag_ts = datetime.fromisoformat(flag.get("ts", ""))
            flag_age_min = (datetime.now() - flag_ts).total_seconds() / 60
            # 60分以内のフラグは有効
            if flag_age_min < 60:
                info["problem"] = True
                info["reasons"].append("login_expired")
                info["login_expired_detail"] = flag.get("detail", "")
        except Exception:
            pass
    return info


def _sqlite_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    try:
        # uri=True で readonly 接続
        uri = f"file:{db_path.as_posix()}?mode=ro"
        c = sqlite3.connect(uri, uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def check_post() -> dict:
    """post (メインPC) 観測 — room_bot.db 優先、v5 を副次"""
    info: dict = {"function": "post", "machine": "main", "problem": False, "reasons": []}
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 1. legacy DB から today_posted + last_posted_ts
    c = _sqlite_readonly(DB_LEGACY)
    today_posted = 0
    last_posted = None
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM post_queue "
                "WHERE status='posted' AND DATE(posted_at)=DATE('now','localtime')"
            ).fetchone()
            today_posted = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT posted_at FROM post_queue "
                "WHERE status='posted' ORDER BY posted_at DESC LIMIT 1"
            ).fetchone()
            if r:
                last_posted = r[0]
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT planned, posted, failed, skipped FROM daily_summary "
                "WHERE summary_date=? LIMIT 1",
                (today,),
            ).fetchone()
            if r:
                info["today_plan"] = dict(r)
        except Exception:
            pass
        c.close()

    info["today_posted"] = today_posted
    info["last_posted_at"] = last_posted

    # 2. last_posted からの経過日数
    if last_posted:
        try:
            dt = datetime.fromisoformat(last_posted.replace("Z", ""))
            info["last_posted_age_days"] = round((now - dt).total_seconds() / 86400, 1)
        except Exception:
            info["last_posted_age_days"] = None

    # 3. scheduler heartbeat 鮮度
    if HEARTBEAT.exists():
        try:
            hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
            hb_dt = datetime.fromisoformat(hb.get("updated_at", "").replace("Z", ""))
            age_min = (now - hb_dt).total_seconds() / 60
            info["heartbeat_age_min"] = round(age_min, 1)
            info["heartbeat_job"] = hb.get("current_job", "?")
        except Exception:
            info["heartbeat_age_min"] = None

    # 4. 判定: JST 08:00 以降で today_posted==0 → problem
    if now.hour >= 8 and today_posted == 0:
        info["problem"] = True
        info["reasons"].append("no_post_today_after_0800")
    # 追加: last_posted が 2日以上前なら警告
    if info.get("last_posted_age_days") is not None and info["last_posted_age_days"] >= 2:
        info["problem"] = True
        info["reasons"].append(f"last_post_{info['last_posted_age_days']:.0f}d_ago")

    # 5. Phase B-1: 商品プール件数監視（POOL_MIN=700 を割ったら警告、200未満で critical）
    try:
        source_items = ROOT / "rakuten-room" / "bot" / "data" / "source_items.json"
        if source_items.exists():
            pool_data = json.loads(source_items.read_text(encoding="utf-8"))
            pool_count = len(pool_data) if isinstance(pool_data, list) else 0
            info["pool_count"] = pool_count
            # POOL_MIN=700 (config.py)。CRITICAL=200未満、WARN=700未満
            if pool_count < 200:
                info["problem"] = True
                info["reasons"].append(f"pool_critical({pool_count}<200)")
            elif pool_count < 700:
                info["problem"] = True
                info["reasons"].append(f"pool_low({pool_count}<700)")
    except Exception as e:
        info["pool_count_error"] = str(e)

    # 6. 2026-05-26: post_queue (queued) 件数監視
    # daily_task_reset 06:00 で generate_today_post_plan() が動かなかった場合
    # post_queue に当日 queued が無く、Batch1 9:00 で 0件達成になる
    # → 7:00 以降に queued=0 を検出したら problem 上げて auto recovery
    info["queued_count"] = None
    c = _sqlite_readonly(DB_LEGACY)
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM post_queue WHERE status='queued'"
            ).fetchone()
            info["queued_count"] = int(r[0]) if r else 0
        except Exception:
            pass
        c.close()
    # 判定: 07:00 以降 〜 09:00 (Batch1 前) で queued=0 → CRITICAL
    if info["queued_count"] == 0 and 7 <= now.hour < 9:
        info["problem"] = True
        info["reasons"].append("post_queue_empty_before_batch1")
        info["auto_recover"] = "regen_post_plan"
    elif info["queued_count"] is not None and info["queued_count"] < 5 and now.hour >= 7:
        info["problem"] = True
        info["reasons"].append(f"post_queue_low({info['queued_count']}<5)")

    return info


def check_like() -> dict:
    """like (メインPC) 観測 — room_bot_v5.db like_log + like_history.json fallback"""
    info: dict = {"function": "like", "machine": "main", "problem": False, "reasons": []}
    now = datetime.now()

    today_liked = 0
    last_liked = None

    # 1. v5 like_log
    c = _sqlite_readonly(DB_V5)
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM like_log "
                "WHERE DATE(liked_at)=DATE('now','localtime')"
            ).fetchone()
            today_liked = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute("SELECT liked_at FROM like_log ORDER BY liked_at DESC LIMIT 1").fetchone()
            if r:
                last_liked = r[0]
        except Exception:
            pass
        c.close()

    # 2. like_history.json fallback
    if LIKE_HISTORY.exists():
        try:
            data = json.loads(LIKE_HISTORY.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                info["history_entries"] = len(data)
                last_h = data[-1]
                if not last_liked:
                    last_liked = last_h.get("liked_at")
                # today 件数の json fallback
                if today_liked == 0:
                    today_str = now.strftime("%Y-%m-%d")
                    today_liked = sum(
                        1 for e in data if str(e.get("liked_at", "")).startswith(today_str)
                    )
        except Exception:
            pass

    info["today_liked"] = today_liked
    info["last_liked_at"] = last_liked

    if last_liked:
        try:
            dt = datetime.fromisoformat(str(last_liked).replace("Z", ""))
            info["last_liked_age_days"] = round((now - dt).total_seconds() / 86400, 1)
        except Exception:
            pass

    # 判定: JST 15:00 以降で today_liked==0 → problem
    if now.hour >= 15 and today_liked == 0:
        info["problem"] = True
        info["reasons"].append("no_like_today_after_1500")
    if info.get("last_liked_age_days") is not None and info["last_liked_age_days"] >= 2:
        info["problem"] = True
        info["reasons"].append(f"last_like_{info['last_liked_age_days']:.0f}d_ago")
    return info


def check_followback() -> dict:
    """followback (VB機同居予定) 観測 — room_bot_v5.db followback_queue + follow_log action='followback'"""
    info: dict = {"function": "followback", "machine": "VB", "problem": False, "reasons": []}
    now = datetime.now()

    pending = 0
    today_fb = 0
    last_fb = None
    table_exists = False

    c = _sqlite_readonly(DB_V5)
    if c is not None:
        try:
            r = c.execute("SELECT COUNT(*) FROM followback_queue WHERE status='pending'").fetchone()
            pending = int(r[0]) if r else 0
            table_exists = True
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM follow_log "
                "WHERE action='followback' AND DATE(followed_at)=DATE('now','localtime')"
            ).fetchone()
            today_fb = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT followed_at FROM follow_log "
                "WHERE action='followback' ORDER BY followed_at DESC LIMIT 1"
            ).fetchone()
            if r:
                last_fb = r[0]
        except Exception:
            pass
        c.close()

    info["pending_count"] = pending
    info["today_followback"] = today_fb
    info["last_followback_at"] = last_fb

    # 2026-05-05 Phase B-3: followback は operational 確定（152件/日の運用実績）
    # 旧 pending_impl 判定は削除。pending=0 でも operational 扱いとする
    info["status"] = "operational"
    # 判定1: JST 19:00 以降で today_fb==0 AND pending>0 → problem
    if now.hour >= 19 and today_fb == 0 and pending > 0:
        info["problem"] = True
        info["reasons"].append(f"pending_{pending}_not_processed_after_1900")
    # 判定2: last_fb から3日以上 → problem (followback 機能が止まっている可能性)
    if last_fb:
        try:
            dt = datetime.fromisoformat(str(last_fb).replace("Z", ""))
            age_days = (now - dt).total_seconds() / 86400
            if age_days >= 3.0:
                info["problem"] = True
                info["reasons"].append(f"last_followback_{age_days:.1f}d_ago")
        except Exception:
            pass
    return info


# ============================================================
# VM 自動復旧 + Slack通知
# ============================================================

SLACK_REPORTER = ROOT / "ops" / "notifications" / "slack_reporter.py"
VM_ALERT_THROTTLE_SEC = 7200   # 同一アラートを2h以内に連打しない
VM_RECOVER_THROTTLE_SEC = 600  # startvm を10min以内に連打しない
VM_BOOT_WAIT_SEC = 90          # startvm後、launcherを起動するまでの待機時間


def _vm_auto_recover(state: dict, stamp: str, now: datetime) -> None:
    """VM(RoomBot)停止検知時: Slack通知(2hスロットル) + VBoxManage startvm + 遅延後launcher起動"""

    # 1. Slack通知（2hスロットル）
    last_alert = state.get("last_vm_alert")
    alert_needed = True
    if last_alert:
        try:
            if (now - datetime.fromisoformat(last_alert)).total_seconds() < VM_ALERT_THROTTLE_SEC:
                alert_needed = False
        except Exception:
            pass

    if alert_needed:
        msg = (
            f"<!here> 【パトロール自動検知】VM(RoomBot)が停止しています ({stamp})\n"
            f"自動復旧(startvm)を実行します。follow再開まで最大2分かかります。\n"
            f"復旧失敗時は手動でVMを起動してください。"
        )
        rc_slack, _ = run(sys.executable, str(SLACK_REPORTER), msg, timeout=30)
        state["last_vm_alert"] = stamp
        append_patrol_log(f"[VM-ALERT] Slack通知送信 rc={rc_slack}")

    # 2. startvm連打防止（10minスロットル）
    last_recovery = state.get("last_vm_recovery")
    if last_recovery:
        try:
            elapsed = (now - datetime.fromisoformat(last_recovery)).total_seconds()
            if elapsed < VM_RECOVER_THROTTLE_SEC:
                append_patrol_log(
                    f"[VM-RECOVER] スキップ: 前回復旧から{elapsed:.0f}s — 次の起動試行まで待機"
                )
                return
        except Exception:
            pass

    # 3. VBoxManage startvm
    rc_start, out_start = run(VBOXMANAGE, "startvm", VM_NAME, timeout=60)
    state["last_vm_recovery"] = stamp
    log_line = f"[VM-RECOVER] startvm rc={rc_start}: {out_start[:120].strip()}"
    append_patrol_log(log_line)
    print(log_line)

    if rc_start != 0 and "already locked" not in out_start.lower():
        append_patrol_log("[VM-RECOVER] startvm失敗 — 手動対応が必要です")
        err_msg = (
            f"<!here> 【パトロール】VM自動起動が失敗しました ({stamp})\n"
            f"rc={rc_start}: {out_start[:200].strip()}\n手動でVMを起動してください。"
        )
        run(sys.executable, str(SLACK_REPORTER), err_msg, timeout=30)
        return

    # 4. VM起動待機後 → follow launcher を非同期起動
    append_patrol_log(f"[VM-RECOVER] VM起動待機中 ({VM_BOOT_WAIT_SEC}s)...")
    time.sleep(VM_BOOT_WAIT_SEC)

    launcher = ROOT / "ops" / "vm_follow_launcher.py"
    try:
        subprocess.Popen(
            [sys.executable, str(launcher), "--limit", "100"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW,
        )
        append_patrol_log("[VM-RECOVER] launcher起動完了 (非同期・no_window)")
    except Exception as e:
        append_patrol_log(f"[VM-RECOVER] launcher起動失敗: {e}")


# ============================================================
# SSOT — 4機能の現状を統合JSON出力（patrol が唯一の書込側）
# ============================================================

SSOT_PATH = ROOT / "state" / "follow_runtime_state.json"


def _write_ssot_state(stamp: str, follow_info: dict, post_info: dict, like_info: dict, fb_info: dict, patrol_state: dict) -> None:
    """4機能の現状を state/follow_runtime_state.json に atomic write。

    用途: 下流ツール（CEOダッシュボード・slack_reporter等）はこのファイル1つだけ読めばよい。
    既存の heartbeat / follow_rpa_state / _patrol_state はVM bot側 / patrol側のローカル更新源として温存。
    """
    payload = {
        "schema_version": 1,
        "updated_at": stamp,
        "follow": {
            "vm_running": follow_info.get("vm_running"),
            "login_status": "expired" if "login_expired" in follow_info.get("reasons", []) else "ok",
            "heartbeat_age_sec": follow_info.get("heartbeat_age_sec"),
            "heartbeat_phase": follow_info.get("heartbeat_phase"),
            "heartbeat_seed": follow_info.get("heartbeat_seed"),
            "log_age_min": follow_info.get("log_age_min"),
            "last_12h": follow_info.get("last_12h"),
            "last_entry": follow_info.get("last_entry"),
            "delta_vs_last_patrol": follow_info.get("delta_vs_last_patrol"),
            "problem": follow_info.get("problem"),
            "reasons": follow_info.get("reasons"),
        },
        "post": {
            "today_posted": post_info.get("today_posted"),
            "last_posted_at": post_info.get("last_posted_at"),
            "last_posted_age_days": post_info.get("last_posted_age_days"),
            "heartbeat_age_min": post_info.get("heartbeat_age_min"),
            "heartbeat_job": post_info.get("heartbeat_job"),
            "today_plan": post_info.get("today_plan"),
            "problem": post_info.get("problem"),
            "reasons": post_info.get("reasons"),
        },
        "like": {
            "today_liked": like_info.get("today_liked"),
            "last_liked_at": like_info.get("last_liked_at"),
            "last_liked_age_days": like_info.get("last_liked_age_days"),
            "history_entries": like_info.get("history_entries"),
            "problem": like_info.get("problem"),
            "reasons": like_info.get("reasons"),
        },
        "followback": {
            "status": fb_info.get("status"),
            "pending_count": fb_info.get("pending_count"),
            "today_followback": fb_info.get("today_followback"),
            "last_followback_at": fb_info.get("last_followback_at"),
            "problem": fb_info.get("problem"),
            "reasons": fb_info.get("reasons"),
        },
        "patrol_meta": {
            "any_problem": patrol_state.get("any_problem"),
            "last_vm_alert": patrol_state.get("last_vm_alert"),
            "last_vm_recovery": patrol_state.get("last_vm_recovery"),
            "last_login_alert": patrol_state.get("last_login_alert"),
            "last_stuck_recover": patrol_state.get("last_stuck_recover"),
        },
    }
    SSOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SSOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(SSOT_PATH)


# ============================================================
# main
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recover", action="store_true", help="異常時に follow 自動復旧")
    ap.add_argument("--json", action="store_true", help="JSON形式で出力")
    args = ap.parse_args()

    now = datetime.now()
    tag = now.strftime("%H%M")
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")

    # 4機能観測
    follow_info = check_follow()
    post_info = check_post()
    like_info = check_like()
    fb_info = check_followback()

    # follow 用 screenshot（既存機能）
    ss_path = take_screenshot(tag) if follow_info["vm_running"] else None

    # 状態比較（follow 前回比）
    state = load_state()
    prev = state.get("follow_last_success_total")
    delta = None
    if prev is not None:
        delta = follow_info.get("last_12h", {}).get("success_total", 0) - int(prev)
    follow_info["delta_vs_last_patrol"] = delta

    # follow 追加判定: delta=0 AND age>120min → problem (VM専用・実行間隔 ~1-3h)
    # 2026-05-25: HOST follow (follow_via_seeds.py) が active な場合はスキップ
    if (not follow_info.get("host_follow_active", False)
        and delta is not None and delta == 0
        and follow_info.get("log_age_min", 0) > 120
        and "log_stale" not in "|".join(follow_info["reasons"])):
        follow_info["problem"] = True
        follow_info["reasons"].append("no_progress_since_last_patrol")

    # state 更新
    state.update({
        "last_patrol": stamp,
        "follow_last_success_total": follow_info.get("last_12h", {}).get("success_total", 0),
        "follow_last_age_min": follow_info.get("log_age_min"),
        "post_today_posted": post_info.get("today_posted"),
        "like_today_liked": like_info.get("today_liked"),
        "followback_today": fb_info.get("today_followback"),
        "followback_status": fb_info.get("status", "?"),
        "any_problem": any(x.get("problem") for x in [follow_info, post_info, like_info, fb_info]),
    })
    save_state(state)

    # 2026-05-05 Phase 2-5: SSOT — 4機能の現状を state/follow_runtime_state.json に統合出力
    _write_ssot_state(stamp, follow_info, post_info, like_info, fb_info, state)

    # VM停止時: 自動復旧 + Slack通知（--recover フラグ不要、常時有効）
    if "vm_not_running" in follow_info.get("reasons", []):
        _vm_auto_recover(state, stamp, now)
        save_state(state)  # last_vm_alert / last_vm_recovery を永続化

    # 2026-05-05 Phase 2-1: ログイン失効検知時のCEO通知（2hスロットル）
    if "login_expired" in follow_info.get("reasons", []):
        last_login_alert = state.get("last_login_alert")
        login_alert_needed = True
        if last_login_alert:
            try:
                if (now - datetime.fromisoformat(last_login_alert)).total_seconds() < 7200:
                    login_alert_needed = False
            except Exception:
                pass
        if login_alert_needed:
            detail = follow_info.get("login_expired_detail", "")
            msg = (
                f"<!channel> 【パトロール緊急】楽天ROOMログイン失効を検知 ({stamp})\n"
                f"VM ChromeのROOMセッションが切れています。CEO手動再ログインが必要です。\n"
                f"手順: docs/vm_chrome_relogin_runbook.md\n"
                f"詳細: {detail}"
            )
            run(sys.executable, str(SLACK_REPORTER), msg, timeout=30)
            state["last_login_alert"] = stamp
            append_patrol_log(f"[LOGIN-EXPIRED] CEO Slack通知送信")
            save_state(state)

    # 2026-05-05 Phase 2-2: VM稼働中stuck検知時の自動復旧（heartbeat 3分以上停止）
    if any(r.startswith("vm_internal_stuck") for r in follow_info.get("reasons", [])):
        last_stuck_recover = state.get("last_stuck_recover")
        stuck_recover_needed = True
        if last_stuck_recover:
            try:
                if (now - datetime.fromisoformat(last_stuck_recover)).total_seconds() < 1800:  # 30分スロットル
                    stuck_recover_needed = False
            except Exception:
                pass
        if stuck_recover_needed:
            msg = (
                f"<!here> 【パトロール】VM内bot stuck検知・自動復旧試行 ({stamp})\n"
                f"heartbeat age={follow_info.get('heartbeat_age_sec')}s phase={follow_info.get('heartbeat_phase')}\n"
                f"既存セッションをkill して launcher 再投入します。"
            )
            run(sys.executable, str(SLACK_REPORTER), msg, timeout=30)
            # vm_kill_all + launcher --force
            rc1, _ = run(sys.executable, str(ROOT / "ops" / "vm_kill_all.py"), timeout=120)
            append_patrol_log(f"[STUCK-RECOVER] vm_kill_all rc={rc1}")
            launcher = ROOT / "ops" / "vm_follow_launcher.py"
            try:
                subprocess.Popen(
                    [sys.executable, str(launcher), "--force", "--limit", "100"],
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW,
                )
                append_patrol_log("[STUCK-RECOVER] launcher 再投入完了 (no_window)")
            except Exception as e:
                append_patrol_log(f"[STUCK-RECOVER] launcher 起動失敗: {e}")
            state["last_stuck_recover"] = stamp
            save_state(state)

    # 出力
    report = {
        "stamp": stamp,
        "screenshot": str(ss_path) if ss_path else None,
        "follow": follow_info,
        "post": post_info,
        "like": like_info,
        "followback": fb_info,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        lines = [f"=== PATROL {stamp} (4-function) ==="]
        lines.append(f"Screenshot: {ss_path if ss_path else 'FAIL'}")
        lines.append("")
        lines.append(f"[FOLLOW  ] vm={follow_info['vm_running']} "
                     f"log_age={follow_info.get('log_age_min','?')}min "
                     f"hb_age={follow_info.get('heartbeat_age_sec','?')}s "
                     f"hb_phase={follow_info.get('heartbeat_phase','?')} "
                     f"entries={follow_info.get('log_entries','?')} "
                     f"delta={delta} "
                     f"12h_runs={follow_info.get('last_12h',{}).get('runs',0)}/"
                     f"success={follow_info.get('last_12h',{}).get('success_total',0)} "
                     f"problem={follow_info['problem']} {follow_info['reasons']}")
        lines.append(f"[POST    ] today_posted={post_info.get('today_posted',0)} "
                     f"last_posted_at={post_info.get('last_posted_at','?')} "
                     f"age_days={post_info.get('last_posted_age_days','?')} "
                     f"hb_age_min={post_info.get('heartbeat_age_min','?')} "
                     f"problem={post_info['problem']} {post_info['reasons']}")
        lines.append(f"[LIKE    ] today_liked={like_info.get('today_liked',0)} "
                     f"last_liked_at={like_info.get('last_liked_at','?')} "
                     f"age_days={like_info.get('last_liked_age_days','?')} "
                     f"history_entries={like_info.get('history_entries','?')} "
                     f"problem={like_info['problem']} {like_info['reasons']}")
        lines.append(f"[FOLLOWBK] status={fb_info.get('status','?')} "
                     f"pending={fb_info.get('pending_count',0)} "
                     f"today_fb={fb_info.get('today_followback',0)} "
                     f"last_fb_at={fb_info.get('last_followback_at','?')} "
                     f"problem={fb_info['problem']} {fb_info['reasons']}")
        lines.append("")
        lines.append(f"any_problem={state['any_problem']}")
        text = "\n".join(lines)
        print(text)
        append_patrol_log(text + "\n")

    # 自動復旧（follow のみ）
    if follow_info["problem"] and args.recover:
        print("AUTO-RECOVER follow: vm_kill_all + launcher --force")
        rc1, _ = run(sys.executable, str(ROOT / "ops" / "vm_kill_all.py"), timeout=120)
        print(f"vm_kill_all rc={rc1}")
        rc2, _ = run(sys.executable, str(ROOT / "ops" / "vm_follow_launcher.py"),
                     "--force", "--limit", "100", timeout=180)
        print(f"launcher rc={rc2}")

    # 2026-05-26: POST queue 空 auto recovery
    # Codex REJECT 反映:
    #   - --recover フラグでゲート (権限委譲一貫性)
    #   - 1日1回 lock で重複実行防止 (15分間隔で 7-9時 7回走るのを防ぐ)
    if post_info.get("auto_recover") == "regen_post_plan" and args.recover:
        today = datetime.now().strftime("%Y-%m-%d")
        regen_flag = ROOT / "state" / f"post_regen_{today}.done"
        if regen_flag.exists():
            print(f"AUTO-RECOVER post: skip (already regen today: {regen_flag.name})")
        else:
            print("AUTO-RECOVER post: post_queue empty → regen plan (1x/day)")
            run_py = ROOT / "rakuten-room" / "bot" / "run.py"
            rc = -1
            if run_py.exists():
                try:
                    r = subprocess.run(
                        [sys.executable, str(run_py), "plan"],
                        cwd=str(run_py.parent),
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=180, creationflags=_NO_WINDOW
                    )
                    rc = r.returncode
                    out = (r.stdout or "")[-200:]
                    print(f"plan rc={rc} tail={out}")
                except Exception as _pe:
                    print(f"plan exception: {_pe}")
                # 成功/失敗いずれも flag ファイル作成 (1日1回 lock)
                try:
                    regen_flag.parent.mkdir(parents=True, exist_ok=True)
                    regen_flag.write_text(f"rc={rc} ts={datetime.now().isoformat()}\n")
                except Exception:
                    pass
            # Slack alert
            try:
                slack = ROOT / "ops" / "notifications" / "slack_reporter.py"
                msg = (
                    f"<!channel> 【patrol_hourly AUTO-RECOVER】POST queue 空検出 "
                    f"→ regen rc={rc} (06:00 daily_reset が失敗していた可能性)"
                )
                run(sys.executable, str(slack), msg, timeout=30)
            except Exception as _se:
                print(f"slack alert error: {_se}")


if __name__ == "__main__":
    main()
