"""日次目標 自動ペース調整 (CEO 5/14 指示 SSOT).

【背景】 CEO 2026-05-14:
> 目標が達成できているかどうか。本来は多すぎても少なすぎても NG。
> 目標とずれがあるのであれば、自動で是正。改善するのが仕事。

【設計】 ある時点 (now) で:
- target  = スプシの本日目標
- actual  = 当日の実績
- elapsed = 当日経過時間
- 期待値 expected = target × (elapsed / 24h)
- 残り時間 remaining = 24h - elapsed
- 残り目標 remain_target = target - actual

判定:
- actual >= target → 「停止」(目標 100% 到達 = それ以上は NG)
- 残り時間 0 → 「停止」(時間切れ)
- remain_target / 残り時間 = 残り pace 目標

各 bot は走る前にこれを呼んで、適切な per-cycle target を取得.

使い方:
    from shared.daily_pacer import get_pace_directive
    d = get_pace_directive("FOLLOW")
    # d = {"action": "run" | "stop", "remain_target": int, "per_cycle_target": int, "pace_per_hour": float}
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Trigger cycle 設定 (per function 1日に走る回数)
CYCLES_PER_DAY = {
    "POST":   4,   # Batch1/2/3/4 (約 6h 毎)
    "FOLLOW": 96,  # 15min × 96
    "LIKE":   48,  # 30min × 48
    "FB":     96,  # 15min × 96 (但し source 駆動)
}

# 許容バッファ (over-shoot 防止)
SAFETY_BUFFER = {
    "POST":   0,    # POST は厳密 (over NG)
    "FOLLOW": 0,
    "LIKE":   0,
    "FB":     0,
}


def _load_ssot_targets() -> dict:
    """スプシから本日の目標値取得 (cache 経由)."""
    cache = REPO_ROOT / "state" / "daily_targets_ssot.json"
    today = datetime.now().strftime("%Y-%m-%d")
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data.get("targets", {})
        except Exception:
            pass
    # cache miss → live fetch
    try:
        from ops.notifications.dashboard_report import _load_ssot_targets as _impl
        return _impl() or {}
    except Exception:
        return {}


def _get_actuals(fn: str) -> int:
    """当日実績数 (function 別)."""
    today = datetime.now().strftime("%Y-%m-%d")
    if fn.upper() == "POST":
        try:
            db = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            r = c.execute(
                "SELECT COUNT(*) FROM post_queue WHERE status='posted' AND posted_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
            c.close()
            return int(r[0]) if r else 0
        except Exception:
            return 0
    elif fn.upper() == "FOLLOW":
        try:
            from shared.follow_history_reader import count_real_follows_on
            return count_real_follows_on(today)
        except Exception:
            return 0
    elif fn.upper() == "LIKE":
        try:
            hist = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json").read_text(encoding="utf-8"))
            return sum(1 for h in hist if str(h.get("liked_at", "")).startswith(today))
        except Exception:
            return 0
    elif fn.upper() == "FB":
        try:
            db = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            r = c.execute(
                "SELECT COUNT(*) FROM follow_log WHERE action='followback' AND DATE(followed_at)=DATE('now','localtime')"
            ).fetchone()
            c.close()
            return int(r[0]) if r else 0
        except Exception:
            return 0
    return 0


def get_pace_directive(fn: str, now: datetime | None = None) -> dict:
    """function 別 ペース指示を返す.

    Args:
        fn: 'POST', 'FOLLOW', 'LIKE', 'FB'
        now: 現在時刻 (テスト用、省略時は datetime.now())

    Returns:
        {
            "fn": str,
            "target": int,        # スプシ本日目標
            "actual": int,        # 当日実績
            "elapsed_h": float,   # 当日経過時間 (h)
            "remain_h": float,    # 当日残り時間 (h)
            "remain_target": int, # 残り達成必要数
            "expected_now": int,  # この時刻の期待値
            "pace_per_hour": float,    # 残り時間 で 1h あたり必要数
            "per_cycle_target": int,   # 1cycle で目標とすべき数
            "action": "run" | "stop",  # 推奨アクション
            "reason": str,
        }
    """
    now = now or datetime.now()
    targets = _load_ssot_targets()
    target_map = {"POST": "post", "FOLLOW": "follow", "LIKE": "like", "FB": "followback"}
    target = int(targets.get(target_map[fn.upper()], 0))
    actual = _get_actuals(fn)
    buffer = SAFETY_BUFFER.get(fn.upper(), 0)

    # day boundaries
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    elapsed_h = (now - day_start).total_seconds() / 3600
    remain_h = max((day_end - now).total_seconds() / 3600, 0.001)

    expected_now = int(target * (elapsed_h / 24))
    remain_target = max(target - actual - buffer, 0)
    pace_per_hour = remain_target / remain_h
    cycles_remain = max(1, int(CYCLES_PER_DAY.get(fn.upper(), 4) * remain_h / 24))
    per_cycle_target = max(1, int(remain_target / cycles_remain))

    result = {
        "fn": fn.upper(), "target": target, "actual": actual,
        "elapsed_h": round(elapsed_h, 2), "remain_h": round(remain_h, 2),
        "remain_target": remain_target,
        "expected_now": expected_now,
        "pace_per_hour": round(pace_per_hour, 2),
        "per_cycle_target": per_cycle_target,
    }

    if target <= 0:
        result["action"] = "stop"
        result["reason"] = "target=0 (no target set in spreadsheet)"
    elif actual >= target:
        result["action"] = "stop"
        result["reason"] = f"target_reached: actual {actual} >= target {target} (over-shoot prevention)"
    elif remain_h < 0.05:
        result["action"] = "stop"
        result["reason"] = "day_ended"
    else:
        result["action"] = "run"
        # 進捗状況の説明
        if actual < expected_now * 0.7:
            result["reason"] = f"behind: actual {actual} vs expected {expected_now} (catch up needed)"
        elif actual > expected_now * 1.3:
            result["reason"] = f"ahead: actual {actual} vs expected {expected_now} (slow down)"
        else:
            result["reason"] = f"on_track: actual {actual} vs expected {expected_now}"
    return result


if __name__ == "__main__":
    for fn in ["POST", "FOLLOW", "LIKE", "FB"]:
        d = get_pace_directive(fn)
        print(f"=== {fn} ===")
        for k, v in d.items():
            print(f"  {k}: {v}")
        print()
