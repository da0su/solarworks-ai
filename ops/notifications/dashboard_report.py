#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase C-6: CEO Slack ダッシュボード (1日3回定時投稿)

【目的】
07:00 / 12:00 / 21:00 に4機能サマリを Slack #web-cyber_marke_clow に自動投稿。
CEO の状況把握コストを削減し、異常を早期に CEO 視野に入れる。

【出力例】
【サイバー定時報告 #朝】2026-05-05 07:00
■ POST   today=145/200 | last 21:13 (10h前)
■ LIKE   today=703/500 | last 06:54 (6m前) [上限達成]
■ FOLLOW today=1842/2000 | last_session 06:30 (30m前) [VM稼働中]
■ FB     today=152/30 | last 07:59 (-) [上限達成]
[Pool] 837件 | [Audit] pass 70.4% / review 29.3% / fail 0.3%
SLO Status: ALL OK / 直近 patrol problem=なし

実行: python ops/notifications/dashboard_report.py [--mode morning|noon|night]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DB_LEGACY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
DB_V5 = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"
SOURCE_ITEMS = REPO_ROOT / "rakuten-room" / "bot" / "data" / "source_items.json"
AUDIT_RESULTS = REPO_ROOT / "rakuten-room" / "bot" / "data" / "audit" / "audit_results.json"
LIKE_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json"
FOLLOW_RPA_LOG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
FOLLOW_RUNTIME_STATE = REPO_ROOT / "state" / "follow_runtime_state.json"


def _readonly(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except Exception:
        return None


def _today_post() -> dict:
    info = {"posted": 0, "last_at": None, "last_age_h": None}
    c = _readonly(DB_LEGACY)
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM post_queue WHERE status='posted' "
                "AND DATE(posted_at)=DATE('now','localtime')"
            ).fetchone()
            info["posted"] = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT posted_at FROM post_queue WHERE status='posted' "
                "ORDER BY posted_at DESC LIMIT 1"
            ).fetchone()
            if r:
                info["last_at"] = r[0]
                dt = datetime.fromisoformat(str(r[0]).replace("Z", ""))
                info["last_age_h"] = round((datetime.now() - dt).total_seconds() / 3600, 1)
        except Exception:
            pass
        c.close()
    return info


def _today_like() -> dict:
    info = {"liked": 0, "last_at": None, "last_age_min": None}
    if not LIKE_HISTORY.exists():
        return info
    try:
        data = json.loads(LIKE_HISTORY.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return info
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_likes = [x for x in data if str(x.get("liked_at", "")).startswith(today_str)]
        info["liked"] = len(today_likes)
        if today_likes:
            last = today_likes[-1].get("liked_at", "")
            info["last_at"] = last
            try:
                dt = datetime.fromisoformat(str(last).replace("Z", ""))
                info["last_age_min"] = round((datetime.now() - dt).total_seconds() / 60, 1)
            except Exception:
                pass
    except Exception:
        pass
    return info


def _today_follow() -> dict:
    info = {"followed": 0, "last_session": None, "last_age_min": None, "vm_state": "unknown"}
    if FOLLOW_RUNTIME_STATE.exists():
        try:
            state = json.loads(FOLLOW_RUNTIME_STATE.read_text(encoding="utf-8"))
            f = state.get("follow", {})
            info["vm_state"] = "running" if f.get("vm_running") else "stopped"
            info["login_status"] = f.get("login_status", "?")
            info["heartbeat_age_sec"] = f.get("heartbeat_age_sec")
        except Exception:
            pass
    if FOLLOW_RPA_LOG.exists():
        try:
            data = json.loads(FOLLOW_RPA_LOG.read_text(encoding="utf-8"))
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_sessions = [x for x in data if str(x.get("timestamp", "")).startswith(today_str)]
            info["followed"] = sum(int(x.get("success", 0)) for x in today_sessions)
            info["sessions_today"] = len(today_sessions)
            if today_sessions:
                last = today_sessions[-1].get("timestamp")
                info["last_session"] = last
                try:
                    dt = datetime.fromisoformat(str(last).replace("Z", ""))
                    info["last_age_min"] = round((datetime.now() - dt).total_seconds() / 60, 1)
                except Exception:
                    pass
        except Exception:
            pass
    return info


def _today_followback() -> dict:
    info = {"followback": 0, "pending": 0, "last_at": None, "last_age_min": None}
    c = _readonly(DB_V5)
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM follow_log "
                "WHERE action='followback' AND DATE(followed_at,'localtime')=DATE('now','localtime')"
            ).fetchone()
            info["followback"] = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute("SELECT COUNT(*) FROM followback_queue WHERE status='pending'").fetchone()
            info["pending"] = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            r = c.execute(
                "SELECT followed_at FROM follow_log "
                "WHERE action='followback' ORDER BY followed_at DESC LIMIT 1"
            ).fetchone()
            if r:
                info["last_at"] = r[0]
                dt = datetime.fromisoformat(str(r[0]).replace("Z", ""))
                info["last_age_min"] = round((datetime.now() - dt).total_seconds() / 60, 1)
        except Exception:
            pass
        c.close()
    return info


def _pool_audit_info() -> dict:
    info = {"pool": 0, "audit": {}}
    if SOURCE_ITEMS.exists():
        try:
            data = json.loads(SOURCE_ITEMS.read_text(encoding="utf-8"))
            info["pool"] = len(data) if isinstance(data, list) else 0
        except Exception:
            pass
    if AUDIT_RESULTS.exists():
        try:
            audit_data = json.loads(AUDIT_RESULTS.read_text(encoding="utf-8"))
            if isinstance(audit_data, list):
                from collections import Counter
                cnt = Counter(d.get("audit_result", "unknown") for d in audit_data)
                total = sum(cnt.values())
                if total > 0:
                    info["audit"] = {
                        "pass_pct": round(cnt.get("pass", 0) * 100 / total, 1),
                        "review_pct": round(cnt.get("review", 0) * 100 / total, 1),
                        "fail_pct": round(cnt.get("fail", 0) * 100 / total, 1),
                    }
        except Exception:
            pass
    return info


def build_report(mode: str = "noon") -> str:
    """4機能サマリレポートを生成する."""
    now = datetime.now()
    mode_label = {
        "morning": "朝",
        "noon": "昼",
        "night": "夜",
    }.get(mode, mode)

    post = _today_post()
    like = _today_like()
    follow = _today_follow()
    fb = _today_followback()
    pool = _pool_audit_info()

    lines = [
        f"【サイバー定時報告 #{mode_label}】 {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"■ POST   today={post['posted']}/200 | "
        f"last {post['last_at'] or '-'} ({post['last_age_h'] or '-'}h前)",
        f"■ LIKE   today={like['liked']}/500 | "
        f"last {like['last_at'] or '-'} ({like['last_age_min'] or '-'}m前)",
        f"■ FOLLOW today={follow['followed']}/2000 | "
        f"sessions={follow.get('sessions_today', 0)} | "
        f"VM={follow['vm_state']} | login={follow.get('login_status', '?')} | "
        f"hb_age={follow.get('heartbeat_age_sec', '?')}s",
        f"■ FB     today={fb['followback']}/30 | "
        f"pending={fb['pending']} | last {fb['last_at'] or '-'} ({fb['last_age_min'] or '-'}m前)",
        "",
    ]
    if pool.get("audit"):
        a = pool["audit"]
        lines.append(
            f"[Pool] {pool['pool']}件 | [Audit] pass {a['pass_pct']}% / "
            f"review {a['review_pct']}% / fail {a['fail_pct']}%"
        )
    else:
        lines.append(f"[Pool] {pool['pool']}件")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="CEO Slack ダッシュボード")
    parser.add_argument("--mode", default="noon", choices=["morning", "noon", "night"])
    parser.add_argument("--dry-run", action="store_true", help="Slack送信せず stdout のみ")
    args = parser.parse_args()

    report = build_report(args.mode)
    print(report)

    if args.dry_run:
        return 0

    try:
        sys.path.insert(0, str(REPO_ROOT / "ops" / "notifications"))
        import slack_reporter
        slack_reporter.post_message(report)
        print("\n[OK] sent to Slack")
    except Exception as e:
        print(f"\n[ERR] slack send failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
