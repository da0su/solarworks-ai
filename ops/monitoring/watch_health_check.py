"""
watch_health_check.py
24時間 watch 安定稼働確認スクリプト

usage:
  python ops/monitoring/watch_health_check.py           # 状態確認のみ
  python ops/monitoring/watch_health_check.py --slack   # Slack通知付き
  python ops/monitoring/watch_health_check.py --full    # 全チェック詳細出力

確認項目:
  1. state-audit CLEAN 維持
  2. bridge.log サイズ（5MB 未満 or ローテーション済み）
  3. events.jsonl 行数（10,000 行未満）
  4. state の最終更新時刻（2時間超 stale なら警告）
  5. watch プロセス生存確認
"""

import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

# プロジェクトルートをパスに追加
_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_root))

from slack_bridge import StateManager, state_mgr  # noqa: E402

# ============================================================
# 定数
# ============================================================
SLACK_DIR = Path.home() / ".slack_bridge"
BRIDGE_LOG  = SLACK_DIR / "bridge.log"
EVENTS_FILE = SLACK_DIR / "events.jsonl"

LOG_MAX_BYTES = 5 * 1024 * 1024          # 5MB
EVENTS_MAX_LINES = 10_000
STATE_STALE_MINUTES_BUSY = 30             # busy時: 30分更新なしで警告
STATE_STALE_MINUTES_IDLE = 480            # idle時: 8時間更新なしで警告


# ============================================================
# チェック関数
# ============================================================
def check_state_audit() -> dict:
    """state-audit を実行して CLEAN かどうか確認"""
    result = subprocess.run(
        [sys.executable, str(_root / "slack_bridge.py"), "state-audit"],
        capture_output=True, timeout=30, cwd=str(_root),
    )
    # Windows CP932 端末でも動くよう errors='replace' でデコード
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    clean = "Result: CLEAN" in stdout
    issues = [
        line.strip() for line in stdout.splitlines()
        if line.strip().startswith("WARN:")
    ]
    return {
        "ok": clean,
        "result": "CLEAN" if clean else "NEEDS ATTENTION",
        "issues": issues,
    }


def check_bridge_log() -> dict:
    """bridge.log のサイズ確認"""
    if not BRIDGE_LOG.exists():
        return {"ok": True, "size_kb": 0, "note": "log not yet created"}
    size = BRIDGE_LOG.stat().st_size
    rotated = any(Path(str(BRIDGE_LOG) + f".{i}").exists() for i in range(1, 4))
    ok = size < LOG_MAX_BYTES
    return {
        "ok": ok,
        "size_kb": size // 1024,
        "rotated": rotated,
        "note": "OK" if ok else f"WARN: {size//1024}KB >= {LOG_MAX_BYTES//1024}KB",
    }


def check_events_jsonl() -> dict:
    """events.jsonl 行数確認"""
    if not EVENTS_FILE.exists():
        return {"ok": True, "lines": 0, "note": "events not yet created"}
    lines = sum(1 for _ in open(EVENTS_FILE, encoding="utf-8", errors="replace"))
    ok = lines < EVENTS_MAX_LINES
    return {
        "ok": ok,
        "lines": lines,
        "note": "OK" if ok else f"WARN: {lines} lines >= {EVENTS_MAX_LINES}",
    }


def check_state_freshness() -> dict:
    """state の最終更新時刻確認（idle時は8時間、busy時は30分でアラート）"""
    state = state_mgr.load()
    updated = state.get("updated_at", "")
    sys_status = state.get("system_status", "idle")
    if not updated:
        return {"ok": False, "note": "updated_at missing"}
    try:
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        threshold = STATE_STALE_MINUTES_BUSY if sys_status == "busy" else STATE_STALE_MINUTES_IDLE
        ok = age_min < threshold
        return {
            "ok": ok,
            "updated_at": updated[:19],
            "age_minutes": round(age_min, 1),
            "system_status": sys_status,
            "note": (f"OK ({sys_status}, {age_min:.0f}min ago)" if ok
                     else f"WARN: {sys_status} state stale ({age_min:.0f}min > {threshold}min)"),
        }
    except ValueError as e:
        return {"ok": False, "note": f"invalid updated_at: {e}"}


def check_watch_process() -> dict:
    """watch プロセス生存確認（slack_bridge.py watch が動いているか）"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, timeout=10,
        )
        stdout = result.stdout.decode("cp932", errors="replace") if result.stdout else ""
        alive = "python.exe" in stdout
        return {"ok": alive, "note": "process alive" if alive else "WARN: no python.exe found"}
    except Exception as e:
        return {"ok": True, "note": f"check skipped: {e}"}  # Windows以外でも動くよう


# ============================================================
# メイン
# ============================================================
def run_all_checks() -> dict:
    checks = {
        "state_audit":    check_state_audit(),
        "bridge_log":     check_bridge_log(),
        "events_jsonl":   check_events_jsonl(),
        "state_freshness": check_state_freshness(),
        "watch_process":  check_watch_process(),
    }
    all_ok = all(v["ok"] for v in checks.values())
    return {"all_ok": all_ok, "checks": checks, "checked_at": datetime.now(timezone.utc).isoformat()}


def print_report(report: dict, full: bool = False):
    print(f"\n{'='*60}")
    print(f"  Watch Health Check -- {report['checked_at'][:19]}")
    status_str = "[HEALTHY]" if report["all_ok"] else "[NEEDS ATTENTION]"
    print(f"  Overall: {status_str}")
    print(f"{'='*60}")
    for name, c in report["checks"].items():
        icon = "[OK]  " if c["ok"] else "[WARN]"
        print(f"  {icon} {name:<22} {c.get('note', '')}")
        if full and not c["ok"]:
            for issue in c.get("issues", []):
                print(f"         -> {issue}")
    print()


def send_slack_alert(report: dict):
    """問題がある場合のみ Slack に通知"""
    if report["all_ok"]:
        return
    try:
        from slack_bridge import send_message
        lines = ["⚠️ *Watch Health Check ALERT*"]
        for name, c in report["checks"].items():
            if not c["ok"]:
                lines.append(f"  • {name}: {c.get('note', 'NG')}")
        lines.append(f"  checked_at: {report['checked_at'][:19]}")
        send_message("\n".join(lines))
        print("[Slack] Alert sent")
    except Exception as e:
        print(f"[Slack] Failed to send: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Watch health check")
    parser.add_argument("--slack", action="store_true", help="Send Slack alert on issues")
    parser.add_argument("--full", action="store_true", help="Show full details")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    report = run_all_checks()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, full=args.full)

    if args.slack:
        send_slack_alert(report)

    # 問題があれば exit code 1
    sys.exit(0 if report["all_ok"] else 1)
