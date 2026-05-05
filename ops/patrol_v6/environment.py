#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 0: 環境層 (Disk / Memory / Network)."""
from __future__ import annotations

import shutil
import socket
from typing import List


def check() -> dict:
    alerts: List[dict] = []

    # Disk free
    try:
        free_gb = shutil.disk_usage("C:\\").free / (1024**3)
        if free_gb < 5:
            alerts.append({"level": "CRITICAL", "message": f"disk free {free_gb:.1f}GB < 5GB",
                          "auto_recover": "disk_cleanup"})
        elif free_gb < 10:
            alerts.append({"level": "WARN", "message": f"disk free {free_gb:.1f}GB < 10GB"})
    except Exception as e:
        alerts.append({"level": "WARN", "message": f"disk check failed: {e}"})

    # Memory
    try:
        import psutil
        free_gb = psutil.virtual_memory().available / (1024**3)
        if free_gb < 4:
            alerts.append({"level": "CRITICAL", "message": f"memory free {free_gb:.1f}GB < 4GB"})
        elif free_gb < 8:
            alerts.append({"level": "WARN", "message": f"memory free {free_gb:.1f}GB < 8GB"})
    except ImportError:
        pass  # psutil 未インストール時は skip

    # Network 疎通 (rakuten.co.jp)
    try:
        sock = socket.create_connection(("room.rakuten.co.jp", 443), timeout=5)
        sock.close()
    except Exception as e:
        alerts.append({"level": "WARN", "message": f"rakuten.co.jp unreachable: {e}"})

    return {"layer": "L0_env", "status": "ok" if not alerts else "alert", "alerts": alerts}
