"""
coin_business/scripts/kpi_report.py
KPI集計レポート — 運用モニタリング用

使い方:
  python scripts/kpi_report.py           # 現在のKPIを表示
  python scripts/kpi_report.py --slack   # Slack通知付き
  python scripts/kpi_report.py --today   # 本日分のみ
"""

import argparse
import sys
import os
from datetime import datetime, date, timezone
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.supabase_client import get_client


def fetch_kpi() -> dict:
    """SupabaseからKPI数値を取得して返す。"""
    db = get_client()
    today = date.today().isoformat()

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": today,
    }

    # ──────────────────────────────────────────
    # 候補数 (daily_candidates)
    # ──────────────────────────────────────────
    try:
        total = db.table("daily_candidates").select("id", count="exact").execute()
        pending = db.table("daily_candidates").select("id", count="exact").eq("ceo_decision", "pending").execute()
        approved_all = db.table("daily_candidates").select("id", count="exact").eq("ceo_decision", "approved").execute()
        rejected_all = db.table("daily_candidates").select("id", count="exact").eq("ceo_decision", "rejected").execute()

        # 本日判断分
        today_approved = (
            db.table("daily_candidates")
            .select("id", count="exact")
            .eq("ceo_decision", "approved")
            .gte("ceo_decided_at", today)
            .execute()
        )
        today_rejected = (
            db.table("daily_candidates")
            .select("id", count="exact")
            .eq("ceo_decision", "rejected")
            .gte("ceo_decided_at", today)
            .execute()
        )

        result["candidates"] = {
            "total": total.count or 0,
            "pending": pending.count or 0,
            "approved_all": approved_all.count or 0,
            "rejected_all": rejected_all.count or 0,
            "today_approved": today_approved.count or 0,
            "today_rejected": today_rejected.count or 0,
        }
    except Exception as e:
        result["candidates"] = {"error": str(e)}

    # ──────────────────────────────────────────
    # 入札数 (bid_history)
    # ──────────────────────────────────────────
    try:
        bids_all = db.table("bid_history").select("id", count="exact").execute()
        scheduled = db.table("bid_history").select("id", count="exact").eq("result", "scheduled").execute()
        win = db.table("bid_history").select("id", count="exact").eq("result", "win").execute()
        lose = db.table("bid_history").select("id", count="exact").eq("result", "lose").execute()

        # 本日入札登録分
        today_bids = (
            db.table("bid_history")
            .select("id", count="exact")
            .gte("created_at", today)
            .execute()
        )

        # 落札率（0除算防止）
        decided = (win.count or 0) + (lose.count or 0)
        win_rate = round((win.count or 0) / decided * 100, 1) if decided > 0 else None

        result["bids"] = {
            "total": bids_all.count or 0,
            "scheduled": scheduled.count or 0,
            "win": win.count or 0,
            "lose": lose.count or 0,
            "win_rate": win_rate,
            "today": today_bids.count or 0,
        }
    except Exception as e:
        result["bids"] = {"error": str(e)}

    return result


def format_report(kpi: dict, today_only: bool = False) -> str:
    """KPIデータを人間が読みやすいテキストに整形する。"""
    lines = []
    sep = "─" * 40

    lines.append(f"📊 コイン事業 KPIレポート")
    lines.append(f"生成: {kpi['generated_at']}")
    lines.append(sep)

    c = kpi.get("candidates", {})
    if "error" in c:
        lines.append(f"⚠️  候補DB取得エラー: {c['error']}")
    else:
        lines.append("【候補数】daily_candidates")
        if not today_only:
            lines.append(f"  総候補数      : {c['total']:>5}件")
            lines.append(f"  未判断 (未処理): {c['pending']:>5}件  ← 毎日ゼロを目指す")
            lines.append(f"  承認済み累計   : {c['approved_all']:>5}件")
            lines.append(f"  NG済み累計     : {c['rejected_all']:>5}件")
        lines.append(f"  本日 承認      : {c['today_approved']:>5}件")
        lines.append(f"  本日 NG        : {c['today_rejected']:>5}件")

    lines.append(sep)

    b = kpi.get("bids", {})
    if "error" in b:
        lines.append(f"⚠️  入札DB取得エラー: {b['error']}")
    else:
        lines.append("【入札数】bid_history")
        if not today_only:
            lines.append(f"  総入札数       : {b['total']:>5}件")
            lines.append(f"  入札予定 (未確定): {b['scheduled']:>5}件")
            lines.append(f"  落札累計        : {b['win']:>5}件")
            lines.append(f"  落選累計        : {b['lose']:>5}件")
            wr = f"{b['win_rate']}%" if b['win_rate'] is not None else "—"
            lines.append(f"  落札率          : {wr:>6}")
        lines.append(f"  本日 入札登録  : {b['today']:>5}件")

    lines.append(sep)

    # アラート
    alerts = []
    if c.get("pending", 0) > 50:
        alerts.append(f"🚨 未判断が{c['pending']}件たまっています → CEO確認タブを開いてください")
    if b.get("scheduled", 0) > 10:
        alerts.append(f"⚠️  入札予定が{b['scheduled']}件あります → 締切確認を忘れずに")
    if alerts:
        lines.append("【アラート】")
        lines.extend(f"  {a}" for a in alerts)
        lines.append(sep)

    return "\n".join(lines)


def post_to_slack(text: str) -> bool:
    """Slack Webhookに投稿する（SLACK_WEBHOOK_URL環境変数が必要）。"""
    import json
    import urllib.request

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        # coin_business/.env から読む
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("SLACK_WEBHOOK_URL="):
                    webhook_url = line.split("=", 1)[1].strip().strip('"')
                    break

    if not webhook_url:
        print("⚠️  SLACK_WEBHOOK_URL が設定されていません。Slack通知をスキップします。")
        return False

    payload = json.dumps({"text": f"```\n{text}\n```"}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"⚠️  Slack送信エラー: {e}")
        return False


def safe_print(text: str) -> None:
    """Windowsのcp932でも安全に出力する（絵文字はASCII代替に変換）。"""
    import sys
    try:
        print(text)
    except UnicodeEncodeError:
        # cp932環境: 絵文字などを除去して出力
        safe = text.encode(sys.stdout.encoding or "cp932", errors="replace").decode(
            sys.stdout.encoding or "cp932", errors="replace"
        )
        print(safe)


def main():
    parser = argparse.ArgumentParser(description="coin business KPI report")
    parser.add_argument("--slack", action="store_true", help="Slack notify")
    parser.add_argument("--today", action="store_true", help="today only")
    args = parser.parse_args()

    kpi = fetch_kpi()
    report = format_report(kpi, today_only=args.today)

    safe_print(report)

    if args.slack:
        ok = post_to_slack(report)
        if ok:
            safe_print("Slack sent OK")


if __name__ == "__main__":
    main()
