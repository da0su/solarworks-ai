#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patrol_v6 Layer 5: 楽天 API 層 (login_status / rate_limit / cookie expiry)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGIN_FLAG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "login_expired_flag.json"
DATA_DIR = REPO_ROOT / "rakuten-room" / "bot" / "data"

# 認証 cookie 名 (Im + OSSO が最重要)
AUTH_COOKIE_NAMES = {"OSSO", "Im"}

# Chrome 時刻 epoch offset: Jan 1 1601 → Jan 1 1970 = 11644473600 秒
_CHROME_EPOCH_OFFSET = 11644473600


def _check_cookie_expiry(alerts: List[dict]) -> None:
    """chrome_profile の Im / OSSO cookie の有効期限を確認する。

    2026-05-24 追加: Im が期限切れになると全 session が 未ログイン になる。
    2026-05-25 修正: Plan v6 cutover 以降、実際の BOT は VM 内の chrome_profile_follow/post/like を使用。
    HOST の chrome_profile は legacy (未使用)。
    → CRITICAL + escalate_ceo は誤報になるため WARN に降格。
    VM ログイン確認は L4 の /healthz で行う。

    - WARN: Im が既に期限切れ (HOST legacy profile の情報として記録)
    - WARN: Im が 7日以内に期限切れ (事前警告)
    """
    profile = DATA_DIR / "chrome_profile"
    cookies_db = profile / "Default" / "Network" / "Cookies"
    if not cookies_db.exists():
        return

    try:
        con = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT name, expires_utc FROM cookies "
                "WHERE name IN ('OSSO', 'Im') AND host_key LIKE '%rakuten%'"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return

    if not rows:
        # HOST legacy profile に cookie がない → VM が担当しているため INFO 扱い
        return

    now = datetime.now()
    for name, exp_utc in rows:
        if exp_utc <= 0:
            continue  # session cookie (expiry 無し) は skip
        # Chrome stored time: microseconds since Jan 1 1601
        exp_ts = (exp_utc / 1_000_000) - _CHROME_EPOCH_OFFSET
        try:
            exp_dt = datetime.utcfromtimestamp(exp_ts)
        except Exception:
            continue
        diff = exp_dt - now
        if diff.total_seconds() < 0:
            # WARN のみ (CRITICAL/escalate_ceo は誤報: 実BOT は VM 内プロファイル使用)
            alerts.append({
                "level": "WARN",
                "message": f"[HOST legacy] cookie {name} が期限切れ (expired={exp_dt.strftime('%Y-%m-%d')}) — VM プロファイルは別途確認",
            })
        elif diff < timedelta(days=7):
            alerts.append({
                "level": "WARN",
                "message": f"[HOST legacy] cookie {name} が{diff.days}日後に期限切れ (expires={exp_dt.strftime('%Y-%m-%d')})",
            })


def check() -> dict:
    alerts: List[dict] = []

    # 2026-05-24 追加: cookie 有効期限チェック
    _check_cookie_expiry(alerts)

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
