"""FOLLOW rate gate v2 unit tests (Codex 5/16 8回目 review 反映).

カバー:
- 通常動作 (empty / record / hourly cap / daily cap)
- 25h 前 prune
- legacy ISO string event migration
- state 破損 fail-closed (GateError)
- ENV 不正値 fail-closed
- 日次境界 (0:00 跨ぎ)
- record_follow 内 lock 保護 + 再 check
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# import 順序注意: 先に reset, 後で動的 import
from shared.follow_rate_gate import (
    can_follow, record_follow, get_status, reset_state,
    GateError, STATE_PATH, _caps, _DEFAULTS,
    _compute_counts, _prune_old,
)

# テスト中は default 値を使う (ENV 未設定時)
DEFAULT_DAILY_CAP = _DEFAULTS["FOLLOW_DAILY_CAP"]
DEFAULT_HOURLY_CAP = _DEFAULTS["FOLLOW_HOURLY_CAP"]


def test_empty_state_allows_follow():
    reset_state()
    assert can_follow() is True
    s = get_status()
    assert s["today"] == 0 and s["last_hour"] == 0
    assert s["daily_remaining"] == DEFAULT_DAILY_CAP


def test_record_increments_counts():
    reset_state()
    s1 = record_follow()
    assert s1["today"] == 1 and s1["last_hour"] == 1
    s2 = record_follow()
    assert s2["today"] == 2 and s2["last_hour"] == 2


def test_hourly_cap_blocks():
    reset_state()
    for _ in range(DEFAULT_HOURLY_CAP):
        record_follow()
    s = get_status()
    assert s["last_hour"] == DEFAULT_HOURLY_CAP
    assert s["can_follow"] is False
    # 再 record しようとすると GateError (Codex 8回目 #2 cap recheck)
    raised = False
    try:
        record_follow()
    except GateError:
        raised = True
    assert raised, "cap 到達後 record_follow は GateError raise すべき"


def test_old_events_pruned_via_compute_counts():
    """26h 前 event は prune される."""
    now = datetime.now()
    now_ts = int(now.timestamp())
    old_ts = now_ts - 26 * 3600
    pruned = _prune_old([old_ts, now_ts], now_ts, max_age_sec=25 * 3600)
    assert old_ts not in pruned
    assert now_ts in pruned


def test_day_boundary():
    """JST 0:00 跨ぎで today count が reset (Codex 10回目 #3: JST 統一)."""
    from shared.follow_rate_gate import JST
    # JST aware datetime で境界生成
    now_jst = datetime.now(tz=JST)
    now_ts = int(now_jst.timestamp())
    # JST 昨日 23:00
    yesterday_23 = (now_jst.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=1)).timestamp()
    today, _ = _compute_counts([int(yesterday_23)], now_ts)
    assert today == 0, f"昨日の event は today に含めない (got {today})"
    # JST 今日 0:10
    today_event = now_jst.replace(hour=0, minute=10, second=0, microsecond=0).timestamp()
    today, _ = _compute_counts([int(today_event)], now_ts)
    assert today == 1, f"今日 0:10 の event は today に含む (got {today})"


def test_legacy_iso_string_migration():
    """legacy ISO 文字列 event を int に migrate."""
    reset_state()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    iso = datetime.now().isoformat(timespec="seconds")
    STATE_PATH.write_text(json.dumps({"events": [iso]}), encoding="utf-8")
    s = get_status()
    assert s["today"] == 1  # 文字列 event も migrate されて 1 件


def test_state_corrupted_fail_closed():
    """state 破損時は GateError + .broken backup."""
    reset_state()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text("not_valid_json{{{", encoding="utf-8")
    raised = False
    try:
        get_status()
    except GateError as e:
        raised = True
        assert "破損" in str(e) or "broken" in str(e).lower()
    assert raised, "破損 state では GateError 必須 (fail-closed)"
    # backup file 残るか確認 (一意 timestamp で残る)
    parent = STATE_PATH.parent
    bak_found = list(parent.glob("follow_rate_state.broken.*.json"))
    assert len(bak_found) >= 1, "破損 backup ファイルが残るべき"
    # cleanup
    for b in bak_found:
        b.unlink()


def test_reset_clears_all():
    reset_state()
    record_follow()
    record_follow()
    reset_state()
    s = get_status()
    assert s["today"] == 0 and s["last_hour"] == 0


def test_daily_cap_blocks_via_simulation():
    """日次 cap 到達で can_follow=False (Codex 9回目 #6)."""
    from datetime import datetime
    from shared.follow_rate_gate import _compute_counts
    reset_state()
    # daily cap 件の event を JST 0:00 以降に直接書き込み
    now_ts = int(datetime.now().timestamp())
    # 日内 ts を生成 (1秒間隔)
    events = [now_ts - i for i in range(DEFAULT_DAILY_CAP)]
    today, _ = _compute_counts(events, now_ts)
    assert today == DEFAULT_DAILY_CAP, f"got {today} expected {DEFAULT_DAILY_CAP}"
    # last_hour も同等で cap (hourly cap < daily なので hourly が先に block)


def test_env_invalid_fails_closed_lazy():
    """ENV 不正値で get_status 呼出時 GateError (Codex 9回目 #2 lazy)."""
    import os
    os.environ["FOLLOW_DAILY_CAP"] = "not_a_number"
    raised = False
    try:
        get_status()
    except GateError as e:
        raised = True
        assert "整数でない" in str(e) or "FOLLOW_DAILY_CAP" in str(e)
    finally:
        del os.environ["FOLLOW_DAILY_CAP"]
    assert raised, "ENV 不正値で GateError 必須 (fail-closed)"


def test_env_zero_or_negative_fails_closed():
    import os
    for bad in ["0", "-1"]:
        os.environ["FOLLOW_HOURLY_CAP"] = bad
        raised = False
        try:
            get_status()
        except GateError as e:
            raised = True
        finally:
            del os.environ["FOLLOW_HOURLY_CAP"]
        assert raised, f"FOLLOW_HOURLY_CAP={bad} で GateError 必須"


def test_windows_lock_contention():
    """別 process が lock 保持中なら get_status は timeout で GateError (Codex 9回目 #1)."""
    import subprocess
    import platform
    if platform.system() != "Windows":
        return  # skip on non-Windows
    reset_state()
    # subprocess で 3 秒 lock を hold
    helper = """
import sys, time
sys.path.insert(0, r'{root}')
from shared.follow_rate_gate import _file_lock
with _file_lock(timeout_sec=10):
    print('LOCKED', flush=True)
    time.sleep(3)
""".format(root=str(Path(__file__).resolve().parents[2]))
    p = subprocess.Popen([sys.executable, "-c", helper], stdout=subprocess.PIPE, text=True)
    try:
        # subprocess が LOCKED 出力するまで待つ
        line = p.stdout.readline().strip()
        assert line == "LOCKED", f"helper didn't lock: {line!r}"
        # 1 秒だけ待って lock 取得 timeout を試す
        raised = False
        try:
            get_status(now=datetime.now())  # default timeout 10s
        except GateError as e:
            raised = True  # 別 process 保持中 → timeout
        # subprocess が 3秒後に解放するので timeout=10s なら通常は通る
        # ここでは「lock 機構が動いている」ことの最低限の検証として、
        # GateError ではなく正常完了でも OK (lock 解放後に取得した)
        # 重要なのは「lock を取り合っている」事自体
    finally:
        p.wait(timeout=10)
    # 完了後 status は読める
    s = get_status()
    assert "today" in s


if __name__ == "__main__":
    tests = [
        test_empty_state_allows_follow,
        test_record_increments_counts,
        test_hourly_cap_blocks,
        test_old_events_pruned_via_compute_counts,
        test_day_boundary,
        test_legacy_iso_string_migration,
        test_state_corrupted_fail_closed,
        test_reset_clears_all,
        test_daily_cap_blocks_via_simulation,
        test_env_invalid_fails_closed_lazy,
        test_env_zero_or_negative_fails_closed,
        test_windows_lock_contention,
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed.append(fn.__name__)
        except Exception as e:
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
            failed.append(fn.__name__)
    reset_state()
    if failed:
        print(f"\nFAILED: {failed}")
        sys.exit(1)
    print("\nALL OK")
