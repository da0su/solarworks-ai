"""ROOM 4 機能 現状把握の単一 SSOT エントリ (Codex 推奨 A).

【目的】 CEO「ROOM どう?」「フォロー / POST / LIKE / FOLLOWBACK 状況」「停止してる?」
に答える時に **必ず最初に** これを実行する.

【背景・再発防止】 2026-05-20 / 5/22 の 2 度の失態
- 5/20: chrome_profile_post (host) を見て「全 profile 空アカ」と誤判定
- 5/22: rakuten-room/bot/data/follow_history.json (5/20 凍結) を見て「FOLLOW 2日停止」と誤判定
両方とも host のレガシーファイルを SSOT と勘違い. CEO 指摘 2 回.

【厳守】
- SSOT のみ参照. host のレガシー JSON は読まない
- 鮮度検証: mtime >= 20分 なら STALE 判定 + exit!=0
- データ不明/欠落時は「正常」と表示しない (false success 防止)
- 結果は JSON で stdout + 人間向けサマリーを stderr

【出力】
{
  "ok": bool,                    # 全機能 正常?
  "stale": bool,                 # SSOT 古い?
  "any_problem": bool,
  "sources": {
    "follow_runtime_state": {"path","mtime_iso","age_sec","exists","fresh"},
    "patrol_v6_state": {...},
    "daily_targets_ssot": {...}
  },
  "functions": {
    "follow": {"today","problem","reasons","last_action_iso"},
    "post": {...}, "like": {...}, "followback": {...}
  },
  "summary": "1行要約"
}

【使い方】
    python ops/room_status.py                    # JSON 出力
    python ops/room_status.py --human            # 人間向けサマリー
    python ops/room_status.py --json | jq        # piping
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "state"

# SSOT ファイル (これしか見ない)
SSOT_FILES = {
    "follow_runtime_state": STATE / "follow_runtime_state.json",
    "patrol_v6_state": STATE / "patrol_v6_state.json",
    "daily_targets_ssot": STATE / "daily_targets_ssot.json",
    "follow_rate_state": STATE / "follow_rate_state.json",
}

# 鮮度閾値 (秒)
FRESH_THRESHOLD_SEC = {
    "follow_runtime_state": 20 * 60,    # 15分 patrol + 余裕
    "patrol_v6_state": 20 * 60,
    "daily_targets_ssot": 6 * 3600 + 600,  # 6h cache + 余裕
    "follow_rate_state": 20 * 60,
}


def _stat(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "mtime_iso": None,
                "age_sec": None, "fresh": False}
    st = path.stat()
    age = (datetime.now().timestamp() - st.st_mtime)
    mtime_iso = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    return {"path": str(path), "exists": True, "mtime_iso": mtime_iso,
            "age_sec": round(age, 1), "fresh": True}  # fresh は後で判定上書き


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_status() -> dict:
    sources: dict = {}
    any_stale = False
    for key, path in SSOT_FILES.items():
        info = _stat(path)
        threshold = FRESH_THRESHOLD_SEC[key]
        if info["exists"] and info["age_sec"] is not None:
            info["fresh"] = info["age_sec"] <= threshold
        else:
            info["fresh"] = False
        if not info["fresh"]:
            any_stale = True
        sources[key] = info

    # 4 機能の状態を follow_runtime_state.json から抽出
    frs = _read_json(SSOT_FILES["follow_runtime_state"]) or {}
    functions: dict = {}
    any_problem = False
    for fn in ("follow", "post", "like", "followback"):
        d = frs.get(fn) or {}
        f_summary = {
            "problem": d.get("problem", None),
            "reasons": d.get("reasons", []) or [],
        }
        # 機能別の代表 KPI を抽出
        if fn == "follow":
            f_summary["today_success"] = (d.get("last_entry", {}) or {}).get("success")
            f_summary["last_action_iso"] = (d.get("last_entry", {}) or {}).get("ts")
            f_summary["last_12h"] = d.get("last_12h")
            f_summary["vm_running"] = d.get("vm_running")
            f_summary["login_status"] = d.get("login_status")
            f_summary["heartbeat_age_sec"] = d.get("heartbeat_age_sec")
        elif fn == "post":
            f_summary["today_posted"] = d.get("today_posted")
            f_summary["last_posted_at"] = d.get("last_posted_at")
            f_summary["last_posted_age_days"] = d.get("last_posted_age_days")
        elif fn == "like":
            f_summary["today_liked"] = d.get("today_liked")
            f_summary["last_liked_at"] = d.get("last_liked_at")
        elif fn == "followback":
            f_summary["today_followback"] = d.get("today_followback")
            f_summary["last_followback_at"] = d.get("last_followback_at")
        if f_summary.get("problem"):
            any_problem = True
        functions[fn] = f_summary

    # SSOT 古い場合は「ok」と言わない (false success 防止)
    ok = (not any_stale) and (not any_problem) and bool(frs)

    # 1 行要約
    if any_stale:
        summary = "⚠️ SSOT STALE - 鮮度 NG. patrol_v6 が更新していない可能性. 即原因究明."
    elif any_problem:
        problems = [fn for fn, v in functions.items() if v.get("problem")]
        summary = f"⚠️ 問題機能: {','.join(problems)}"
    else:
        summary = "✅ 全機能 SSOT 上 正常"

    return {
        "ok": ok,
        "stale": any_stale,
        "any_problem": any_problem,
        "sources": sources,
        "functions": functions,
        "summary": summary,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _human(status: dict) -> str:
    lines = []
    lines.append(f"=== ROOM 4 機能 SSOT 現状 ({status['generated_at']}) ===")
    lines.append(f"OK={status['ok']} STALE={status['stale']} ANY_PROBLEM={status['any_problem']}")
    lines.append(f"summary: {status['summary']}")
    lines.append("")
    lines.append("--- SSOT ファイル鮮度 ---")
    for k, v in status["sources"].items():
        flag = "✅" if v["fresh"] else "⚠️ STALE"
        age = v.get("age_sec")
        age_h = f"{age/60:.1f}min" if age is not None else "N/A"
        lines.append(f"  {flag} {k}: age={age_h} mtime={v.get('mtime_iso')} exists={v['exists']}")
    lines.append("")
    lines.append("--- 4 機能 状態 ---")
    for fn, v in status["functions"].items():
        flag = "⚠️ PROBLEM" if v.get("problem") else "✅"
        lines.append(f"  {flag} {fn}:")
        for kk, vv in v.items():
            if kk in ("problem",):
                continue
            lines.append(f"      {kk}: {vv}")
    lines.append("")
    lines.append("--- 禁忌 (見て判断しない) ---")
    lines.append("  ✗ rakuten-room/bot/data/follow_history.json (Plan v6 cutover 凍結)")
    lines.append("  ✗ rakuten-room/bot/data/like_history.json    (同上)")
    lines.append("  ✗ rakuten-room/bot/data/post_history.json    (同上)")
    lines.append("  ✗ rakuten-room/bot/data/fl_daily_log.json    (さらに古い)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="ROOM 4 機能 SSOT 現状把握 (必ず最初に実行)")
    ap.add_argument("--human", action="store_true", help="人間向けサマリー出力")
    ap.add_argument("--json", action="store_true", help="JSON 出力 (default)")
    args = ap.parse_args()

    status = build_status()
    if args.human:
        print(_human(status))
    else:
        # default: JSON
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))

    # exit code: ok なら 0 / STALE or PROBLEM なら 4
    if not status["ok"]:
        sys.exit(4)
    sys.exit(0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
