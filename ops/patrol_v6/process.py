#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 4: Process 層 (VM HTTP server alive)."""
from __future__ import annotations

from typing import List


def check() -> dict:
    alerts: List[dict] = []
    info: dict = {}

    try:
        from ops.vm_v6.vm_controller import is_alive
        alive = is_alive(timeout=2.0)
        info["vm_http_alive"] = alive
        if not alive:
            alerts.append({
                "level": "CRITICAL",
                "message": "VM HTTP server not responding",
                "auto_recover": "vm_http_restart",
            })
    except Exception as e:
        alerts.append({"level": "WARN", "message": f"http check error: {e}"})

    return {"layer": "L4_process", "status": "ok" if not alerts else "alert",
            "alerts": alerts, "info": info}
