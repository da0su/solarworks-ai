#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 3: Chrome 層 (4 profile 健全性)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List


PROFILE_BASE = Path(r"C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\data")


def check_profile(action: str) -> List[dict]:
    alerts: List[dict] = []
    profile = PROFILE_BASE / f"chrome_profile_{action}"

    if not profile.exists():
        alerts.append({"level": "CRITICAL", "message": f"profile {action} dir missing",
                      "context": {"mode": action}})
        return alerts

    cookies = profile / "Default" / "Network" / "Cookies"
    if not cookies.exists():
        alerts.append({"level": "CRITICAL", "message": f"profile {action} cookies missing",
                      "context": {"mode": action}})
        return alerts

    # SingletonLock 残置
    for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        lp = profile / lock_name
        if lp.exists():
            try:
                age_min = (time.time() - lp.stat().st_mtime) / 60
                if age_min > 30:
                    alerts.append({"level": "WARN",
                                  "message": f"{action} stale {lock_name} ({age_min:.0f}min)",
                                  "auto_recover": "chrome_profile_unlock",
                                  "context": {"mode": action}})
            except Exception:
                pass

    # cookie 鮮度
    try:
        c_age_days = (time.time() - cookies.stat().st_mtime) / 86400
        if c_age_days > 90:
            alerts.append({"level": "WARN",
                          "message": f"{action} cookies {c_age_days:.0f}d old (>90d)"})
    except Exception:
        pass

    return alerts


def check() -> dict:
    all_alerts: List[dict] = []
    for action in ["post", "like", "followback", "follow"]:
        all_alerts.extend(check_profile(action))
    return {"layer": "L3_chrome", "status": "ok" if not all_alerts else "alert", "alerts": all_alerts}
