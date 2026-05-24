"""Codex (GPT-5) 朝 9:00 定期 briefing + 本日指示 (CEO 5/17 22:08 指示).

【CEO 指示】
> 「毎朝 朝9時にコデックスに定期報告して、本日の指示を仰ぐ.
>  前日と夜間の結果をそのままコデックスに報告する形」

【設計】
- 過去 24h (前日 9:00 → 当日 9:00) の各 KPI を集計
- スプシ目標値 vs 実績 + profile_health 指紋
- false success suspect, エラー上位
- Codex (GPT-5) に投げて本日の優先アクション 3-5 取得
- Slack に投稿 + state/codex_daily_briefings/<date>.json 保存

【Task Scheduler】
- 毎日 9:00 起動 (run_hidden.vbs 経由)
- 起動コマンド:
    wscript.exe ops/scheduler/run_hidden.vbs ops/scheduler/wrap_RoomBot_CodexBriefing.bat

【手動実行】
    python ops/codex_daily_briefing.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = REPO_ROOT / "state" / "codex_daily_briefings"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# データ集計
# ============================================================

def _ssot_targets() -> dict:
    """CEO 5/18 指示: 毎朝必ず force_refresh で gspread fetch.
    cache に頼らず スプシ最新を取得する.
    """
    try:
        from ops.notifications.dashboard_report import _load_ssot_targets
        t = _load_ssot_targets(force_refresh=True)
        return {
            "values": t or {},
            "source": "gspread:楽天ROOM_デイリーログ (force_refresh=True)",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {"_error": str(e), "source": "fallback"}


def _ssot_cumulative() -> dict:
    """CEO 5/20 指示: スプシ累計実績 N/O/P/Q 列 も毎朝取得.

    「累計数値スプシ分とアカウント分どちらも毎日報告して GPT にアドバイスもらう」
    要件. ROOM 累計 (room_cumulative_now) と突合可能にする.
    """
    try:
        from ops.notifications.dashboard_report import _load_ssot_cumulative
        c = _load_ssot_cumulative(force_refresh=True)
        return c if c else {"_error": "empty"}
    except Exception as e:
        return {"_error": str(e)}


def _room_cumulative_via_vm_http() -> dict | None:
    """VM HTTP /room_stats エンドポイントから ROOM 累計を取得.

    2026-05-25 追加: HOST Chrome profile は全て空アカウント (商品数 0)
    のため、VM の KAPIBARAN session 経由で取得する。VM HTTP が死んでいる場合は
    None を返して呼び元が HOST profile fallback に移行する。
    """
    import urllib.request as _ureq
    VM_TOKEN = "rakuten-room-v6-secret"
    VM_BASE  = "http://localhost:18765"
    try:
        req = _ureq.Request(
            f"{VM_BASE}/room_stats",
            headers={"Authorization": f"Bearer {VM_TOKEN}"}
        )
        r = _ureq.urlopen(req, timeout=70)   # fetcher timeout=60 + margin
        data = json.loads(r.read())
        if "_error" in data:
            print(f"[_room_cumulative_via_vm_http] VM returned error: {data['_error'][:200]}",
                  file=sys.stderr)
            return None
        return data
    except Exception as e:
        print(f"[_room_cumulative_via_vm_http] VM HTTP unreachable: {e}", file=sys.stderr)
        return None


def _room_cumulative_via_browser() -> dict:
    """CEO 5/18 指示: ROOM 内のすべての累計数字 (スプシ突合用).

    2026-05-25 修正: VM HTTP /room_stats を優先試行 (KAPIBARAN 本物 session)。
    VM HTTP 失敗時に HOST profile fallback chain (旧ロジック) へ移行。

    Codex 29回目 #8 反映 (CEO 5/20 累計突合):
    chrome_profile_post が空アカウントへ切替の疑い → profile fallback chain で取得.
    試行順: post → follow → like → followback. 商品数 >= 50 を valid 判定.

    Returns: {profile_used, fingerprint, tried, errors} or {_error, tried, errors}
    Codex 32回目 #5 fix: Task Scheduler 起動 dir 異常時の import フォールバック
    """
    # ── 優先: VM HTTP /room_stats (KAPIBARAN 本物 session) ───────────────────
    vm_result = _room_cumulative_via_vm_http()
    if vm_result is not None:
        # shape を downstream と揃える (_profile_used, _tried, _errors)
        vm_result.setdefault("_profile_used", vm_result.get("_profile_used", "vm:follow"))
        vm_result.setdefault("_tried", ["vm_http"])
        vm_result.setdefault("_errors", {})
        vm_result.setdefault("tried", vm_result["_tried"])
        vm_result.setdefault("errors", vm_result["_errors"])
        return vm_result

    # ── 後退: HOST profile fallback chain (旧ロジック) ─────────────────────
    try:
        from shared.profile_health import fetch_room_cumulative_via_fallback_chain
    except ImportError:
        # Task Scheduler の cwd が想定外 → REPO_ROOT を強制 insert して再 try
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from shared.profile_health import fetch_room_cumulative_via_fallback_chain
        except Exception as e:
            return {"_error": f"import (post sys.path retry): {e}",
                    "_tried": [], "_errors": {}, "_profile_used": None,
                    "tried": [], "errors": {}}
    except Exception as e:
        return {"_error": f"import: {e}",
                "_tried": [], "_errors": {}, "_profile_used": None,
                "tried": [], "errors": {}}

    result = fetch_room_cumulative_via_fallback_chain()
    # 古い shape との互換: 成功時 fingerprint を top-level merge (downstream の
    # room.get("item_count") などの参照を壊さないため)
    # Codex 31回目 #1 fix: 成功/失敗 shape を統一 (_tried/_errors/_profile_used で固定)
    if "_error" not in result:
        fp = result.get("fingerprint", {})
        out = dict(fp)
        out["_profile_used"] = result.get("profile_used")
        out["_tried"] = result.get("tried", [])
        out["_errors"] = result.get("errors", {})
        # Codex 32回目 #3: 後方互換 (旧 key 'tried'/'errors' を読む既存コード対策)
        # 段階的移行用 - 確認後削除予定
        out["tried"] = out["_tried"]
        out["errors"] = out["_errors"]
        return out
    # 失敗時も _tried/_errors を underscore key で揃える (downstream 一貫性)
    return {
        "_error": result.get("_error"),
        "_tried": result.get("tried", []),
        "_errors": result.get("errors", {}),
        "_profile_used": None,
        # 後方互換 (Codex 32 #3)
        "tried": result.get("tried", []),
        "errors": result.get("errors", {}),
    }


def _post_summary_24h(now: datetime) -> dict:
    """過去 24h の POST DB 集計."""
    db = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
    if not db.exists():
        return {"_error": "db not found"}
    yesterday = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        rows = con.execute(
            "SELECT status, COUNT(*) FROM post_queue "
            "WHERE posted_at >= ? GROUP BY status",
            (yesterday,),
        ).fetchall()
        by_status = dict(rows)
        # 直近 false success suspect 件数 (result_message に false_success or persona_mismatch)
        rfail = con.execute(
            "SELECT error_type, COUNT(*) FROM post_queue "
            "WHERE posted_at >= ? AND status='failed' GROUP BY error_type",
            (yesterday,),
        ).fetchall()
        # queue 残
        rq = con.execute(
            "SELECT COUNT(*) FROM post_queue WHERE queue_date >= ? AND status='queued'",
            (now.strftime("%Y-%m-%d"),),
        ).fetchone()
        con.close()
        return {
            "by_status_24h": by_status,
            "failed_breakdown": dict(rfail),
            "queued_today_plus": rq[0] if rq else 0,
        }
    except Exception as e:
        return {"_error": str(e)}


def _follow_summary_24h(now: datetime) -> dict:
    """過去 24h FOLLOW 実績 (follow_history.json から)."""
    fh = REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json"
    if not fh.exists():
        return {"_error": "follow_history not found"}
    try:
        data = json.loads(fh.read_text(encoding="utf-8"))
        cutoff = (now - timedelta(hours=24)).isoformat()
        by_src = Counter()
        for e in data:
            if isinstance(e, dict):
                ts = e.get("followed_at", "")
                if ts >= cutoff:
                    by_src[e.get("source", "unknown")] += 1
        total = sum(by_src.values())
        real = sum(v for k, v in by_src.items() if k != "skip_discover")
        # 直近 7日 trend
        by_date = Counter()
        for e in data:
            if isinstance(e, dict):
                d = (e.get("followed_at") or "")[:10]
                if d.startswith("2026-"):
                    by_date[d] += 1
        last7 = {k: v for k, v in sorted(by_date.items())[-7:]}
        return {
            "total_24h": total,
            "real_24h": real,
            "skip_discover_24h": by_src.get("skip_discover", 0),
            "by_source": dict(by_src),
            "last_7_days_total": last7,
        }
    except Exception as e:
        return {"_error": str(e)}


def _profile_health() -> dict:
    """profile_baseline (saved) と現状 fingerprint を取得."""
    try:
        from shared.profile_health import load_baseline
        baseline = load_baseline() or {}
        return {
            "baseline_saved": bool(baseline),
            "baseline": baseline,
            "note": "現状 fingerprint は browser 起動が必要なので別 step. baseline は CEO 確認済 OK アカウント時の値.",
        }
    except Exception as e:
        return {"_error": str(e)}


def _recent_codex_reviews() -> dict:
    """過去 24h の Codex review verdict + 件数."""
    reviews_dir = REPO_ROOT / "state" / "codex_reviews"
    if not reviews_dir.exists():
        return {}
    now = datetime.now()
    cutoff_str = (now - timedelta(hours=24)).strftime("%Y%m%d_%H%M%S")
    verdicts = Counter()
    count = 0
    for f in sorted(reviews_dir.glob("*_review.json")):
        if f.name >= cutoff_str:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                verdicts[d.get("verdict", "UNKNOWN")] += 1
                count += 1
            except Exception:
                continue
    return {"reviews_24h": count, "by_verdict": dict(verdicts)}


def _usage_cumulative() -> dict:
    """Codex 累計使用量."""
    try:
        from ops.codex_review import _cumulative_usage
        return _cumulative_usage()
    except Exception as e:
        return {"_error": str(e)}


# ============================================================
# Codex プロンプト
# ============================================================

SYSTEM_PROMPT = """あなたは楽天ROOM自動化 bot の シニア戦略アドバイザー兼サイバーセキュリティ専門家.
CEO は信頼性を最重視. 虚偽報告は厳禁. 過去事例:
- 5/12-5/17 で 6日間 全 POST/FOLLOW が false success だった (chrome_profile_* が空アカウントへ切替)
- CEO 「成功してるか商品画面で確認」ルール化済

【CEO 5/18 + 5/20 追加指示: payload の 4 項目を最優先 check】
1. ssot_targets_today_FRESH: スプシ最新「目標値」(B/E/H/K列・force_refresh で gspread).
   サイバー (私) が前提を間違える可能性があるため、必ずこの値を 本日目標 として扱う.
2. ssot_cumulative_today_FRESH: スプシ「累計実績」(N/O/P/Q列・force_refresh) ← CEO 5/20 NEW
   投稿/フォロー/いいね/フォローバック の 累計 (前日累計+当日実績の formula 自動計算).
3. room_cumulative_now: ROOM 実際の全累計 (商品/フォロー/フォロワー/コーディネート/コレクション/いいね).
4. ssot_cum_vs_room_diff: ② と ③ の乖離 (各項目 delta + warning フラグ).
   - 大きな乖離 = スプシ集計バグ or 投稿実態が ROOM 反映されてない (false success 再発疑い)
   - 商品数 = 0 or follower 異常少 → profile 異常 (別アカ切替疑い)
   - delta が abs(s * 0.05) 超過なら警告必須

本日朝 9:00 の briefing として、以下の過去 24h データを見て:
1. CEO に伝えるべき最優先 3 アクション (本日 何をすべきか)
2. 警告: false success 疑い・profile 異常・KPI 大幅未達・スプシ↔ROOM 累計乖離 等
3. 達成見込み: 今日の目標達成可否予測 (ssot_targets_today_FRESH を baseline に)
4. 中期戦略提案 (1-2 個)
を 簡潔 (各 2-3 行) に提案してください.

出力 JSON:
{
    "verdict": "OK | WARN | CRITICAL",
    "today_priorities": ["1. ...", "2. ...", "3. ..."],
    "warnings": ["..."],
    "achievement_forecast": "短文",
    "ssot_vs_room_check": "短文: スプシ累積 vs ROOM 累計 の乖離有無",
    "mid_term_proposals": ["..."],
    "summary": "1-2 行要約"
}
"""


def _compute_ssot_vs_room_diff(ssot: dict, room: dict) -> dict:
    """SSOT スプシ累積 vs ROOM 累計 の乖離計算 (Codex 29回目 #8 反映 CEO 5/20).

    Returns: {by_field: {field: {ssot, room, delta, status}}, summary}

    Codex 31回目 #1 fix: error path も _tried/_errors の underscore key で統一.
    Codex 32回目 #2 fix: error path も {by_field, summary} 必須 (shape 不変).
    """
    if "_error" in room:
        return {
            "_error": room["_error"],
            "_tried": room.get("_tried", []),
            "_errors": room.get("_errors", {}),
            "_profile_used": room.get("_profile_used"),
            "by_field": {},
            "summary": f"error: {room['_error']}",
        }
    ssot_vals = ssot.get("values", {}) if isinstance(ssot, dict) else {}
    # スプシ累計 cumulative (列名は環境依存。一旦 follow/post/like/followback 名で試行)
    # 実際の SSOT 列名は ops.notifications.dashboard_report の値構造による
    out_fields = {}
    for field, ssot_keys in {
        "follower_count": ["cumulative_follower", "follower_cumulative", "total_followers"],
        "follow_count": ["cumulative_follow", "follow_cumulative", "total_follows"],
        "item_count": ["cumulative_post", "post_cumulative", "total_posts"],
        "like_count": ["cumulative_like", "like_cumulative", "total_likes"],
    }.items():
        room_val = room.get(field)
        ssot_val = None
        for k in ssot_keys:
            if k in ssot_vals:
                ssot_val = ssot_vals[k]
                break
        if room_val is None or ssot_val is None:
            out_fields[field] = {"ssot": ssot_val, "room": room_val,
                                  "delta": None, "status": "missing"}
            continue
        try:
            import math
            delta = int(room_val) - int(ssot_val)
            # Codex 30回目 #1 + 31回目 #7 fix: 閾値 = max(100, ceil(ssot * 0.05))
            # 小型値 (ssot < 2000) は 100 件絶対閾値, 大型値 (ssot >= 2000) は 5% 比率閾値
            # ceil 採用で 小数切り捨てで臨界点が暈けない (Codex 31回目 #8 反映)
            ssot_int = int(ssot_val)
            pct_threshold = math.ceil(ssot_int * 0.05) if ssot_int > 0 else 0
            threshold = max(100, pct_threshold)
            divergent = abs(delta) > threshold
            out_fields[field] = {
                "ssot": ssot_val, "room": room_val, "delta": delta,
                "threshold": threshold,
                "status": "diverged" if divergent else "ok",
            }
        except (ValueError, TypeError):
            out_fields[field] = {"ssot": ssot_val, "room": room_val,
                                  "delta": None, "status": "parse_error"}

    # Codex 32回目 #1 fix: status 別 count は実カウント (parse_error 含む).
    divergent_count = sum(1 for v in out_fields.values() if v["status"] == "diverged")
    missing_count = sum(1 for v in out_fields.values() if v["status"] == "missing")
    ok_count = sum(1 for v in out_fields.values() if v["status"] == "ok")
    parse_err_count = sum(1 for v in out_fields.values() if v["status"] == "parse_error")
    # Codex 31回目 #2 fix: key 名を _profile_used (underscore prefix) で統一
    return {
        "_profile_used": room.get("_profile_used"),
        "by_field": out_fields,
        "summary": (
            f"divergent={divergent_count}, missing={missing_count}, "
            f"ok={ok_count}, parse_err={parse_err_count} "
            f"/ profile={room.get('_profile_used')}"
        ),
    }


def build_user_payload(now: datetime) -> dict:
    """Codex に渡す全データ (CEO 5/18 指示反映).

    必須 (CEO 5/18 + 5/20 指示):
      1. ssot_targets_today_FRESH: スプシ最新目標 (B/E/H/K 列) force_refresh
      2. ssot_cumulative_today_FRESH: スプシ累計実績 (N/O/P/Q 列) force_refresh  ← CEO 5/20 NEW
      3. room_cumulative_now: ROOM 内すべての累計数字 (browser 起動 fallback chain)
      4. ssot_vs_room_diff: ① と ③ の乖離 + NEW ② と ③ の乖離

    CEO 5/20 09:30 指示「累計数値スプシ分とアカウント分どちらも毎日報告」を満たす.
    """
    ssot = _ssot_targets()
    ssot_cum = _ssot_cumulative()  # CEO 5/20 NEW
    room = _room_cumulative_via_browser()
    return {
        "briefing_at": now.isoformat(timespec="seconds"),
        "window": "前24h (前日 9:00 → 当日 9:00 想定)",
        "ssot_targets_today_FRESH": ssot,           # CEO 5/18: 目標値 (B/E/H/K)
        "ssot_cumulative_today_FRESH": ssot_cum,    # CEO 5/20: 累計実績 (N/O/P/Q) ← NEW
        "room_cumulative_now": room,                # ROOM 全累計
        "ssot_vs_room_diff": _compute_ssot_vs_room_diff(ssot, room),
        "ssot_cum_vs_room_diff": _compute_ssot_cum_vs_room_diff(ssot_cum, room),  # NEW
        "post_24h": _post_summary_24h(now),
        "follow_24h": _follow_summary_24h(now),
        "profile_health": _profile_health(),
        "recent_codex_reviews_24h": _recent_codex_reviews(),
        "codex_usage_cumulative": _usage_cumulative(),
    }


def _compute_ssot_cum_vs_room_diff(ssot_cum: dict, room: dict) -> dict:
    """スプシ累計 (N/O/P/Q) と ROOM 実際の累計 の乖離計算 (CEO 5/20)."""
    if not isinstance(ssot_cum, dict) or "_error" in ssot_cum:
        return {"_error": "ssot_cum invalid", "ssot_cum": ssot_cum}
    if not isinstance(room, dict) or "_error" in room:
        return {"_error": "room invalid", "room_error": room.get("_error") if isinstance(room, dict) else "?"}
    # mapping: post_cum vs item_count (ROOM item count = 投稿累計)
    #          follow_cum vs follow_count (ROOM フォロー数)
    #          like_cum vs like_count (ROOM いいね数)
    #          fb_cum vs follower_count? (フォロワー = followback されたユーザ含む)
    diff = {}
    pairs = [
        ("post_cum", "item_count", "投稿"),
        ("follow_cum", "follow_count", "フォロー"),
        ("like_cum", "like_count", "いいね"),
        ("fb_cum", "follower_count", "フォロワー (fb 累計の代理)"),
    ]
    for ssot_key, room_key, label in pairs:
        s = ssot_cum.get(ssot_key, 0) or 0
        r = room.get(room_key, 0) or 0
        diff[label] = {
            "ssot_cumulative": s,
            "room_actual": r,
            "delta": r - s,  # positive = ROOM が多い (= スプシ集計遅れ可能性)
            "warning": abs(r - s) > max(100, int(s * 0.05)) if s else False,
        }
    return diff


def request_codex_briefing(payload: dict) -> dict:
    """Codex に投げて本日指示を取得."""
    from ops.codex_review import _load_openai_key, _calc_cost, _append_usage_log, _cumulative_usage, MODEL, USD_JPY
    key = _load_openai_key()
    if not key:
        return {"verdict": "CRITICAL", "summary": "OPENAI_API_KEY 未設定", "warnings": ["key not found"]}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
    except Exception as e:
        return {"verdict": "CRITICAL", "summary": f"OpenAI init failed: {e}"}

    user_content = (
        "【本日 9:00 briefing データ】\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:50000]}\n```\n\n"
        "上記から本日の優先アクションを JSON で返してください."
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = _calc_cost(MODEL, u.prompt_tokens, u.completion_tokens)
            _append_usage_log({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "model": MODEL,
                "context": "daily_briefing 9:00",
                **usage,
            })
            data["usage"] = usage
            data["cumulative"] = _cumulative_usage()
        return data
    except Exception as e:
        return {"verdict": "CRITICAL", "summary": f"Codex call err: {e}"}


def post_to_slack(briefing: dict, codex_result: dict) -> None:
    """Slack に投稿 (slack_reporter.py 経由)."""
    try:
        import subprocess
        msg_lines = [
            f"【サイバー朝報】{datetime.now().strftime('%Y-%m-%d %H:%M')} Codex (GPT-5) 朝の戦略 briefing",
            "",
            f"verdict: {codex_result.get('verdict','?')}",
            f"summary: {codex_result.get('summary','?')}",
            "",
            "■ 本日の優先 (Codex 推奨)",
        ]
        for p in codex_result.get("today_priorities", []):
            msg_lines.append(f"  {p}")
        if codex_result.get("warnings"):
            msg_lines.append("")
            msg_lines.append("■ 警告")
            for w in codex_result.get("warnings", []):
                msg_lines.append(f"  ⚠ {w}")
        if codex_result.get("achievement_forecast"):
            msg_lines.append("")
            msg_lines.append(f"■ 達成見込み: {codex_result['achievement_forecast']}")
        if codex_result.get("mid_term_proposals"):
            msg_lines.append("")
            msg_lines.append("■ 中期戦略提案")
            for p in codex_result.get("mid_term_proposals", []):
                msg_lines.append(f"  💡 {p}")
        # data snippet (CEO 5/18 必須 2 項目を 強調)
        ssot = briefing.get('ssot_targets_today_FRESH', {})
        room = briefing.get('room_cumulative_now', {})
        msg_lines += [
            "",
            "■ 【CEO 5/18 必須】スプシ最新目標 (force_refresh)",
            f"  {ssot.get('values', ssot.get('_error', '?'))}",
            f"  source: {ssot.get('source', '?')}",
            "",
            "■ 【CEO 5/18 必須】ROOM 内 全累計 (スプシ突合用)",
        ]
        if "_error" in room:
            msg_lines.append(f"  ❌ 取得失敗: {room['_error']}")
        else:
            msg_lines.append(f"  商品 {room.get('item_count')} / フォロー {room.get('follow_count')} / フォロワー {room.get('follower_count')}")
            msg_lines.append(f"  コーディネート {room.get('coordinate_count')} / コレクション {room.get('collection_count')} / いいね {room.get('like_count')}")
        if codex_result.get('ssot_vs_room_check'):
            msg_lines += ["", f"■ スプシ↔ROOM 突合: {codex_result['ssot_vs_room_check']}"]
        msg_lines += [
            "",
            "■ 過去 24h データ サマリ",
            f"  POST: {briefing.get('post_24h', {}).get('by_status_24h', {})}",
            f"  FOLLOW: real {briefing.get('follow_24h', {}).get('real_24h')} / total {briefing.get('follow_24h', {}).get('total_24h')}",
        ]
        # Codex 使用
        u = codex_result.get("usage", {})
        c = codex_result.get("cumulative", {})
        if u:
            msg_lines += ["", f"■ Codex 使用 (本回): ${u.get('total_usd',0):.4f} ~= JPY{u.get('total_jpy',0):.2f} ({u.get('total_tokens',0):,} tok)"]
        if c:
            msg_lines += [f"■ Codex 累計: ${c.get('all_usd',0):.4f} ~= JPY{c.get('all_jpy',0):.2f} ({c.get('all_calls',0)} calls)"]
        msg = "\n".join(msg_lines)
        sl = REPO_ROOT / "ops" / "notifications" / "slack_reporter.py"
        subprocess.run([sys.executable, str(sl), msg], capture_output=True, timeout=30)
    except Exception as e:
        print(f"[slack] ERR: {e}", file=sys.stderr)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now()
    print(f"[{now}] Codex 朝 briefing 開始")
    payload = build_user_payload(now)
    print(f"[data] ssot_FRESH={payload.get('ssot_targets_today_FRESH', {}).get('values')}, room={payload.get('room_cumulative_now')}, post={payload.get('post_24h')}, follow_real={payload.get('follow_24h', {}).get('real_24h')}")
    codex = request_codex_briefing(payload)
    print(f"\n=== Codex verdict: {codex.get('verdict','?')} ===")
    print(f"summary: {codex.get('summary','?')}")
    for p in codex.get("today_priorities", []):
        print(f"  {p}")
    # save
    fn = OUT_DIR / f"{now.strftime('%Y%m%d_%H%M')}_briefing.json"
    fn.write_text(json.dumps({
        "briefing_data": payload,
        "codex_result": codex,
        "saved_at": now.isoformat(),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {fn}")
    # Slack
    post_to_slack(payload, codex)
    print("[slack] 投稿完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
