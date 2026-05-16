"""FOLLOW Token bucket rate gate (Codex 5/16 review 反映 v3 - REJECT 7件全反映).

【背景】 Codex 5/16 5/8/9回目 review:
> 「起動 = action」を切り離せ. token bucket で 日次/時間上限を強制し、
>  トークン枯渇時は即時ノーオペ終了せよ. fail-open NG.

【v3 修正 (Codex 9回目 REJECT 反映)】
1. Windows ロック: open 後 truncate(1) で 1 byte 確保し seek(0) → 全プロセス同一位置 lock
2. ENV パースを lazy (import 時例外 NG → 呼び出し時に GateError)
3. exit code は呼び出し側で一貫管理 (follow_via_seeds の責務)
4. heartbeat の problem=True を異常系で明示
5. 日次境界を JST 固定 (zoneinfo Asia/Tokyo)
6. test 拡充: daily cap, ENV 不正, Windows lock 競合

【exit code (呼び出し側で使用)】
- EXIT_OK = 0
- EXIT_RATE_LIMITED_NOOP = 20
- EXIT_GATE_INIT_ERROR = 21
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except ImportError:
    JST = timezone(timedelta(hours=9))  # fallback

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = REPO_ROOT / "state" / "follow_rate_state.json"
LOCK_PATH = REPO_ROOT / "state" / "follow_rate_state.lock"

# default 上限 (Codex 5/16 review)
_DEFAULTS = {"FOLLOW_DAILY_CAP": 200, "FOLLOW_HOURLY_CAP": 80}


class GateError(Exception):
    """fail-closed signal."""


def _parse_cap_env(name: str, default: int) -> int:
    """ENV を安全 parse. 不正値で GateError raise (呼び出し時)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return int(default)
    try:
        v = int(raw)
    except (ValueError, TypeError) as e:
        raise GateError(f"ENV {name}={raw!r} は整数でない: {e}")
    if v <= 0:
        raise GateError(f"ENV {name}={v} は 0 以下 (fail-closed)")
    return v


def _caps() -> tuple[int, int]:
    """ENV から (daily_cap, hourly_cap) を lazy 取得 (Codex 9回目 #2)."""
    return (
        _parse_cap_env("FOLLOW_DAILY_CAP", _DEFAULTS["FOLLOW_DAILY_CAP"]),
        _parse_cap_env("FOLLOW_HOURLY_CAP", _DEFAULTS["FOLLOW_HOURLY_CAP"]),
    )


def _atomic_write(path: Path, content: str) -> None:
    """atomic write (.tmp → rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextmanager
def _file_lock(timeout_sec: float = 10.0):
    """cross-platform exclusive lock.

    Codex 9回目 #1 修正:
    Windows: open 後 truncate(1) で 1 byte 確保 → seek(0) → 全プロセス同一位置 lock.
    UNLOCK も seek(0) で同位置.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Windows: 1 byte ファイルを保証してから lock
        f = open(LOCK_PATH, "r+b") if LOCK_PATH.exists() and LOCK_PATH.stat().st_size >= 1 else None
        if f is None:
            # 1 byte 初期化
            with open(LOCK_PATH, "wb") as init:
                init.write(b"\0")
            f = open(LOCK_PATH, "r+b")
    else:
        f = open(LOCK_PATH, "a+b")

    deadline = time.time() + timeout_sec
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            while time.time() < deadline:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl
            while time.time() < deadline:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except (OSError, BlockingIOError):
                    time.sleep(0.1)
        if not locked:
            raise GateError(f"file lock timeout ({timeout_sec}s) - 他プロセス保持中?")
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        f.close()


def _load_state_unsafe() -> dict:
    """state 読み込み. 破損 = GateError + .broken backup."""
    if not STATE_PATH.exists():
        return {"events": []}  # epoch seconds (int)
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        bak = STATE_PATH.with_suffix(f".broken.{int(time.time())}.json")
        try:
            STATE_PATH.rename(bak)
        except Exception:
            pass
        raise GateError(f"state 破損 ({e}). backup: {bak.name} → 復旧後再開")
    if not isinstance(d, dict) or "events" not in d or not isinstance(d["events"], list):
        raise GateError(f"state schema 不正: {type(d).__name__}")
    # legacy ISO string → int migrate
    events = []
    for e in d["events"]:
        if isinstance(e, int):
            events.append(e)
        elif isinstance(e, str):
            try:
                events.append(int(datetime.fromisoformat(e).timestamp()))
            except Exception:
                continue
    d["events"] = events
    return d


def _save_state_unsafe(state: dict) -> None:
    _atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))


def _prune_old(events: list[int], now_ts: int, max_age_sec: int = 25 * 3600) -> list[int]:
    cutoff = now_ts - max_age_sec
    return [e for e in events if e >= cutoff]


def _compute_counts(events: list[int], now_ts: int) -> tuple[int, int]:
    """(today_count, last_hour_count) を JST 固定で計算 (Codex 9回目 #5)."""
    one_hour_ago = now_ts - 3600
    # JST day boundary
    now_jst = datetime.fromtimestamp(now_ts, tz=JST)
    day_start_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ts = int(day_start_jst.timestamp())
    today = sum(1 for e in events if e >= day_start_ts)
    last_hour = sum(1 for e in events if e >= one_hour_ago)
    return today, last_hour


def get_status(now: Optional[datetime] = None) -> dict:
    """状態取得 (lazy ENV parse → GateError 可)."""
    daily_cap, hourly_cap = _caps()
    now = now or datetime.now()
    now_ts = int(now.timestamp())
    with _file_lock():
        state = _load_state_unsafe()
        state["events"] = _prune_old(state["events"], now_ts)
        today, last_hour = _compute_counts(state["events"], now_ts)
    return {
        "now": now.isoformat(timespec="seconds"),
        "today": today,
        "last_hour": last_hour,
        "daily_cap": daily_cap,
        "hourly_cap": hourly_cap,
        "daily_remaining": max(0, daily_cap - today),
        "hourly_remaining": max(0, hourly_cap - last_hour),
        "can_follow": today < daily_cap and last_hour < hourly_cap,
    }


def can_follow(now: Optional[datetime] = None) -> bool:
    return get_status(now)["can_follow"]


def record_follow(now: Optional[datetime] = None) -> dict:
    """1件 follow 成功時 token 消費 (lock 内 recheck)."""
    daily_cap, hourly_cap = _caps()
    now = now or datetime.now()
    now_ts = int(now.timestamp())
    with _file_lock():
        state = _load_state_unsafe()
        state["events"] = _prune_old(state["events"], now_ts)
        today, last_hour = _compute_counts(state["events"], now_ts)
        if today >= daily_cap or last_hour >= hourly_cap:
            raise GateError(
                f"record_follow: cap reached in lock (today={today}/{daily_cap}, "
                f"hour={last_hour}/{hourly_cap})"
            )
        state["events"].append(now_ts)
        _save_state_unsafe(state)
        today2, last_hour2 = _compute_counts(state["events"], now_ts)
    return {
        "now": now.isoformat(timespec="seconds"),
        "today": today2,
        "last_hour": last_hour2,
        "daily_cap": daily_cap,
        "hourly_cap": hourly_cap,
        "daily_remaining": max(0, daily_cap - today2),
        "hourly_remaining": max(0, hourly_cap - last_hour2),
        "can_follow": today2 < daily_cap and last_hour2 < hourly_cap,
    }


def reset_state() -> None:
    """テスト/手動 reset."""
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    if LOCK_PATH.exists():
        try:
            LOCK_PATH.unlink()
        except Exception:
            pass


def update_heartbeat(phase: str, problem: bool = False, **fields) -> None:
    """follow_runtime_state.json を atomic update (Codex 9回目 #4: problem 引数).

    Args:
        phase: heartbeat_phase に書く文字列
        problem: True で異常系 (gate_init_error 等). default False.
        **fields: 追加フィールド (success_so_far 等)
    """
    hb_path = REPO_ROOT / "state" / "follow_runtime_state.json"
    existing = {}
    if hb_path.exists():
        try:
            existing = json.loads(hb_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.setdefault("schema_version", 1)
    existing["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing.setdefault("follow", {})
    existing["follow"].update({
        "vm_running": True,
        "heartbeat_phase": phase,
        "heartbeat_age_sec": 0,
        "log_age_min": 0,
        "problem": problem,
        **fields,
    })
    _atomic_write(hb_path, json.dumps(existing, ensure_ascii=False, indent=2))


# Exit codes (Codex 8回目 #9 + 10回目 #1: partial 区別で虚偽成功排除)
EXIT_OK = 0
EXIT_RATE_LIMITED_NOOP = 20     # 開始時に既に cap → 1件も follow せず終了
EXIT_RATE_LIMITED_PARTIAL = 22  # mid/record で cap 到達 (success>0 でも非0で報告)
EXIT_GATE_INIT_ERROR = 21       # ENV/state 異常で開始不可


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        s = get_status()
        print(f"FOLLOW rate gate status:")
        for k, v in s.items():
            print(f"  {k}: {v}")
        if s["can_follow"]:
            print(f"\n=> can follow ({s['hourly_remaining']}/h, {s['daily_remaining']}/day remaining)")
        else:
            print(f"\n=> RATE LIMITED")
    except GateError as e:
        print(f"GateError (fail-closed): {e}", file=sys.stderr)
        sys.exit(EXIT_GATE_INIT_ERROR)
