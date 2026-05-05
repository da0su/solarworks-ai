#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 2: VM 層 (RoomBot 起動状態 / GuestAdditionsRunLevel)."""
from __future__ import annotations

import subprocess
from typing import List

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"


def check() -> dict:
    alerts: List[dict] = []
    info: dict = {}

    try:
        r = subprocess.run([VBOXMANAGE, "showvminfo", "RoomBot", "--machinereadable"],
                          capture_output=True, text=True, timeout=10, creationflags=NO_WIN)
        if r.returncode != 0:
            alerts.append({"level": "CRITICAL", "message": f"VBoxManage error rc={r.returncode}"})
            return {"layer": "L2_vm", "status": "error", "alerts": alerts}

        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v.strip().strip('"')

        state = info.get("VMState", "?")
        if state != "running":
            alerts.append({
                "level": "CRITICAL",
                "message": f"VM not running (state={state})",
                "auto_recover": "vm_startvm",
            })
        else:
            run_level = info.get("GuestAdditionsRunLevel", "0")
            if int(run_level) < 3:
                alerts.append({"level": "WARN",
                              "message": f"GuestAdditionsRunLevel={run_level} (booting)"})

    except Exception as e:
        alerts.append({"level": "CRITICAL", "message": f"VM check failed: {e}"})

    return {"layer": "L2_vm", "status": "ok" if not alerts else "alert",
            "alerts": alerts, "info": {"state": info.get("VMState"),
                                       "memory": info.get("memory"),
                                       "cpus": info.get("cpus")}}
