"""codex_daily_briefing: _compute_ssot_vs_room_diff (Codex 30回目 #1).

OR 結合の divergent 判定検証.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ops.codex_daily_briefing import _compute_ssot_vs_room_diff


def _mk_ssot(**kw):
    return {"values": kw}


def _mk_room(**kw):
    out = {}
    for k, v in kw.items():
        out[k] = v
    out["_profile_used"] = "post"
    return out


def test_diverged_by_pct_large():
    """大型値で 5% 超 → diverged. threshold = max(100, ceil(ssot*0.05))."""
    ssot = _mk_ssot(cumulative_follow=10000)
    room = _mk_room(follow_count=11000)  # +1000 (10%, > 500 = ceil(10000*0.05))
    r = _compute_ssot_vs_room_diff(ssot, room)
    field = r["by_field"]["follow_count"]
    assert field["status"] == "diverged", field
    # threshold = max(100, 500) = 500
    assert field.get("threshold") == 500


def test_diverged_by_absolute_when_small():
    """小型値: threshold=100 floor. 150件差で diverged."""
    ssot = _mk_ssot(cumulative_follow=200)
    room = _mk_room(follow_count=350)  # +150件
    r = _compute_ssot_vs_room_diff(ssot, room)
    field = r["by_field"]["follow_count"]
    assert field["status"] == "diverged", field
    assert field.get("threshold") == 100  # max(100, ceil(10)) = 100


def test_ok_when_under_threshold():
    """100件以下 かつ 5% 以下 → ok."""
    ssot = _mk_ssot(cumulative_follow=10000)
    room = _mk_room(follow_count=10050)  # +50件 (0.5%, < threshold=500)
    r = _compute_ssot_vs_room_diff(ssot, room)
    field = r["by_field"]["follow_count"]
    assert field["status"] == "ok", field


def test_threshold_ceil_for_small_pct():
    """Codex 31回目 #8: ceil 適用. ssot=2001 で threshold = max(100, 101) = 101."""
    ssot = _mk_ssot(cumulative_follow=2001)
    room = _mk_room(follow_count=2101)  # delta=100 < threshold=101
    r = _compute_ssot_vs_room_diff(ssot, room)
    field = r["by_field"]["follow_count"]
    assert field["status"] == "ok", field
    # 1件超で diverged
    room2 = _mk_room(follow_count=2102)  # delta=101 < threshold=101 (== 同値は ok)
    r2 = _compute_ssot_vs_room_diff(ssot, room2)
    field2 = r2["by_field"]["follow_count"]
    assert field2["status"] == "ok", field2  # > 比較なので 101 はまだ ok
    # 2件超で diverged (102 > 101)
    room3 = _mk_room(follow_count=2103)
    r3 = _compute_ssot_vs_room_diff(ssot, room3)
    field3 = r3["by_field"]["follow_count"]
    assert field3["status"] == "diverged", field3


def test_missing_when_no_ssot():
    ssot = _mk_ssot()
    room = _mk_room(follow_count=100)
    r = _compute_ssot_vs_room_diff(ssot, room)
    field = r["by_field"]["follow_count"]
    assert field["status"] == "missing", field


def test_room_error_propagates():
    """room に _error あれば top-level に echo."""
    r = _compute_ssot_vs_room_diff({"values": {}}, {"_error": "all profiles failed"})
    assert "_error" in r


if __name__ == "__main__":
    import traceback
    tests = [
        test_diverged_by_pct_large,
        test_diverged_by_absolute_when_small,
        test_ok_when_under_threshold,
        test_threshold_ceil_for_small_pct,
        test_missing_when_no_ssot,
        test_room_error_propagates,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception:
            print(f"ERR : {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n=> {passed} pass / {failed} fail")
    sys.exit(0 if failed == 0 else 1)
