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


def get_actuals() -> dict:
    """4機能の今日の実績."""
    actuals = {"post": 0, "like": 0, "follow": 0, "followback": 0}
    today = datetime.now().strftime("%Y-%m-%d")

    # POST: room_bot.db
    try:
        c = sqlite3.connect(f"file:{REPO_ROOT / 'rakuten-room' / 'bot' / 'data' / 'room_bot.db'}?mode=ro", uri=True, timeout=2)
        r = c.execute("SELECT COUNT(*) FROM post_queue WHERE status='posted' AND DATE(posted_at)=DATE('now','localtime')").fetchone()
        actuals["post"] = int(r[0]) if r else 0
        c.close()
    except Exception:
        pass

    # LIKE: like_history.json
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json").read_text(encoding="utf-8"))
        actuals["like"] = sum(1 for x in h if str(x.get("liked_at", "")).startswith(today))
    except Exception:
        pass

    # FOLLOW: VM (follow_rpa_log) + HOST (follow_history) 合算
    # 2026-05-08: HOST follow_via_seeds.py の実績を加算 (VM のみだと 0 表示誤検知)
    vm_follow = 0
    host_follow = 0
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json").read_text(encoding="utf-8"))
        vm_follow = sum(int(e.get("success", 0)) for e in h
                        if str(e.get("timestamp", "")).startswith(today))
    except Exception:
        pass
    try:
        h = json.loads((REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json").read_text(encoding="utf-8"))
        host_follow = sum(1 for x in h if isinstance(x, dict)
                          and str(x.get("followed_at", "")).startswith(today))
    except Exception:
        pass
    actuals["follow"] = vm_follow + host_follow

    # FB: room_bot_v5.db
    try:
        c = sqlite3.connect(f"file:{REPO_ROOT / 'rakuten-room' / 'bot' / 'data' / 'room_bot_v5.db'}?mode=ro", uri=True, timeout=2)
        r = c.execute("SELECT COUNT(*) FROM follow_log WHERE action='followback' AND DATE(followed_at,'localtime')=DATE('now','localtime')").fetchone()
        actuals["followback"] = int(r[0]) if r else 0
        c.close()
    except Exception:
        pass

    return actuals


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
        actual = actuals.get(mode, 0)
        if not target:
            continue
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
