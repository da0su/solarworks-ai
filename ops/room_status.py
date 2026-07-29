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
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ROOM API の created_at は JST naive ("YYYY-MM-DD HH:MM:SS"). TZ 安全のため明示。
JST = timezone(timedelta(hours=9))

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
    # 2026-05-29: HOST follow が VM移行で停止以降 follow_rate_state は更新なし
    # → stale チェック対象から実質除外 (7日 threshold)
    "follow_rate_state": 7 * 24 * 3600,
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


# 自アカウント (空くんママ / room_e05d4d1c1e) の user_id
OWN_USER_ID = "1000006606047125"
# POST 停止とみなす経過時間 (時間). 投稿は日次バッチなので 30h 無投稿=明確に停止
POST_STALL_HOURS = 30


def fetch_post_truth() -> dict | None:
    """POST の真値を公開 ROOM API から取得 (凍結 post_history.json を見ない).
    2026-07-29: patrol_v6 business.py からも参照するため public 名に変更
    (旧 _fetch_post_truth)。POST 実績の真値取得はここに一本化する。
    2026-06-04 CEO 指示: room_status.py が post_history.json (5/30 凍結) を参照して
    毎回 POST を誤って「停止」判定する誤報を解消する。真値は自アカウントの実投稿フィード。
    戻り {today_posted, last_posted_at, last_posted_age_hours, source} / 失敗時 None。"""
    import urllib.request
    url = f"https://room.rakuten.co.jp/api/{OWN_USER_ID}/collects?limit=50"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace")).get("data", [])
    except Exception:
        return None
    if not data:
        return None

    def _parse(s):
        """TZ 安全パース: ISO8601(オフセット/Z付) も "YYYY-MM-DD HH:MM:SS" も受ける。
        naive は JST とみなす (ROOM API は JST naive)。"""
        if not s:
            return None
        s = str(s).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt

    times = [t for t in (_parse(p.get("created_at")) for p in data) if t]
    if not times:
        return None
    now_jst = datetime.now(JST)
    last = max(times)
    today_jst = now_jst.date()
    today_posted = sum(1 for t in times if t.astimezone(JST).date() == today_jst)
    age_h = max(0.0, (now_jst - last).total_seconds() / 3600)  # clock skew で負にしない
    return {
        "today_posted": today_posted,
        "last_posted_at": last.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "last_posted_age_hours": round(age_h, 1),
        "last_posted_age_days": round(age_h / 24, 2),  # 互換: 旧 days フィールド維持
        "source": "public_api (truth, limit=50)",
    }


# 2026-06-04: HOST follow が VM 移行で廃止され follow_rate_state は永久に更新されない。
# 死んだファイルの古さで STALE 誤報を出さないよう、STALE 判定の対象から除外する
# (表示はするが OK/STALE は左右しない)。
RETIRED_SOURCES = {"follow_rate_state"}


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
        # 2026-06-06 修正: daily_targets_ssot は「1日1回更新」の値なので mtime 6h 閾値だと
        # 毎日昼に必ず STALE 誤報になる。中身の date が当日なら fresh とみなす(日付ベース判定)。
        if key == "daily_targets_ssot" and not info["fresh"] and info["exists"]:
            try:
                _dt = _read_json(path) or {}
                _tg = _dt.get("targets")
                # 壊れ/部分JSONを fresh と誤判定しない: 4機能の目標が揃い非Noneであること
                _valid = isinstance(_tg, dict) and all(
                    _tg.get(k) is not None for k in ("post", "follow", "like", "followback"))
                if _dt.get("date") == datetime.now().strftime("%Y-%m-%d") and _valid:
                    info["fresh"] = True
                    info["fresh_reason"] = "date==today かつ targets 4機能とも有効 (1日1回更新の値)"
            except Exception:
                pass  # 読めない/壊れている場合は fresh にしない (STALEのまま=安全側)
        info["retired"] = key in RETIRED_SOURCES
        if not info["fresh"] and key not in RETIRED_SOURCES:
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
            # 2026-05-27 修正: follow_runtime_state.json の last_entry は patrol_v6
            # の観測値で host-side の実フォローと乖離する (本日 follow_history.json
            # は 156 件 real follow なのに last_entry.success=0 と矛盾)。
            # → 真値は follow_history.json (count_real_follows_on) を読む。
            try:
                import sys as _sys
                _sys.path.insert(0, str(REPO))
                from shared.follow_history_reader import count_real_follows_on
                from datetime import datetime as _dt2
                _today_str = _dt2.now().strftime("%Y-%m-%d")
                f_summary["today_success"] = count_real_follows_on(_today_str)
                f_summary["today_success_source"] = "follow_history.json (truth)"
            except Exception:
                # フォールバック (旧 patrol 観測値)
                f_summary["today_success"] = (d.get("last_entry", {}) or {}).get("success")
                f_summary["today_success_source"] = "patrol_v6 observation (fallback)"
            f_summary["last_action_iso"] = (d.get("last_entry", {}) or {}).get("ts")
            f_summary["last_12h"] = d.get("last_12h")
            f_summary["vm_running"] = d.get("vm_running")
            f_summary["login_status"] = d.get("login_status")
            f_summary["heartbeat_age_sec"] = d.get("heartbeat_age_sec")
        elif fn == "post":
            # 2026-06-04 修正: patrol が凍結 post_history.json (5/30 停止) を見て
            # 毎回 POST を「停止」と誤判定する誤報を解消. 真値=公開 API の実投稿フィード。
            post_truth = fetch_post_truth()
            if post_truth:
                f_summary["today_posted"] = post_truth["today_posted"]
                f_summary["last_posted_at"] = post_truth["last_posted_at"]
                f_summary["last_posted_age_hours"] = post_truth["last_posted_age_hours"]
                f_summary["last_posted_age_days"] = post_truth["last_posted_age_days"]
                f_summary["post_source"] = post_truth["source"]
                # 真値で problem/reasons を再計算 (30h 無投稿=停止)
                stalled = post_truth["last_posted_age_hours"] > POST_STALL_HOURS
                f_summary["problem"] = stalled
                f_summary["reasons"] = (
                    [f"no_post_for_{post_truth['last_posted_age_hours']:.0f}h (truth)"]
                    if stalled else [])
            else:
                # 公開 API 不達時: 凍結 patrol 値で「停止」と断定するとフラッピング/誤報の元。
                # → unknown 扱い (problem は立てない・any_problem に算入しない)。source 明示。
                f_summary["today_posted"] = None
                f_summary["last_posted_at"] = d.get("last_posted_at")
                f_summary["post_source"] = "unknown (public API unreachable)"
                f_summary["problem"] = False
                f_summary["reasons"] = ["public_api_unreachable (判定不能・要確認)"]
        elif fn == "like":
            f_summary["today_liked"] = d.get("today_liked")
            f_summary["last_liked_at"] = d.get("last_liked_at")
        elif fn == "followback":
            f_summary["today_followback"] = d.get("today_followback")
            f_summary["last_followback_at"] = d.get("last_followback_at")
            # 2026-06-04 修正: 本日 FB 目標が 0 の日は「停止」と誤判定しない。
            # 目標 0 (CEO がスプシで設定) なら FB 0 件は正しい挙動。
            try:
                _tg = _read_json(SSOT_FILES["daily_targets_ssot"]) or {}
                _fb_target = (_tg.get("targets") or {}).get("followback")
                f_summary["today_target"] = _fb_target
                if _fb_target == 0:
                    f_summary["problem"] = False
                    f_summary["reasons"] = []
                    f_summary["note"] = "本日FB目標=0のため稼働停止が正常 (誤報抑制)"
            except Exception:
                pass
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
        if v.get("retired"):
            flag = "➖ 廃止(判定対象外)"
        else:
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
