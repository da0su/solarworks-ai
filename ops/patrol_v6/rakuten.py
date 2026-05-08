#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 5: 楽天 API 層 (login_status / rate_limit)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGIN_FLAG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "login_expired_flag.json"


def check() -> dict:
    alerts: List[dict] = []

    # login_expired_flag check
    # 2026-05-08: stale flag (>6h) は無視 (5/6 のフラグが Slack 連発の真因)
    if LOGIN_FLAG.exists():
        try:
            data = json.loads(LOGIN_FLAG.read_text(encoding="utf-8"))
            mode = data.get("mode", "follow")
            ts_str = data.get("ts", "")
            stale = False
            if ts_str:
                try:
                    from datetime import datetime, timedelta
                    ts = datetime.fromisoformat(ts_str.replace("Z", ""))
                    if datetime.now() - ts > timedelta(hours=6):
                        stale = True
                except Exception:
                    pass
            if not stale:
                alerts.append({
                    "level": "CRITICAL",
                    "message": f"login_expired_flag set ({mode})",
                    "auto_recover": "escalate_ceo",
                    "context": {"mode": mode, "summary": f"楽天ROOM {mode} ログイン失効"},
                })
            else:
                # stale: 自動削除して以降誤検知を止める
                try:
                    LOGIN_FLAG.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    # 各 mode の cooldown ファイル check
    for mode in ["post", "like", "follow", "followback"]:
        cd = REPO_ROOT / "state" / f"cooldown_{mode}.json"
        if cd.exists():
            try:
                data = json.loads(cd.read_text(encoding="utf-8"))
                from datetime import datetime, timedelta
                started = datetime.fromisoformat(data["started_at"])
                duration = data.get("duration_min", 90)
                ends_at = started + timedelta(minutes=duration)
                if datetime.now() < ends_at:
                    remaining = (ends_at - datetime.now()).total_seconds() / 60
                    alerts.append({"level": "INFO",
                                  "message": f"{mode} cooldown active ({remaining:.0f}min left)"})
            except Exception:
                pass

    return {"layer": "L5_rakuten", "status": "ok" if not alerts else "alert", "alerts": alerts}
