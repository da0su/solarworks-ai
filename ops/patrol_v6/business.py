#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 7: Business 層 (スプシ目標達成率)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]


def get_targets() -> dict:
    """SSOT スプシから目標値取得 (cache 経由)."""
    cache = REPO_ROOT / "state" / "daily_targets_ssot.json"
    today = datetime.now().strftime("%Y-%m-%d")
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data.get("targets", {})
        except Exception:
            pass
    # cache 無効 → dashboard_report.py の SSOT loader 使う
    try:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from ops.notifications.dashboard_report import _load_ssot_targets
        return _load_ssot_targets() or {}
    except Exception:
        return {}


def _as_count(v):
    """実績値を非負 int に正規化。取得不能/不正値は None (判定不能) を返す。"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def get_actuals() -> dict:
    """4機能の今日の実績.

    戻り値は {mode: Optional[int]}。値が None のときは「実績ソースに到達できず
    判定不能」を意味し、0 件とは区別する (0 と読み替えると偽 CRITICAL になる)。
    2026-07-29: 全機能でこの契約に統一 (旧実装は例外時に 0 と読み替えていた)。
    """
    actuals = {"post": None, "like": None, "follow": None, "followback": None}
    errors: dict = {}
    today = datetime.now().strftime("%Y-%m-%d")

    # POST: 公開 ROOM API が真値 (room_status.py と同一ソース)。
    # 2026-07-29 真因修正: 旧実装は room_bot.db の post_queue を数えていたが、
    # Plan v6 の ranking_post (VM) は post_queue を経由しないため常に 0 になり、
    # 実際には投稿できている日でも「post 達成率 0%」CRITICAL を15分毎に出し続けた。
    # 凍結された旧データソースで状況判断しない (禁忌ファイル ルール)。
    # API 到達不能時は None のままにして「0件」と誤断定しない。
    try:
        from ops.room_status import fetch_post_truth
        truth = fetch_post_truth()   # 内部で timeout=10 の API 呼び出し
        # truth が None/空 = API 到達不能。dict が返れば today_posted は 0 も正当値。
        actuals["post"] = _as_count(truth.get("today_posted")) if truth else None
    except Exception as e:
        errors["post"] = f"{type(e).__name__}: {e}"[:120]

    # LIKE: like_history.json
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json").read_text(encoding="utf-8"))
        actuals["like"] = sum(1 for x in h if str(x.get("liked_at", "")).startswith(today))
    except Exception as e:
        errors["like"] = f"{type(e).__name__}: {e}"[:120]

    # FOLLOW: VM (follow_rpa_log) + HOST (follow_history) 合算
    # 2026-05-08: HOST follow_via_seeds.py の実績を加算 (VM のみだと 0 表示誤検知)
    # 片方でも読めれば実績として成立する (両方失敗した時のみ判定不能)。
    vm_follow = None
    host_follow = None
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json").read_text(encoding="utf-8"))
        vm_follow = sum(int(e.get("success", 0)) for e in h
                        if str(e.get("timestamp", "")).startswith(today))
    except Exception as e:
        errors["follow_vm"] = f"{type(e).__name__}: {e}"[:120]
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json").read_text(encoding="utf-8"))
        # 2026-05-12 真因修正: skip_discover (再試行回避用) は実フォローではないので除外
        host_follow = sum(1 for x in h if isinstance(x, dict)
                          and str(x.get("followed_at", "")).startswith(today)
                          and x.get("source") != "skip_discover")
    except Exception as e:
        errors["follow_host"] = f"{type(e).__name__}: {e}"[:120]
    # 両方読めた時のみ合算値を信用する。片方だけだと「読めた側が 0 件」のときに
    # 合計 0 → 偽 CRITICAL になりうる (FOLLOW は VM/HOST 双方で実行されるため)。
    # 片方失敗時は、読めた側に実績があれば下限値として採用し (稼働は確実なので
    # 停止判定を避ける)、両方 0 相当なら判定不能に倒す。
    if vm_follow is not None and host_follow is not None:
        actuals["follow"] = vm_follow + host_follow
    elif (vm_follow or 0) + (host_follow or 0) > 0:
        actuals["follow"] = (vm_follow or 0) + (host_follow or 0)   # 部分観測の下限値
    else:
        actuals["follow"] = None   # 片側欠損かつ実績0 = 停止と断定できない

    # FB: room_bot_v5.db
    try:
        c = sqlite3.connect(f"file:{REPO_ROOT / 'rakuten-room' / 'bot' / 'data' / 'room_bot_v5.db'}?mode=ro", uri=True, timeout=2)
        r = c.execute("SELECT COUNT(*) FROM follow_log WHERE action='followback' AND DATE(followed_at)=DATE('now','localtime')").fetchone()
        actuals["followback"] = _as_count(r[0]) if r else None
        c.close()
    except Exception as e:
        errors["followback"] = f"{type(e).__name__}: {e}"[:120]

    if errors:
        actuals["_errors"] = errors   # 復旧判断用に原因を残す (check 側は無視)
    return actuals


# 判定不能が何サイクル続いたら CRITICAL に昇格させるか (patrol は15分毎 = 1時間)
UNKNOWN_STREAK_CRITICAL = 4
_STREAK_F = REPO_ROOT / "state" / "patrol_unknown_streak.json"


def _load_streaks() -> dict:
    try:
        return json.loads(_STREAK_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_streaks(d: dict) -> None:
    try:
        _STREAK_F.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STREAK_F.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STREAK_F)   # atomic write
    except Exception:
        pass


def _bump_unknown_streak(mode: str) -> int:
    d = _load_streaks()
    n = int(d.get(mode, 0)) + 1
    d[mode] = n
    _save_streaks(d)
    return n


def _clear_unknown_streak(mode: str) -> None:
    d = _load_streaks()
    if d.pop(mode, None) is not None:
        _save_streaks(d)


# 各機能ごとの「達成すべき時刻 cutoff」
TIME_CUTOFFS = {
    "post":       8,   # 8時 以降は実績期待
    "like":       15,
    "follow":     21,
    "followback": 19,
}


def check() -> dict:
    alerts: List[dict] = []
    now_h = datetime.now().hour
    targets = get_targets()
    actuals = get_actuals()

    for mode in ["post", "like", "follow", "followback"]:
        target = targets.get(mode, 0)
        actual = actuals.get(mode)
        if not target:
            continue
        # actual is None = 実績ソースに到達できず判定不能。
        # 「取得できない」を「0件」と読み替えると偽 CRITICAL になるので警告に留める。
        # 逆に、取得できて 0 件だった場合は従来通り CRITICAL (本物の停止)。
        if actual is None:
            why = (actuals.get("_errors") or {})
            detail = why.get(mode) or why.get(f"{mode}_vm") or why.get(f"{mode}_host") or "unreachable"
            # 判定不能が続く = 観測ブラインド。単発は WARN だが、継続したら
            # CRITICAL に昇格させる。「見えない」を放置すると 7/16 の16日間
            # サイレント停止と同じことが起きる (ssot_target_missing_outage)。
            streak = _bump_unknown_streak(mode)
            level = "CRITICAL" if streak >= UNKNOWN_STREAK_CRITICAL else "WARN"
            alerts.append({
                "level": level,
                "message": (f"{mode} 実績ソース到達不能 (判定不能"
                            f"{f'・{streak}回連続' if streak > 1 else ''}): {detail}"),
                "context": {"mode": mode, "actual": None, "target": target,
                            "unknown_streak": streak},
            })
            continue
        _clear_unknown_streak(mode)
        achievement = actual / target if target else 0
        cutoff = TIME_CUTOFFS.get(mode, 0)

        # 期待時刻に達成率 50% 未達なら alert
        if now_h >= cutoff and achievement < 0.5:
            level = "CRITICAL" if achievement == 0 else "WARN"
            alerts.append({
                "level": level,
                "message": f"{mode} 達成率 {achievement:.0%} ({actual}/{target}) at {now_h}h (cutoff {cutoff}h)",
                "context": {"mode": mode, "actual": actual, "target": target},
            })

    return {"layer": "L7_biz", "status": "ok" if not alerts else "alert",
            "alerts": alerts, "targets": targets, "actuals": actuals}
