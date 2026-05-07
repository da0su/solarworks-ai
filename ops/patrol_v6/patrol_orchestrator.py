#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 統合オーケストレーター — 8 Layer + 自動復旧 + Slack escalation.

Plan v4 P2 (パトロール強化) の中核。

実行: python ops/patrol_v6/patrol_orchestrator.py [--check-only]

各 Layer は独立 module として実装:
- L0 environment.py
- L1 host.py
- L2 vm.py
- L3 chrome.py
- L4 process.py
- L5 rakuten.py
- L6 session.py
- L7 business.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


# ============================================================
# Layer 一覧
# ============================================================

LAYERS = [
    ("L0_env",     "environment", "Disk/Memory/Network"),
    ("L1_host",    "host",        "Task Scheduler/HOST CPU"),
    ("L2_vm",      "vm",          "VM running/RunLevel"),
    ("L3_chrome",  "chrome",      "4 profile health"),
    ("L4_process", "process",     "VM HTTP server alive"),
    ("L5_rakuten", "rakuten",     "login/rate_limit"),
    ("L6_session", "session",     "heartbeat staleness"),
    ("L7_biz",     "business",    "スプシ目標達成率"),
]


def run_layer(layer_name: str) -> dict:
    """Layer module の check() 関数を実行."""
    try:
        mod = __import__(f"ops.patrol_v6.{layer_name}", fromlist=["check"])
        return mod.check()
    except ImportError:
        return {"layer": layer_name, "status": "not_implemented", "alerts": []}
    except Exception as e:
        return {"layer": layer_name, "status": "error", "error": str(e), "alerts": []}


# ============================================================
# 自動復旧マトリクス
# ============================================================

def auto_recover(action: str, context: dict) -> dict:
    """alert に応じて自動復旧アクション実行."""
    result = {"action": action, "executed_at": datetime.now().isoformat()}
    try:
        if action == "vm_startvm":
            r = subprocess.run([
                r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe", "startvm", "RoomBot", "--type", "headless"
            ], capture_output=True, text=True, timeout=60, creationflags=NO_WIN)
            result["rc"] = r.returncode

        elif action == "vm_http_restart":
            # VM 内 http_server を再起動 (VBoxManage guestcontrol)
            # credentials がない場合は VM reset で代替
            result["note"] = "manual_restart_required"

        elif action == "session_abort":
            mode = context.get("mode")
            if mode:
                from ops.vm_v6.vm_controller import abort
                result["abort_result"] = abort(mode=mode)

        elif action == "cooldown_90min":
            # cooldown flag を file に作成
            cd_file = REPO_ROOT / "state" / f"cooldown_{context.get('mode', 'unknown')}.json"
            cd_file.parent.mkdir(parents=True, exist_ok=True)
            cd_file.write_text(json.dumps({
                "started_at": datetime.now().isoformat(),
                "duration_min": 90,
                "reason": context.get("reason"),
            }))
            result["cooldown_file"] = str(cd_file)

        elif action == "chrome_profile_unlock":
            mode = context.get("mode")
            profile = REPO_ROOT / "rakuten-room" / "bot" / "data" / f"chrome_profile_{mode}"
            for lock in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
                p = profile / lock
                if p.exists():
                    try:
                        p.unlink()
                        result.setdefault("removed", []).append(lock)
                    except Exception:
                        pass

        elif action == "disk_cleanup":
            # 古い screenshot / logs を削除
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(days=30)
            cleaned = 0
            for d in [REPO_ROOT / "ops" / "patrol_screenshots"]:
                if d.exists():
                    for f in d.iterdir():
                        try:
                            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                                f.unlink()
                                cleaned += 1
                        except Exception:
                            pass
            result["cleaned"] = cleaned

        elif action == "escalate_ceo":
            # 2026-05-07 P0-5 (Plan v5 真因 #4): Slack 送信失敗を確実に検知
            # 旧: subprocess.run() の戻り値を無視 (fire-and-forget)
            # 新: returncode 確認 + 3 回 retry + 全失敗時は escalate_failed.log に残す
            sl = REPO_ROOT / "ops" / "notifications" / "slack_reporter.py"
            msg = f"<!channel> 【patrol_v6 CRITICAL】 {context.get('summary', 'unknown')}"
            slack_sent = False
            attempts: list[dict] = []
            for attempt in range(3):
                try:
                    r = subprocess.run(
                        [sys.executable, str(sl), msg],
                        capture_output=True, timeout=30, creationflags=NO_WIN,
                    )
                    attempts.append({
                        "attempt": attempt + 1,
                        "rc": r.returncode,
                        "stdout_tail": (r.stdout or b"")[-200:].decode("utf-8", "ignore"),
                        "stderr_tail": (r.stderr or b"")[-200:].decode("utf-8", "ignore"),
                    })
                    # slack_reporter.py は成功時 returncode=0 / OK を含む
                    if r.returncode == 0:
                        slack_sent = True
                        break
                except Exception as e:
                    attempts.append({"attempt": attempt + 1, "error": str(e)})
                # backoff: 5 秒 → 30 秒 (合計 < 1 分)
                if attempt < 2:
                    time.sleep(5 if attempt == 0 else 30)
            result["slack_sent"] = slack_sent
            result["attempts"] = attempts
            if not slack_sent:
                # 3 回失敗時はファイルに残す (CEO が見つけ次第対応)
                fail_log = REPO_ROOT / "state" / "escalate_failed.log"
                fail_log.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with fail_log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "ts": datetime.now().isoformat(),
                            "msg": msg,
                            "attempts": attempts,
                        }, ensure_ascii=False) + "\n")
                except Exception as e:
                    result["fail_log_error"] = str(e)

        else:
            result["status"] = "unknown_action"
    except Exception as e:
        result["error"] = str(e)
    return result


# ============================================================
# main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true",
                        help="auto recover を実行せず判定のみ (--auto-recover を override)")
    parser.add_argument("--auto-recover", action="store_true",
                        dest="auto_recover",
                        help="2026-05-07 P0-2: 明示的に指定された場合のみ auto_recover を実行")
    parser.add_argument("--layer", help="特定 layer のみ実行")
    args = parser.parse_args()

    print(f"=== patrol_v6 START {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    layer_results = {}
    layers_to_run = [(k, m, d) for k, m, d in LAYERS if not args.layer or k == args.layer]
    for short, mod_name, desc in layers_to_run:
        result = run_layer(mod_name)
        layer_results[short] = result
        status = result.get("status", "?")
        alerts = result.get("alerts", [])
        alert_summary = f"{len(alerts)} alerts" if alerts else "OK"
        print(f"  [{short}] {desc}: status={status}, {alert_summary}")
        for a in alerts[:3]:  # 最初3件のみ表示
            print(f"     - [{a.get('level','?')}] {a.get('message','')}")

    # 全 alert を集計
    all_alerts = []
    for short, res in layer_results.items():
        for a in res.get("alerts", []):
            a["layer"] = short
            all_alerts.append(a)

    crit = [a for a in all_alerts if a.get("level") == "CRITICAL"]
    warn = [a for a in all_alerts if a.get("level") == "WARN"]

    print(f"\n=== Summary: CRITICAL={len(crit)} WARN={len(warn)} ===")

    # 自動復旧
    # 2026-05-07 P0-2 (Plan v5 真因 #4):
    #   旧: not check_only → recover (default で recover 走るはずだったが Layer alert に
    #       auto_recover キーが入っていなかった等で実態は観測のみ)
    #   新: --auto-recover 明示指定時に走る (--check-only で override 可)
    should_recover = args.auto_recover and not args.check_only
    recovery_actions_taken: list[dict] = []
    if should_recover and (crit or warn):
        for a in all_alerts:
            recover = a.get("auto_recover")
            if recover:
                print(f"  [auto_recover] {a.get('layer')} → {recover}")
                rec_result = auto_recover(recover, a.get("context", {}))
                rec_result["alert_layer"] = a.get("layer")
                rec_result["alert_message"] = a.get("message")
                recovery_actions_taken.append(rec_result)
                print(f"     result: {rec_result}")
        # CRITICAL がありどの alert にも auto_recover が紐付いていない場合は
        # escalate_ceo を保険として 1 回だけ走らせる (silent stuck の再発防止)
        if crit and not any(a.get("auto_recover") for a in all_alerts):
            print("  [auto_recover] CRITICAL あり / auto_recover 未指定 → escalate_ceo fallback")
            summary = "; ".join(a.get("message", "?") for a in crit[:3])
            rec = auto_recover("escalate_ceo", {"summary": summary})
            recovery_actions_taken.append(rec)
    elif (crit or warn):
        print(f"  [auto_recover] skip (auto_recover={args.auto_recover}, "
              f"check_only={args.check_only})")

    # state file に書き出し (dashboard 用)
    state_file = REPO_ROOT / "state" / "patrol_v6_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "ts": datetime.now().isoformat(),
        "layers": layer_results,
        "critical_count": len(crit),
        "warn_count": len(warn),
        "critical_alerts": [
            {"layer": a.get("layer"), "message": a.get("message"),
             "auto_recover": a.get("auto_recover")}
            for a in crit
        ],
        "warn_alerts": [
            {"layer": a.get("layer"), "message": a.get("message"),
             "auto_recover": a.get("auto_recover")}
            for a in warn
        ],
        "auto_recover_enabled": args.auto_recover,
        "check_only": args.check_only,
        "recovery_actions_taken": recovery_actions_taken,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # Plan v4 P4: room_bot_v6.db patrol_log に記録
    try:
        import sqlite3
        db_path = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v6.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=2)
            for short, res in layer_results.items():
                conn.execute(
                    "INSERT INTO patrol_log(ts, layer, status, alerts_json, actions_taken_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), short, res.get("status", "?"),
                     json.dumps(res.get("alerts", []), ensure_ascii=False),
                     json.dumps([], ensure_ascii=False)),
                )
            # SLO 違反は別 table へ
            for a in all_alerts:
                if a.get("level") in ("CRITICAL", "WARN"):
                    conn.execute(
                        "INSERT INTO slo_violations(detected_at, function, sli_name, "
                        "actual_value, slo_threshold, alert_level, auto_recover_action) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (datetime.now().isoformat(),
                         a.get("layer", "?"),
                         a.get("message", "?")[:120],
                         None, None,
                         a.get("level"),
                         a.get("auto_recover")),
                    )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[patrol_v6] db log error: {e}", file=sys.stderr)

    return 2 if crit else (1 if warn else 0)


if __name__ == "__main__":
    sys.exit(main())
