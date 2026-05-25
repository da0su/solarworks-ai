#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 6: Session 層 (heartbeat staleness 検知)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]


def check_heartbeat(mode: str) -> List[dict]:
    """1 mode の heartbeat staleness check."""
    alerts: List[dict] = []
    paths = [
        REPO_ROOT / "rakuten-room" / "bot" / "executor" / f"heartbeat_{mode}.json",
        REPO_ROOT / "rakuten-room" / "bot" / "data" / "state" / f"heartbeat_{mode}.json",
        REPO_ROOT / "ops" / "vm_v6" / "data" / f"heartbeat_{mode}.json",
    ]
    if mode == "follow":
        paths.append(REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_heartbeat.json")

    hb = None
    for p in paths:
        if p.exists():
            try:
                hb = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                continue

    if hb is None:
        # heartbeat 不在は session 走っていないだけかも (alert しない)
        return alerts

    try:
        ts = hb.get("ts") or hb.get("updated_at")
        if not ts:
            return alerts
        age_sec = (datetime.now() - datetime.fromisoformat(str(ts).replace("Z", ""))).total_seconds()
        phase = hb.get("phase", "?")
        # shutdown phase なら session は終了済 → alert なし
        if phase == "shutdown":
            return alerts

        # startup が 3h 以上古い場合: バッチ系 (post/like/followback) は
        # shutdown 書き込み漏れでも既に完了している。alert 対象外。
        # (例: POST バッチ 02:06 startup → 9h後に patrol が読んで誤 CRITICAL)
        _STARTUP_GRACE_SEC = 3 * 3600  # 3 時間
        if phase == "startup" and age_sec >= _STARTUP_GRACE_SEC:
            return alerts

        if age_sec >= 300:
            alerts.append({
                "level": "CRITICAL",
                "message": f"{mode} heartbeat stale ({age_sec:.0f}s, phase={phase})",
                "auto_recover": "session_abort",
                "context": {"mode": mode},
            })
        elif age_sec >= 180:
            alerts.append({
                "level": "WARN",
                "message": f"{mode} heartbeat slow ({age_sec:.0f}s, phase={phase})",
            })
    except Exception:
        pass

    return alerts


def check_host_follow_freshness() -> List[dict]:
    """HOST follow_history.json の最終 entry が新しいかチェック.

    2026-05-08: HOST follow (follow_via_seeds.py / follow_executor.py) の
    生存判定に、VM heartbeat の代わりに follow_history.json 最終 entry の age を使う。
    時刻 hour が 9-23 (稼働時間帯) の時のみチェック。
    最終 entry が 90 分以上前 = 何らかの問題で stop している。
    """
    alerts: List[dict] = []
    now = datetime.now()
    if not (9 <= now.hour <= 23):
        return alerts  # 深夜 0-8時は trigger 少ないので skip

    hist_path = REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json"
    if not hist_path.exists():
        return alerts

    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
        today_str = now.strftime("%Y-%m-%d")
        today_entries = [h for h in hist if isinstance(h, dict)
                         and str(h.get("followed_at", "")).startswith(today_str)]
        if not today_entries:
            # 朝 9 時以降で 1 件もない = 完全停止 = CRITICAL
            if now.hour >= 11:
                alerts.append({
                    "level": "CRITICAL",
                    "message": f"HOST follow today=0 at {now.hour}h (動いていない)",
                    "auto_recover": "escalate_ceo",
                    "context": {"summary": "HOST follow が完全停止"},
                })
            return alerts

        last_at = today_entries[-1].get("followed_at", "")
        last_dt = datetime.fromisoformat(str(last_at).replace("Z", ""))
        age_min = (now - last_dt).total_seconds() / 60

        if age_min >= 90:
            alerts.append({
                "level": "CRITICAL",
                "message": f"HOST follow stale {age_min:.0f}min (last={last_at[:19]} count={len(today_entries)})",
                "auto_recover": "escalate_ceo",
                "context": {"summary": f"HOST follow が {age_min:.0f} 分間停止"},
            })
        elif age_min >= 45:
            alerts.append({
                "level": "WARN",
                "message": f"HOST follow slow {age_min:.0f}min (last={last_at[:19]})",
            })
    except Exception:
        pass

    return alerts


def check() -> dict:
    all_alerts: List[dict] = []
    for mode in ["post", "like", "follow", "followback"]:
        all_alerts.extend(check_heartbeat(mode))
    # HOST follow 専用 freshness check (CEO 指示で意味のある patrol)
    all_alerts.extend(check_host_follow_freshness())
    return {"layer": "L6_session", "status": "ok" if not all_alerts else "alert", "alerts": all_alerts}
