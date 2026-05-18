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


def _room_cumulative_via_browser() -> dict:
    """CEO 5/18 指示: ROOM 内のすべての累計数字 (スプシ突合用).

    chrome_profile_post を起動して my ROOM page から:
    商品 / フォロー / フォロワー / コーディネート / コレクション / いいね
    すべて取得.

    profile が壊れていれば 0 / None で記録 (Codex がそれを見て CRITICAL 判定)
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "rakuten-room" / "bot"))
        from executor.browser_manager import BrowserManager
        from shared.profile_health import fetch_my_room_fingerprint
    except Exception as e:
        return {"_error": f"import: {e}"}
    bm = None
    try:
        bm = BrowserManager(action="post")
        bm.start()
        st = bm.check_login_status()
        if not st.get("logged_in"):
            return {"_error": "not logged in", "login_status": st}
        fp = fetch_my_room_fingerprint(bm.page)
        return fp
    except Exception as e:
        return {"_error": f"fetch: {e}"}
    finally:
        if bm:
            try:
                bm.stop()
            except Exception:
                pass


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

【CEO 5/18 追加指示: payload の 2 項目を最優先に check】
1. ssot_targets_today_FRESH: スプシ最新値 (force_refresh で必ず gspread から取得).
   サイバー (私) が前提を間違える可能性があるため、必ずこの値を 本日目標 として扱う.
2. room_cumulative_now: ROOM の全累計 (商品/フォロー/フォロワー/コーディネート/コレクション/いいね).
   - スプシの累積値と この実 ROOM の累積値が合っているか 比較せよ.
   - 商品数 = 0 or follower 異常に少ない → profile 異常 (別アカウント切替疑い)
   - スプシ累積値と乖離 → スプシ集計バグ or 投稿実態が DB に正しく反映されていない疑い

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


def build_user_payload(now: datetime) -> dict:
    """Codex に渡す全データ (CEO 5/18 指示反映).

    必須 2 点 (CEO 5/18 22:09):
      1. ssot_targets_today: スプシ最新を force_refresh で必ず取得 (cache 使わない)
      2. room_cumulative_now: ROOM 内のすべての累計数字 (商品/フォロー/フォロワー/
         コーディネート/コレクション/いいね) - スプシが合っているかの突合用
    """
    return {
        "briefing_at": now.isoformat(timespec="seconds"),
        "window": "前24h (前日 9:00 → 当日 9:00 想定)",
        "ssot_targets_today_FRESH": _ssot_targets(),  # CEO 5/18: 必ず最新
        "room_cumulative_now": _room_cumulative_via_browser(),  # CEO 5/18: ROOM 全累計突合
        "post_24h": _post_summary_24h(now),
        "follow_24h": _follow_summary_24h(now),
        "profile_health": _profile_health(),
        "recent_codex_reviews_24h": _recent_codex_reviews(),
        "codex_usage_cumulative": _usage_cumulative(),
    }


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
