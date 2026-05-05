#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orchestrator_v5 — single dispatch point for 4 functions.

Replaces scheduler_v2/v3/v3_backup/v4 (all frozen 2026-04-14 in version_registry).
This is the MVP that provides:
  1. preflight_v5 gate (13 items; blocked=True aborts run)
  2. Lock acquisition via shared.vb_lock
       * post / like          → Lock(name)
       * follow / followback  → VbLock(action)  (VB machine mutex)
  3. Dispatch to per-action runner
  4. execution_log insert (start + end with status/reason/counts)
  5. No background loop — triggered per-fire by Task Scheduler / cron / manual

Usage:
    python ops/scheduler/orchestrator_v5.py --action follow      [--limit N]
    python ops/scheduler/orchestrator_v5.py --action follow_host [--limit N]
    python ops/scheduler/orchestrator_v5.py --action post        [--batch 1|2]
    python ops/scheduler/orchestrator_v5.py --action like        [--limit N]
    python ops/scheduler/orchestrator_v5.py --action followback  [--limit N]
    python ops/scheduler/orchestrator_v5.py --action preflight   (just run preflight)

深夜シーケンシャル戦略 (Task Scheduler 設定が必要 — CEO確認後に適用):
    01:00-03:00  follow_host  (HOST先行、c24=0から300件。RL率0%帯)
    03:00-05:00  follow       (VM起動、c24=300から追加)
    ※ 01:00にVM+HOST同時起動すると997件トラップリスクがあるため必ずシーケンシャルで起動する。

Exit codes:
    0  success (or preflight PASS)
    2  preflight blocked
    3  lock busy (another runner is holding the target lock)
    4  runner failed
    5  invalid args / configuration

Design notes (kept deliberately small — MVP):
  * This file does NOT embed per-runner logic. It delegates via subprocess so
    existing runners (follow_rpa_vm.py, queue_executor.py, like executors) can
    be swapped without rewriting orchestrator.
  * heartbeat is updated at start & end via rakuten-room/bot/data/state/heartbeat.json
  * Failures are surfaced through execution_log.stop_reason for post-mortem.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.vb_lock import Lock, VbLock, LockBusy  # noqa: E402

DB_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"
HEARTBEAT_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "state" / "heartbeat.json"
PREFLIGHT_SCRIPT = REPO_ROOT / "ops" / "scheduler" / "preflight_v5.py"

VALID_ACTIONS = {"follow", "follow_host", "post", "like", "followback", "replenish", "preflight"}

# ---- heartbeat ---------------------------------------------------------------

def update_heartbeat(job: str, status: str = "running") -> None:
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(),
            "current_job": job,
            "status": status,
            "orchestrator": "v5",
        }
        tmp = HEARTBEAT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(HEARTBEAT_PATH)
    except Exception as e:
        print(f"[orchestrator_v5] heartbeat write failed: {e}", file=sys.stderr)


# ---- execution_log -----------------------------------------------------------

def execution_log_insert(action: str, status: str, detail: dict) -> int:
    """
    Insert an execution_log row. Returns inserted id (or 0 on failure).
    Schema expected (see v5_schema.py):
        execution_log(id, plan_date, action_type, started_at, finished_at,
                      status, success_count, fail_count, stop_reason, detail_json)
    We probe the schema and map best-effort.
    """
    if not DB_PATH.exists():
        return 0
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("PRAGMA table_info(execution_log)")
        cols = [r[1] for r in cur.fetchall()]
        now = datetime.now().isoformat()
        action_id = detail.get("action_id") or f"{action}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
        row = {
            "action_id": action_id,
            "plan_date": datetime.now().strftime("%Y-%m-%d"),
            "action_type": action,
            "executor": detail.get("executor", f"orchestrator_v5.{action}"),
            "started_at": detail.get("started_at", now),
            "finished_at": detail.get("finished_at", now) if status != "running" else None,
            "status": status,
            "target_count": detail.get("target_count"),
            "success_count": detail.get("success_count", 0),
            "fail_count": detail.get("fail_count", 0),
            "skip_count": detail.get("skip_count", 0),
            "stop_reason": detail.get("stop_reason"),
            "error_detail": detail.get("error_detail"),
            "metrics_json": json.dumps({k: v for k, v in detail.items()
                                         if k not in {"action_id", "executor"}},
                                        ensure_ascii=False),
        }
        # Filter columns that actually exist
        insert_cols = [c for c in row if c in cols]
        placeholders = ",".join(["?"] * len(insert_cols))
        sql = f"INSERT INTO execution_log ({','.join(insert_cols)}) VALUES ({placeholders})"
        cur.execute(sql, [row[c] for c in insert_cols])
        con.commit()
        rid = cur.lastrowid
        con.close()
        return rid
    except Exception as e:
        print(f"[orchestrator_v5] execution_log insert failed: {e}", file=sys.stderr)
        return 0


# ---- preflight ---------------------------------------------------------------

def run_preflight() -> tuple[bool, str]:
    """Invoke preflight_v5.py; return (ok, summary)."""
    if not PREFLIGHT_SCRIPT.exists():
        return (False, f"preflight_v5.py not found at {PREFLIGHT_SCRIPT}")
    try:
        r = subprocess.run(
            [sys.executable, str(PREFLIGHT_SCRIPT), "--verbose"],
            capture_output=True, text=True, timeout=60,
        )
        tail = "\n".join(r.stdout.splitlines()[-15:])
        return (r.returncode == 0, tail)
    except Exception as e:
        return (False, f"preflight invocation error: {e}")


# ---- per-action runner dispatch ----------------------------------------------

def _run_sub(cmd, timeout):
    """subprocess.run wrapper that forces utf-8 decoding with errors=replace.
    Windows default cp932 chokes on Japanese stdout — this is the fix."""
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    def _dec(b):
        if b is None:
            return ""
        try:
            return b.decode("utf-8", errors="replace")
        except Exception:
            return b.decode("cp932", errors="replace")
    return r.returncode, _dec(r.stdout), _dec(r.stderr)


def runner_follow(limit: int) -> tuple[int, str, dict]:
    """Dispatch to follow via existing vm_follow_launcher (VM machine)."""
    script = REPO_ROOT / "ops" / "vm_follow_launcher.py"
    cmd = [sys.executable, str(script), "--force", "--limit", str(limit)]
    rc, out, err = _run_sub(cmd, timeout=600)
    return (rc, "ok" if rc == 0 else "launcher_fail",
            {"stdout_tail": out[-500:], "stderr_tail": err[-500:]})


def runner_follow_host(limit: int) -> tuple[int, str, dict]:
    """Dispatch to follow_host_runner.py (Main PC Playwright, no VM required).

    深夜シーケンシャル戦略 01:00-03:00 枠で HOST 先行起動する際に使用。
    c24=0 から積み上げることで RL 率0%・70-150件/session が期待できる。
    timeout=2100 = MAX_RUNTIME_SEC(1800) + 起動/ログ書込オーバーヘッド(300s)。
    """
    script = REPO_ROOT / "ops" / "follow_host_runner.py"
    cmd = [sys.executable, str(script), "--limit", str(limit)]
    rc, out, err = _run_sub(cmd, timeout=2100)
    # follow_host_runner が stop_reason を最終行に出力するので抽出する
    stop_reason = "launcher_fail" if rc != 0 else "ok"
    for line in reversed((out or "").splitlines()):
        if "stop=" in line:
            try:
                stop_reason = line.split("stop=")[1].strip().split()[0].rstrip("=")
            except Exception:
                pass
            break
    return (rc, stop_reason, {"stdout_tail": out[-600:], "stderr_tail": err[-500:]})


def runner_replenish(limit: int) -> tuple[int, str, dict]:
    """Dispatch to商品プール補充。

    2026-05-05 Phase B-1: replenish 責任を orchestrator_v5 に明確化。
        従来は run.py auto の Step 2 で内部呼び出し（Post Batch1/2/3 起動時のみ）
        だったが、本 action で独立実行可能に。Windows Task Scheduler に
        毎日 06:00 で登録すれば daily replenish が確実に走る。

    呼び出し: rakuten-room/bot/run.py replenish (legacy・既存実装) を使用。
    timeout=1200 (20分) — 楽天API レート制限考慮で大きめ。
    """
    script = REPO_ROOT / "rakuten-room" / "bot" / "run.py"
    cmd = [sys.executable, str(script), "replenish"]
    # limit は replenish では target_max として転用可能だが、現行 run.py replenish は
    # config の POOL_MIN/MAX を使うので transparent。
    rc, out, err = _run_sub(cmd, timeout=1200)
    stop_reason = "ok" if rc == 0 else "replenish_fail"
    # 終了行から「N件追加」抽出を試みる
    detail = {"stdout_tail": out[-800:], "stderr_tail": err[-500:]}
    for line in reversed((out or "").splitlines()):
        if "件追加" in line or "プール" in line:
            detail["last_line"] = line.strip()
            break
    return (rc, stop_reason, detail)


def runner_post(batch: int, limit: int) -> tuple[int, str, dict]:
    """Dispatch to post via rakuten-room/bot/run.py auto --batch N [--limit M].

    2026-04-23 マーケ加速指示: short-cycle verification (L1=1 / L2=3 / L3=5 / L4=10)
    Forward --limit to run.py when limit>0 (0 = config default preserved).
    """
    script = REPO_ROOT / "rakuten-room" / "bot" / "run.py"
    cmd = [sys.executable, str(script), "auto", "--batch", str(batch)]
    if limit and limit > 0:
        cmd += ["--limit", str(limit)]
    # Keep a conservative timeout; post batches run 30-60min typically.
    rc, out, err = _run_sub(cmd, timeout=3600)
    return (rc, "ok" if rc == 0 else "runner_fail",
            {"stdout_tail": out[-800:], "stderr_tail": err[-500:]})


DAILY_TARGETS_CACHE = REPO_ROOT / "ops" / "scheduler" / "daily_targets.json"
LIKE_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json"


def _get_today_like_target() -> int:
    """今日のLIKE目標をdaily_targets.jsonキャッシュから取得。0=キャッシュなし/未設定
    2026-05-02修正: 昨日のキャッシュも有効（patrol が深夜に実行→今朝まで跨ぎ問題を解消）
    """
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if not DAILY_TARGETS_CACHE.exists():
        return 0
    try:
        cache = json.loads(DAILY_TARGETS_CACHE.read_text(encoding="utf-8"))
        # 今日 or 昨日のキャッシュを有効とする（日付跨ぎ対策）
        if cache.get("date") in (today, yesterday):
            return int(cache.get("targets", {}).get("like", 0) or 0)
    except Exception:
        pass
    return 0


def _get_today_liked_count() -> int:
    """今日のいいね済み件数をlike_history.jsonから計算"""
    from datetime import date
    today = date.today().isoformat()
    if not LIKE_HISTORY.exists():
        return 0
    try:
        data = json.loads(LIKE_HISTORY.read_text(encoding="utf-8"))
        return sum(1 for e in data if e.get("liked_at", "").startswith(today))
    except Exception:
        return 0


def runner_like(limit: int) -> tuple[int, str, dict]:
    """
    Dispatch to LikeExecutor v6.1 via rakuten-room/bot/run.py like --limit N.
    2026-04-23: wired per マーケ厳命 12:49 (B条件).
    2026-05-02: daily-cap precise-stop — 目標丁度で終わる設計に修正。
    """
    # ── 日次精密キャップ ────────────────────────────────────────────────────────
    today_target = _get_today_like_target()
    today_count  = _get_today_liked_count()
    if today_target > 0:
        remaining = today_target - today_count
        if remaining <= 0:
            msg = f"LIKE daily_target達成済 ({today_count}/{today_target}) → skip"
            print(f"[runner_like] {msg}")
            return (0, "daily_target_reached",
                    {"today_count": today_count, "today_target": today_target, "note": msg})
        effective_limit = remaining if limit <= 0 else min(limit, remaining)
        print(f"[runner_like] today={today_count}/{today_target} remaining={remaining} → --limit {effective_limit}")
    else:
        effective_limit = limit if limit > 0 else 0
    # ────────────────────────────────────────────────────────────────────────────
    script = REPO_ROOT / "rakuten-room" / "bot" / "run.py"
    cmd = [sys.executable, str(script), "like", "--limit", str(effective_limit)]
    # LIKE batches are shorter than POST; 20min ceiling is generous.
    rc, out, err = _run_sub(cmd, timeout=1200)
    # Parse the summary line for liked/skipped/failed
    detail = {"stdout_tail": out[-800:], "stderr_tail": err[-500:]}
    liked = failed = skipped = 0
    for line in out.splitlines():
        # 「いいね完了: N件成功 / M件スキップ / K件失敗」
        if "いいね完了" in line or "件成功" in line:
            import re
            m = re.search(r"(\d+)件成功\s*/\s*(\d+)件スキップ\s*/\s*(\d+)件失敗", line)
            if m:
                liked, skipped, failed = (int(m.group(i)) for i in (1, 2, 3))
                break
    detail.update({"success_count": liked, "skip_count": skipped, "fail_count": failed})
    stop_reason = "runner_fail" if rc != 0 else (
        "target_limit_reached" if liked >= limit else "source_exhausted" if liked > 0 else "zero_liked")
    return (rc, stop_reason, detail)


def runner_followback(limit: int) -> tuple[int, str, dict]:
    """
    Dispatch to followback_executor.py --execute --limit N.
    Returns stop_reason from the executor's JSON output.
    """
    script_mod = "rakuten-room.bot.executor.followback_executor"
    cmd = [sys.executable, "-m", script_mod, "--execute", "--limit", str(limit)]
    rc, out, err = _run_sub(cmd, timeout=900)
    detail = {"stdout_tail": out[-500:], "stderr_tail": err[-300:]}
    stop_reason = "runner_fail"
    # 2026-04-23: executor may fall back to stderr when Playwright closes stdout,
    # so search both streams for the trailing JSON line.
    combined = (out or "") + "\n" + (err or "")
    try:
        json_lines = [l for l in combined.strip().splitlines() if l.startswith("{")]
        if not json_lines:
            raise ValueError("no JSON payload found")
        last_json_line = json_lines[-1]
        payload = json.loads(last_json_line)
        stop_reason = payload.get("stop_reason", "unknown")
        detail.update({k: v for k, v in payload.items() if k != "stop_reason"})
        # If executor reported status=ok but rc non-zero (stdout close crash),
        # treat as success at orchestrator layer.
        if payload.get("status") == "ok" and rc != 0:
            rc = 0
            detail["rc_rescued_from_stdout_close"] = True
    except Exception as e:
        detail["parse_error"] = str(e)
    return (rc, stop_reason, detail)


# ---- main --------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="orchestrator_v5 dispatch")
    parser.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    parser.add_argument("--limit", type=int, default=100, help="runner-specific item cap")
    parser.add_argument("--batch", type=int, default=1, help="post batch index (1 or 2)")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="DEV ONLY — bypass preflight gate (never use in prod)")
    parser.add_argument("--lock-wait", type=int, default=900,
                        help="seconds to wait for lock before aborting "
                             "(2026-04-23: default 900s = force-queue暫定排他, "
                             "Chrome profile 単一衝突を人手再実行なしで回避)")
    args = parser.parse_args(argv)

    action = args.action
    started_at = datetime.now().isoformat()
    update_heartbeat(action, "running")

    # --- preflight gate ------------------------------------------------------
    if action == "preflight":
        ok, summary = run_preflight()
        print(summary)
        return 0 if ok else 2

    if not args.skip_preflight:
        ok, summary = run_preflight()
        if not ok:
            print("[orchestrator_v5] preflight BLOCKED — aborting action")
            print(summary)
            execution_log_insert(action, "blocked", {
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(),
                "stop_reason": "preflight_blocked",
                "preflight_tail": summary,
            })
            update_heartbeat(action, "blocked")
            return 2

    # --- lock acquisition ----------------------------------------------------
    lock_ctx = None
    try:
        if action in ("follow", "followback"):
            lock_ctx = VbLock(action, wait_sec=args.lock_wait)
        elif action == "replenish":
            # replenish は API 取得のみ（Chrome 不要）なので post lock を共有
            # Phase A-2 で profile分離済のため Chrome competition は無関係
            lock_ctx = Lock("post", wait_sec=args.lock_wait)
        else:
            lock_ctx = Lock(action, wait_sec=args.lock_wait)
        lock_held = lock_ctx.__enter__()
    except LockBusy as e:
        print(f"[orchestrator_v5] lock busy — aborting: {e}")
        execution_log_insert(action, "skipped", {
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "stop_reason": "lock_busy",
            "detail": str(e),
        })
        update_heartbeat(action, "skipped")
        return 3

    # --- runner dispatch -----------------------------------------------------
    try:
        if action == "follow":
            rc, stop_reason, detail = runner_follow(args.limit)
        elif action == "follow_host":
            rc, stop_reason, detail = runner_follow_host(args.limit)
        elif action == "post":
            rc, stop_reason, detail = runner_post(args.batch, args.limit)
        elif action == "like":
            rc, stop_reason, detail = runner_like(args.limit)
        elif action == "followback":
            rc, stop_reason, detail = runner_followback(args.limit)
        elif action == "replenish":
            rc, stop_reason, detail = runner_replenish(args.limit)
        else:
            rc, stop_reason, detail = (5, "invalid_action", {})
    finally:
        try:
            lock_ctx.__exit__(None, None, None)
        except Exception:
            pass

    finished_at = datetime.now().isoformat()
    status = "success" if rc == 0 and stop_reason not in ("not_wired", "not_implemented") else \
             "pending_impl" if stop_reason in ("not_wired", "not_implemented") else "failed"
    execution_log_insert(action, status, {
        "started_at": started_at,
        "finished_at": finished_at,
        "stop_reason": stop_reason,
        "rc": rc,
        **detail,
    })
    update_heartbeat(action, status)
    print(f"[orchestrator_v5] action={action} status={status} stop_reason={stop_reason} rc={rc}")

    # --- スプシ即時更新（CEO指示: BOT実行のたびに都度記入） ---
    if action in ("post", "like", "followback") and status != "blocked":
        try:
            _sheet_sync_after_action()
        except Exception as e:
            print(f"[orchestrator_v5] sheet_sync skipped: {e}")

    return rc if status == "failed" else 0


def _sheet_sync_after_action() -> None:
    """BOT実行完了後にスプシを即時更新（CEO指示 2026-05-01）"""
    import subprocess as _sp
    script = Path(__file__).resolve().parents[2] / "ops" / "sheets" / "daily_log_writer.py"
    if not script.exists():
        print(f"[sheet_sync] script not found: {script}")
        return
    r = _sp.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, cwd=str(script.parent.parent.parent),
    )
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        # Extract the [OK] line for concise log
        for line in out.splitlines():
            if "[OK]" in line or "DONE" in line:
                print(f"[sheet_sync] {line.strip()}")
                break
    else:
        print(f"[sheet_sync] WARN rc={r.returncode}: {out[-150:]}")


if __name__ == "__main__":
    raise SystemExit(main())
