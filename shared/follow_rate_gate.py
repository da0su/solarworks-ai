"""FOLLOW Token bucket rate gate (v4 - Codex 29回目 #4 反映 CEO 5/20 1095/day 設計).

【背景】 Codex 29回目 review (CEO 5/20 02:30):
> 「FOLLOW レート制御が日次 200 の fail-closed で完全停止 (達成 18%).
>  日次/時次/分次の多層ガバナンス不在、成功ベース計測なし、リトライ/指数バックオフ不在.
>  1095/日 に到達するための設計が根本的に不足.」

【v4 修正 (Codex 29回目 #4 反映)】
- 日次 cap 200 → 1200 (CEO target 1095 + 余裕 ~10%)
- 時次 cap 80 → 120
- 分次 cap 4 追加 (バースト制御)
- 成功ベース計測 (record_follow() のみ count, 失敗・skip は count しない既存仕様堅持)
- 回路遮断 (circuit breaker): 直近 60s で失敗が threshold 超 → 15min cooldown
- 指数バックオフ提供 (compute_backoff_sec)

【v3 修正 (継続)】
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
- EXIT_RATE_LIMITED_PARTIAL = 22
- EXIT_CIRCUIT_BREAKER = 23  (v4 追加: 回路遮断発動)
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

# default 上限 (Codex 29回目 #4 CEO 5/20: 1095/day 達成設計)
# 旧: 200/80 (Codex 5/16) → 18% 達成 (200/1095) で失敗
# 新: 1200/120/4 (日次/時次/分次. minute cap でバースト制御)
_DEFAULTS = {
    "FOLLOW_DAILY_CAP": 1200,
    "FOLLOW_HOURLY_CAP": 120,
    "FOLLOW_MINUTE_CAP": 4,
    # 回路遮断: 直近 60s で失敗が これ以上 → 15min cooldown
    "FOLLOW_CIRCUIT_FAIL_THRESHOLD": 5,
    "FOLLOW_CIRCUIT_COOLDOWN_MIN": 15,
}


class GateError(Exception):
    """fail-closed signal."""

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        """kind: 'rate_cap' | 'circuit' | 'state_corrupt' | 'env_invalid' | 'unknown'.

        Codex 30回目 #4 反映: 文字列マッチで判定する依存を排除. 呼び出し側は
        GateError.kind で構造化判定する.
        """
        super().__init__(message)
        self.kind = kind


def _parse_cap_env(name: str, default: int) -> int:
    """ENV を安全 parse. 不正値で GateError raise (呼び出し時)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return int(default)
    try:
        v = int(raw)
    except (ValueError, TypeError) as e:
        raise GateError(f"ENV {name}={raw!r} は整数でない: {e}", kind="env_invalid")
    if v <= 0:
        raise GateError(f"ENV {name}={v} は 0 以下 (fail-closed)", kind="env_invalid")
    return v


def _caps() -> tuple[int, int, int]:
    """ENV から (daily_cap, hourly_cap, minute_cap) を lazy 取得 (Codex 29回目 #4)."""
    return (
        _parse_cap_env("FOLLOW_DAILY_CAP", _DEFAULTS["FOLLOW_DAILY_CAP"]),
        _parse_cap_env("FOLLOW_HOURLY_CAP", _DEFAULTS["FOLLOW_HOURLY_CAP"]),
        _parse_cap_env("FOLLOW_MINUTE_CAP", _DEFAULTS["FOLLOW_MINUTE_CAP"]),
    )


def _circuit_config() -> tuple[int, int]:
    """回路遮断 設定 (fail_threshold, cooldown_min)."""
    return (
        _parse_cap_env("FOLLOW_CIRCUIT_FAIL_THRESHOLD", _DEFAULTS["FOLLOW_CIRCUIT_FAIL_THRESHOLD"]),
        _parse_cap_env("FOLLOW_CIRCUIT_COOLDOWN_MIN", _DEFAULTS["FOLLOW_CIRCUIT_COOLDOWN_MIN"]),
    )


def compute_backoff_sec(consecutive_fail: int) -> float:
    """連続失敗回数 に応じた 指数バックオフ秒数 (Codex 29回目 #4).

    実装と docstring 揃え (Codex 32回目 #6 反映):
    - 1回目: 1s (2^0)
    - 2回目: 2s (2^1)
    - 3回目: 4s (2^2)
    - 4回目: 8s (2^3)
    - 5回目: 16s (2^4)
    - 6回目以上: 30s cap (2^5=32 超 → cap 30)
    """
    if consecutive_fail <= 0:
        return 0.0
    backoff = min(2 ** (consecutive_fail - 1), 30)
    return float(backoff)


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
            raise GateError(f"file lock timeout ({timeout_sec}s) - 他プロセス保持中?",
                            kind="state_corrupt")
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
        raise GateError(f"state 破損 ({e}). backup: {bak.name} → 復旧後再開",
                        kind="state_corrupt")
    if not isinstance(d, dict) or "events" not in d or not isinstance(d["events"], list):
        raise GateError(f"state schema 不正: {type(d).__name__}", kind="state_corrupt")
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


def _compute_counts(events: list[int], now_ts: int) -> tuple[int, int, int]:
    """(today_count, last_hour_count, last_minute_count) を JST 固定で計算.

    Codex 29回目 #4: minute_count 追加 (バースト制御).
    """
    one_hour_ago = now_ts - 3600
    one_minute_ago = now_ts - 60
    # JST day boundary
    now_jst = datetime.fromtimestamp(now_ts, tz=JST)
    day_start_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ts = int(day_start_jst.timestamp())
    today = sum(1 for e in events if e >= day_start_ts)
    last_hour = sum(1 for e in events if e >= one_hour_ago)
    last_minute = sum(1 for e in events if e >= one_minute_ago)
    return today, last_hour, last_minute


def _is_circuit_open(state: dict, now_ts: int) -> tuple[bool, int]:
    """回路遮断中か (cooldown_expires_at まで).

    Returns: (is_open, remaining_sec)
    """
    expires = state.get("circuit_breaker_expires_at", 0)
    if expires and now_ts < expires:
        return True, int(expires - now_ts)
    return False, 0


def get_status(now: Optional[datetime] = None) -> dict:
    """状態取得 (lazy ENV parse → GateError 可).

    Codex 29回目 #4: minute_cap + circuit_breaker 状態を返す.
    """
    daily_cap, hourly_cap, minute_cap = _caps()
    now = now or datetime.now()
    now_ts = int(now.timestamp())
    with _file_lock():
        state = _load_state_unsafe()
        state["events"] = _prune_old(state["events"], now_ts)
        today, last_hour, last_minute = _compute_counts(state["events"], now_ts)
        circuit_open, circuit_remaining = _is_circuit_open(state, now_ts)
    return {
        "now": now.isoformat(timespec="seconds"),
        "today": today,
        "last_hour": last_hour,
        "last_minute": last_minute,
        "daily_cap": daily_cap,
        "hourly_cap": hourly_cap,
        "minute_cap": minute_cap,
        "daily_remaining": max(0, daily_cap - today),
        "hourly_remaining": max(0, hourly_cap - last_hour),
        "minute_remaining": max(0, minute_cap - last_minute),
        "circuit_open": circuit_open,
        "circuit_remaining_sec": circuit_remaining,
        # can_follow: 全 cap + circuit 解放
        "can_follow": (
            today < daily_cap
            and last_hour < hourly_cap
            and last_minute < minute_cap
            and not circuit_open
        ),
    }


def can_follow(now: Optional[datetime] = None) -> bool:
    return get_status(now)["can_follow"]


def record_follow(now: Optional[datetime] = None) -> dict:
    """1件 follow 成功時 token 消費 (lock 内 recheck).

    Codex 29回目 #4: minute_cap + circuit_breaker check 追加.
    """
    daily_cap, hourly_cap, minute_cap = _caps()
    now = now or datetime.now()
    now_ts = int(now.timestamp())
    with _file_lock():
        state = _load_state_unsafe()
        state["events"] = _prune_old(state["events"], now_ts)
        today, last_hour, last_minute = _compute_counts(state["events"], now_ts)
        circuit_open, _remaining = _is_circuit_open(state, now_ts)
        if circuit_open:
            raise GateError(
                f"record_follow: circuit breaker open ({_remaining}s remaining)",
                kind="circuit",
            )
        if today >= daily_cap or last_hour >= hourly_cap or last_minute >= minute_cap:
            raise GateError(
                f"record_follow: cap reached in lock (today={today}/{daily_cap}, "
                f"hour={last_hour}/{hourly_cap}, min={last_minute}/{minute_cap})",
                kind="rate_cap",
            )
        state["events"].append(now_ts)
        _save_state_unsafe(state)
        today2, last_hour2, last_minute2 = _compute_counts(state["events"], now_ts)
    return {
        "now": now.isoformat(timespec="seconds"),
        "today": today2,
        "last_hour": last_hour2,
        "last_minute": last_minute2,
        "daily_cap": daily_cap,
        "hourly_cap": hourly_cap,
        "minute_cap": minute_cap,
        "daily_remaining": max(0, daily_cap - today2),
        "hourly_remaining": max(0, hourly_cap - last_hour2),
        "minute_remaining": max(0, minute_cap - last_minute2),
        "can_follow": (today2 < daily_cap and last_hour2 < hourly_cap
                       and last_minute2 < minute_cap),
    }


def record_failure(now: Optional[datetime] = None) -> dict:
    """1件 follow 失敗時 を記録. 直近 60s 失敗が threshold 超で circuit 発動.

    Codex 29回目 #4 反映 (回路遮断).
    Returns:
        {"recent_failures": N, "circuit_triggered": bool, "circuit_expires_at": ts}
    """
    fail_threshold, cooldown_min = _circuit_config()
    now = now or datetime.now()
    now_ts = int(now.timestamp())
    with _file_lock():
        state = _load_state_unsafe()
        # failure events を別配列で管理 (events は成功のみ)
        failures = state.get("failure_events", [])
        # 60s 以上前のものを prune
        cutoff = now_ts - 60
        failures = [t for t in failures if isinstance(t, int) and t >= cutoff]
        failures.append(now_ts)
        state["failure_events"] = failures
        triggered = False
        # Codex 31回目 #5 fix: before/after reset を明示分離
        recent_failures_before_reset = len(failures)
        if recent_failures_before_reset >= fail_threshold:
            # 回路発動: cooldown_min 分 cooldown
            state["circuit_breaker_expires_at"] = now_ts + cooldown_min * 60
            triggered = True
            # 連続失敗 reset (cooldown 後 fresh start)
            state["failure_events"] = []
        recent_failures_after = len(state["failure_events"])
        _save_state_unsafe(state)
    # Codex 31回目 #4 fix: circuit_open フラグも明示 (downstream の混乱回避)
    # Codex 31回目 #5 fix: recent_failures は state 反映後 (after_reset). before_reset は別 key.
    expires_at = state.get("circuit_breaker_expires_at", 0)
    return {
        # state 反映後の値 (triggered なら 0)
        "recent_failures": recent_failures_after,
        "recent_failures_before_reset": recent_failures_before_reset,
        "fail_threshold": fail_threshold,
        "circuit_triggered": triggered,
        "circuit_open": bool(expires_at and now_ts < expires_at),
        "circuit_expires_at": expires_at,
    }


def record_success_resets_failures(now: Optional[datetime] = None) -> None:
    """成功記録時に直近失敗を reset (連続失敗カウンターを 0 に).

    record_follow() と並行して呼ぶ (record_follow 内で自動 reset は副作用避けるため分離).
    """
    with _file_lock():
        state = _load_state_unsafe()
        if state.get("failure_events"):
            state["failure_events"] = []
            _save_state_unsafe(state)


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
EXIT_CIRCUIT_BREAKER = 23       # 回路遮断発動 (Codex 29回目 #4)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        s = get_status()
        print(f"FOLLOW rate gate status (v4 - 1095/day plan):")
        for k, v in s.items():
            print(f"  {k}: {v}")
        if s["can_follow"]:
            print(f"\n=> can follow "
                  f"({s['minute_remaining']}/min, "
                  f"{s['hourly_remaining']}/h, "
                  f"{s['daily_remaining']}/day remaining)")
        elif s.get("circuit_open"):
            print(f"\n=> CIRCUIT BREAKER OPEN "
                  f"({s['circuit_remaining_sec']}s remaining)")
        else:
            print(f"\n=> RATE LIMITED")
    except GateError as e:
        print(f"GateError (fail-closed): {e}", file=sys.stderr)
        sys.exit(EXIT_GATE_INIT_ERROR)
