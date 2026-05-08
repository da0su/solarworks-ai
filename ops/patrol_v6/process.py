#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 4: Process 層 (VM HTTP server alive)."""
from __future__ import annotations

from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_DONE_MARKER = REPO_ROOT / "ops" / "vm_v6" / ".setup_done"


def check() -> dict:
    alerts: List[dict] = []
    info: dict = {}

    # 2026-05-08: Plan v6 VM 内 setup が未完了 (.setup_done 不在) なら
    # VM HTTP server を期待しない (Plan v6 deploy 待ちの状況で誤 CRITICAL を防ぐ)。
    # setup 完了後のみ alive を要求する。
    if not SETUP_DONE_MARKER.exists():
        info["vm_http_alive"] = "n/a (Plan v6 setup not deployed)"
        info["note"] = "skip_check_pending_v6_setup"
        return {"layer": "L4_process", "status": "ok",
                "alerts": [], "info": info}

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
