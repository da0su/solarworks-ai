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


def check() -> dict:
    all_alerts: List[dict] = []
    for mode in ["post", "like", "follow", "followback"]:
        all_alerts.extend(check_heartbeat(mode))
    return {"layer": "L6_session", "status": "ok" if not all_alerts else "alert", "alerts": all_alerts}
