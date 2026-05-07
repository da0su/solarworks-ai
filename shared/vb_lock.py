#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VB Lock + 4-function mutual exclusion.

Lock files live under rakuten-room/bot/data/locks/:
  - execution.lock     (global, held by orchestrator_v5)
  - lock.post          (main PC - post runner)
  - lock.like          (main PC - like runner)
  - lock.followback    (VB      - followback runner)
  - lock.follow        (VB      - follow runner)
  - vb_lock            (VB machine-wide mutex: follow XOR followback)

Each file contains JSON: {pid, host, started_at, action, label}.
Stale locks (orphaned) are auto-cleaned:
  - pid not running on this host → delete
  - age > STALE_MAX_MIN minutes   → delete (defensive for cross-host)

Usage:
    from shared.vb_lock import Lock, VbLock, LockBusy
    with Lock("post") as lock:
        ...run post batch...
    with VbLock("followback") as lock:
        ...run followback (blocks if follow holds vb_lock)...
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = REPO_ROOT / "rakuten-room" / "bot" / "data" / "locks"
LOCK_DIR.mkdir(parents=True, exist_ok=True)

# Minutes after which a lock is considered stale regardless of PID state.
# 2026-05-07 P0-7 (Plan v5 真因 #3):
#   旧: 120 分 (Follow runs 45-60min + rate_limit cooldown を見越して)
#   新: 60 分 (5/5 11h+ stale lock 残置 = 機能していなかった事実 + Follow VM 化で
#       長時間 lock を host で取らなくなったため)
#   POST batch は最長で 60 分以内に完了する想定なので 60 分で十分。
STALE_MAX_MIN = 60

VALID_NAMES = {"post", "like", "follow", "followback", "execution", "vb_lock"}


class LockBusy(Exception):
    """Raised when a lock is held by another live process."""


def _lock_path(name: str) -> Path:
    if name not in VALID_NAMES:
        raise ValueError(f"unknown lock name: {name!r}; expected one of {sorted(VALID_NAMES)}")
    if name == "execution":
        return LOCK_DIR / "execution.lock"
    if name == "vb_lock":
        return LOCK_DIR / "vb_lock"
    return LOCK_DIR / f"lock.{name}"


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is running on this host."""
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            import subprocess
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            # tasklist prints "INFO: No tasks are running..." if no match
            out = r.stdout or ""
            return f'"{pid}"' in out
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _read_lock(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_stale(meta: dict, path: Path) -> bool:
    # Defensive: treat unreadable/old locks as stale.
    if not meta:
        return True
    # Age check
    try:
        started = datetime.fromisoformat(meta.get("started_at", ""))
        age_min = (datetime.now() - started).total_seconds() / 60
        if age_min > STALE_MAX_MIN:
            return True
    except Exception:
        # mtime fallback
        age_min = (datetime.now().timestamp() - path.stat().st_mtime) / 60
        if age_min > STALE_MAX_MIN:
            return True
    # PID liveness (only trust if same host)
    my_host = socket.gethostname()
    if meta.get("host") == my_host:
        pid = int(meta.get("pid", -1))
        if not _pid_alive(pid):
            return True
    return False


def _try_acquire(path: Path, label: str) -> Optional[dict]:
    """Attempt atomic create (O_EXCL). Returns the meta dict on success, None on busy."""
    existing = _read_lock(path)
    if existing is not None:
        if _is_stale(existing, path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            return None
    meta = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now().isoformat(),
        "label": label,
    }
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        try:
            path.unlink()
        except Exception:
            pass
        raise
    return meta


@contextmanager
def Lock(name: str, *, label: str = "", wait_sec: int = 0, poll_sec: float = 5.0):
    """
    Acquire a named lock (post / like / follow / followback / execution).

    wait_sec=0  → non-blocking. Raises LockBusy if held.
    wait_sec>0  → retry every poll_sec until acquired or timeout (then LockBusy).
    """
    path = _lock_path(name)
    label = label or name
    meta = _try_acquire(path, label)
    start = time.time()
    while meta is None and (time.time() - start) < wait_sec:
        time.sleep(poll_sec)
        meta = _try_acquire(path, label)
    if meta is None:
        cur = _read_lock(path) or {}
        raise LockBusy(f"{name} held by pid={cur.get('pid')} host={cur.get('host')} "
                       f"started_at={cur.get('started_at')} label={cur.get('label')}")
    try:
        yield meta
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def VbLock(action: str, *, wait_sec: int = 300, poll_sec: float = 10.0):
    """
    VB machine-wide mutex: follow XOR followback may hold at a time.

    action must be "follow" or "followback". Other values raise ValueError.
    On success, the per-action lock (lock.follow / lock.followback) is also held
    for the same duration so patrols / inspectors can see which action is active.
    """
    if action not in ("follow", "followback"):
        raise ValueError(f"VbLock action must be follow|followback, got {action!r}")
    vb_path = _lock_path("vb_lock")
    vb_meta = _try_acquire(vb_path, action)
    start = time.time()
    while vb_meta is None and (time.time() - start) < wait_sec:
        time.sleep(poll_sec)
        vb_meta = _try_acquire(vb_path, action)
    if vb_meta is None:
        cur = _read_lock(vb_path) or {}
        raise LockBusy(f"vb_lock held by label={cur.get('label')} pid={cur.get('pid')} "
                       f"started_at={cur.get('started_at')}")
    # Also take the per-action lock so status is visible.
    action_path = _lock_path(action)
    try:
        action_meta = _try_acquire(action_path, action)
        if action_meta is None:
            # Stale-cleanup was attempted inside _try_acquire; re-try once.
            action_meta = _try_acquire(action_path, action)
        yield {"vb_lock": vb_meta, "action_lock": action_meta}
    finally:
        for p in (action_path, vb_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def status() -> dict:
    """Return a snapshot of all lock files for diagnostics."""
    out = {}
    for name in VALID_NAMES:
        p = _lock_path(name)
        meta = _read_lock(p) if p.exists() else None
        out[name] = {
            "path": str(p),
            "exists": p.exists(),
            "meta": meta,
            "stale": _is_stale(meta, p) if meta else None,
        }
    return out


def main(argv=None):
    """CLI: `python -m shared.vb_lock status` shows the table."""
    argv = argv or sys.argv[1:]
    if not argv or argv[0] == "status":
        snap = status()
        print(f"=== VB Lock status @ {datetime.now().isoformat(timespec='seconds')} ===")
        for name, info in snap.items():
            flag = "HELD " if info["exists"] else "free "
            stale = " (STALE)" if info["stale"] else ""
            meta = info["meta"] or {}
            extra = ""
            if info["exists"]:
                extra = f" pid={meta.get('pid')} host={meta.get('host')} " \
                        f"label={meta.get('label')} started_at={meta.get('started_at')}"
            print(f"  [{flag}] {name:11s}{stale}{extra}")
        return 0
    if argv[0] == "clear":
        # Clear all stale locks (safety utility for operators).
        cleared = []
        for name in VALID_NAMES:
            p = _lock_path(name)
            meta = _read_lock(p) if p.exists() else None
            if meta and _is_stale(meta, p):
                p.unlink()
                cleared.append(name)
        print(f"Cleared stale locks: {cleared}")
        return 0
    print("usage: python -m shared.vb_lock [status|clear]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
