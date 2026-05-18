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

# 2026-05-05 礎: SSOT (memory/rakuten_room_targets_ssot.md)
SSOT_SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
SSOT_SHEET_GID = 1447646534
SSOT_CACHE = REPO_ROOT / "state" / "daily_targets_ssot.json"
GSPREAD_CREDS = REPO_ROOT / "credentials" / "sheets_service_account.json"


def _load_ssot_targets(force_refresh: bool = False) -> dict:
    """SSOT スプシから今日の目標値を取得 (cache 6時間).

    Args:
        force_refresh: True なら cache 無視で必ず gspread 取得 (CEO 5/18 朝 briefing 用)

    Returns:
        dict {post, follow, like, followback}
        失敗時は空 dict (呼び側で fallback)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    today_slash = datetime.now().strftime("%Y/%m/%d")

    # Cache check (6h以内なら再利用) - force_refresh なら skip
    if not force_refresh and SSOT_CACHE.exists():
        try:
            cache = json.loads(SSOT_CACHE.read_text(encoding="utf-8"))
            if cache.get("date") == today:
                cache_age = (datetime.now() - datetime.fromisoformat(cache["fetched_at"])).total_seconds()
                if cache_age < 21600:  # 6h
                    return cache.get("targets", {})
        except Exception:
            pass

    # gspread 経由で取得
    targets = {}
    try:
        import gspread
        gc = gspread.service_account(filename=str(GSPREAD_CREDS))
        sh = gc.open_by_key(SSOT_SPREADSHEET_ID)
        ws = next(w for w in sh.worksheets() if w.id == SSOT_SHEET_GID)
        rows = ws.get_all_values()
        for row in rows:
            if not row or not row[0]:
                continue
            if row[0] == today_slash or row[0] == today:
                # B=投稿目標, E=フォロー目標, H=ライク目標, K=フォローバック目標
                def _i(v):
                    try: return int(str(v).replace(",", "").strip())
                    except: return 0
                targets = {
                    "post":       _i(row[1]) if len(row) > 1 else 0,
                    "follow":     _i(row[4]) if len(row) > 4 else 0,
                    "like":       _i(row[7]) if len(row) > 7 else 0,
                    "followback": _i(row[10]) if len(row) > 10 else 0,
                }
                break
        # Cache write
        if targets:
            SSOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SSOT_CACHE.write_text(json.dumps({
                "date": today,
                "fetched_at": datetime.now().isoformat(),
                "targets": targets,
                "source": "gspread:楽天ROOM_デイリーログ",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[ssot] gspread fetch failed: {e}", file=sys.stderr)
    return targets


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

    vm_followed = 0
    host_followed = 0

    # VM 側: follow_rpa_log.json (pyautogui)
    if FOLLOW_RPA_LOG.exists():
        try:
            data = json.loads(FOLLOW_RPA_LOG.read_text(encoding="utf-8"))
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_sessions = [x for x in data if str(x.get("timestamp", "")).startswith(today_str)]
            vm_followed = sum(int(x.get("success", 0)) for x in today_sessions)
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

    # 2026-05-08: HOST 側 follow_history.json も集計 (follow_via_seeds.py / follow_executor.py)
    HOST_FOLLOW_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json"
    if HOST_FOLLOW_HISTORY.exists():
        try:
            hist = json.loads(HOST_FOLLOW_HISTORY.read_text(encoding="utf-8"))
            today_str = datetime.now().strftime("%Y-%m-%d")
            # 2026-05-12 真因修正: skip_discover (再試行回避用記録) は実フォローではないので除外
            today_host = [h for h in hist if isinstance(h, dict)
                           and str(h.get("followed_at", "")).startswith(today_str)
                           and h.get("source") != "skip_discover"]
            host_followed = len(today_host)
            if today_host:
                last_at = today_host[-1].get("followed_at")
                # HOST 系の方が新しければそちらを表示
                try:
                    dt_host = datetime.fromisoformat(str(last_at).replace("Z", ""))
                    age_min = round((datetime.now() - dt_host).total_seconds() / 60, 1)
                    if info["last_age_min"] is None or age_min < info["last_age_min"]:
                        info["last_session"] = last_at
                        info["last_age_min"] = age_min
                except Exception:
                    pass
        except Exception:
            pass

    info["followed"] = vm_followed + host_followed
    info["vm_followed"] = vm_followed
    info["host_followed"] = host_followed
    return info


def _today_followback() -> dict:
    info = {"followback": 0, "pending": 0, "last_at": None, "last_age_min": None}
    c = _readonly(DB_V5)
    if c is not None:
        try:
            r = c.execute(
                "SELECT COUNT(*) FROM follow_log "
                "WHERE action='followback' AND DATE(followed_at)=DATE('now','localtime')"
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


def _patrol_v6_summary() -> str:
    """patrol_v6 の最新 state を 1行 summary に."""
    try:
        p = REPO_ROOT / "state" / "patrol_v6_state.json"
        if not p.exists():
            return "patrol_v6: not run yet"
        d = json.loads(p.read_text(encoding="utf-8"))
        crit = d.get("critical_count", 0)
        warn = d.get("warn_count", 0)
        ts = d.get("ts", "?")[:19]
        if crit > 0:
            return f"patrol_v6: CRITICAL={crit} WARN={warn} (last {ts})"
        if warn > 0:
            return f"patrol_v6: WARN={warn} (last {ts})"
        return f"patrol_v6: ALL OK (last {ts})"
    except Exception as e:
        return f"patrol_v6: parse error {e}"


def _patrol_v6_critical_block() -> list[str]:
    """2026-05-07 P0-6 (Plan v5 真因 #4):
    patrol_v6_state.json の CRITICAL/WARN alert を dashboard 文頭に展開する。

    Returns:
        Slack 投稿用の行リスト (CRITICAL あり時のみ <!channel> mention 付き)
    """
    lines: list[str] = []
    try:
        p = REPO_ROOT / "state" / "patrol_v6_state.json"
        if not p.exists():
            return lines
        d = json.loads(p.read_text(encoding="utf-8"))
        crit_alerts = d.get("critical_alerts") or []
        warn_alerts = d.get("warn_alerts") or []
        recover_actions = d.get("recovery_actions_taken") or []
        if not crit_alerts and not warn_alerts:
            return lines

        if crit_alerts:
            lines.append("<!channel> 【patrol_v6 CRITICAL 検知】")
            for a in crit_alerts[:5]:
                msg = (a.get("message") or "")[:100]
                rec = a.get("auto_recover") or "-"
                lines.append(f"  [!] [{a.get('layer','?')}] {msg} (recover={rec})")
            if len(crit_alerts) > 5:
                lines.append(f"  ... 他 {len(crit_alerts) - 5} 件")
        if warn_alerts:
            lines.append(f"[patrol_v6 WARN] {len(warn_alerts)} 件:")
            for a in warn_alerts[:3]:
                msg = (a.get("message") or "")[:100]
                lines.append(f"  - [{a.get('layer','?')}] {msg}")
        if recover_actions:
            lines.append(f"[auto_recover taken] {len(recover_actions)} 件:")
            for r in recover_actions[:5]:
                act = r.get("action", "?")
                lyr = r.get("alert_layer", "?")
                ok = (
                    "OK" if r.get("rc") == 0 or r.get("slack_sent") or r.get("status") not in ("error", "unknown_action")
                    else "FAIL"
                )
                lines.append(f"  → [{lyr}] {act} = {ok}")
        lines.append("")  # 空行で 4機能サマリと分ける
    except Exception as e:
        lines.append(f"[patrol_v6 critical block parse error] {e}")
    return lines


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
    # 2026-05-05 礎: SSOT スプシから目標値取得
    targets = _load_ssot_targets()
    t_post = targets.get("post", 200)
    t_like = targets.get("like", 500)
    t_follow = targets.get("follow", 2000)
    t_fb = targets.get("followback", 30)

    def _pct(actual, target):
        if not target: return "-"
        return f"{int(actual * 100 / target)}%"

    # 2026-05-07 P0-6 (Plan v5): CRITICAL を文頭に展開し CEO が朝起きて即把握できる状態に
    crit_block = _patrol_v6_critical_block()
    lines = [
        f"【サイバー定時報告 #{mode_label}】 {now.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if crit_block:
        lines.extend(crit_block)
    lines += [
        f"■ POST   today={post['posted']}/{t_post} ({_pct(post['posted'], t_post)}) | "
        f"last {post['last_at'] or '-'} ({post['last_age_h'] or '-'}h前)",
        f"■ LIKE   today={like['liked']}/{t_like} ({_pct(like['liked'], t_like)}) | "
        f"last {like['last_at'] or '-'} ({like['last_age_min'] or '-'}m前)",
        f"■ FOLLOW today={follow['followed']}/{t_follow} ({_pct(follow['followed'], t_follow)}) | "
        f"sessions={follow.get('sessions_today', 0)} | "
        f"VM={follow['vm_state']} | login={follow.get('login_status', '?')} | "
        f"hb_age={follow.get('heartbeat_age_sec', '?')}s",
        f"■ FB     today={fb['followback']}/{t_fb} ({_pct(fb['followback'], t_fb)}) | "
        f"pending={fb['pending']} | last {fb['last_at'] or '-'} ({fb['last_age_min'] or '-'}m前)",
        "",
        f"[目標源] スプシ「楽天ROOM_デイリーログ」 (gid={SSOT_SHEET_GID})",
        _patrol_v6_summary(),
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
