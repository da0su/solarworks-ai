#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 1: HOST 層 (Task Scheduler 健全性)."""
from __future__ import annotations

import subprocess
from typing import List

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

REQUIRED_TASKS_V6 = [
    "RoomBot_POST_Batch1", "RoomBot_POST_Batch2", "RoomBot_POST_Batch3",
    "RoomBot_LIKE_Hourly", "RoomBot_FOLLOWBACK_Hourly", "RoomBotFollow_Hourly",
    "RoomBot_Patrol_Hourly", "RoomBot_DailyReset_06",
    "RoomBot_FB_SourceFeed_4h", "RoomBot_Replenish_Daily",
    "RoomBot_TaskHealthcheck_Daily", "RoomBot_SeedScrape_Daily",
    "RoomBot_Dashboard_Morning", "RoomBot_Dashboard_Noon", "RoomBot_Dashboard_Night",
]


def list_existing_tasks() -> set:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            encoding="cp932", errors="replace", creationflags=NO_WIN,
        )
        existing = set()
        for line in result.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",", 2)]
            if not parts: continue
            tn = parts[0].lstrip("\\")
            if tn.startswith("RoomBot"):
                existing.add(tn)
        return existing
    except Exception:
        return set()


def check() -> dict:
    alerts: List[dict] = []

    existing = list_existing_tasks()
    missing = [t for t in REQUIRED_TASKS_V6 if t not in existing]
    if missing:
        if len(missing) >= 3:
            alerts.append({"level": "CRITICAL", "message": f"{len(missing)} required tasks missing",
                          "context": {"missing": missing}})
        else:
            alerts.append({"level": "WARN", "message": f"{len(missing)} task(s) missing: {missing}"})

    return {"layer": "L1_host", "status": "ok" if not alerts else "alert",
            "alerts": alerts, "task_count": len(existing)}
