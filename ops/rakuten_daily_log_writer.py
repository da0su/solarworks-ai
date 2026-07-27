#!/usr/bin/env python3
"""
楽天ROOM デイリーログ 自動記入スクリプト

毎日23:30実行想定:
1. room_bot.db の daily_summary から当日分データ取得
2. Google Spreadsheet「楽天ROOM_デイリーログ」タブに追記
3. Slack 報告

スケジューリング: Cron または Windows Task Scheduler
実行コマンド: python ops/rakuten_daily_log_writer.py [--date YYYY-MM-DD] [--dry-run]
"""

import sqlite3
import argparse
import datetime
import json
import os
import sys
import pathlib
import urllib.request

# 設定
DB_PATH = "rakuten-room/bot/data/room_bot.db"
SPREADSHEET_ID = "1A-SszxzMKfg2Q5XfrS-2D_pMLTWdPafSVRwGA_rKr34"
TAB_GID = "1405318057"  # 楽天ROOM_デイリーログ

# 目標値（固定）
DEFAULT_TARGET_POST = 95
DEFAULT_TARGET_FOLLOW = 50
DEFAULT_TARGET_LIKE = 200


def get_daily_summary(target_date: str) -> dict | None:
    """DB から指定日のサマリーを取得"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT summary_date, planned, posted, failed, skipped FROM daily_summary WHERE summary_date = ?",
        (target_date,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "date": row[0],
        "planned": row[1],
        "posted": row[2],
        "failed": row[3],
        "skipped": row[4],
    }


def format_row(summary: dict) -> dict:
    """Spreadsheet 入力用のデータを生成"""
    date_str = summary["date"].replace("-", "/")
    note = f"失敗{summary['failed']}件・スキップ{summary['skipped']}件"
    return {
        "date": date_str,
        "target_post": DEFAULT_TARGET_POST,
        "actual_post": summary["posted"],
        "target_follow": DEFAULT_TARGET_FOLLOW,
        "actual_follow": 0,  # 現状計測なし
        "target_like": DEFAULT_TARGET_LIKE,
        "actual_like": 0,  # 現状計測なし
        "note": note,
    }


def slack_notify(message: str) -> None:
    """Slack に報告"""
    # CEO 2026-06-04 全Slack停止: state/SLACK_DISABLED がある間は送信しない
    import os as _os
    # CEO 2026-07-23 Slack 全報告終了 (critical 含め例外なし)
    if _os.path.exists(r"C:\Users\infoa\Documents\solarworks-ai\state\SLACK_FULL_STOP"):
        return
    if _os.path.exists(r"C:\Users\infoa\Documents\solarworks-ai\state\SLACK_DISABLED") \
            or _os.environ.get("SLACK_DISABLED") not in (None, "", "0", "false", "False"):
        return
    try:
        # Load token from env
        token = ""
        for path in [".env", "ops/notifications/.env"]:
            p = pathlib.Path(path)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if "SLACK_BOT_TOKEN" in line and "=" in line:
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if token:
                            break
            if token:
                break
        if not token:
            print("SLACK_BOT_TOKEN not found, skipping notification")
            return

        url = "https://slack.com/api/chat.postMessage"
        data = json.dumps({"channel": "C0AQASABVL7", "text": message}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Slack notification failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date", help="YYYY-MM-DD（未指定なら実行日）", default=None
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="取得のみ・スプシ書き込みなし"
    )
    args = parser.parse_args()

    target_date = args.date or datetime.date.today().strftime("%Y-%m-%d")
    print(f"Target date: {target_date}")

    summary = get_daily_summary(target_date)
    if not summary:
        msg = f"[WARN] 楽天ROOM daily_summary: {target_date} のデータなし"
        print(msg)
        slack_notify(f"⚠️ {msg}")
        sys.exit(1)

    row = format_row(summary)
    print(f"Data: {row}")

    if args.dry_run:
        print("[DRY-RUN] Would append to spreadsheet")
        return

    # 実際のスプシ書き込みは別スクリプト or Apps Script 経由
    # 現時点では実行ログを JSON で保存（後続のスプシ書き込みで利用）
    out_path = pathlib.Path(f"reports/rakuten_daily_log_{target_date}.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

    msg = (
        f"📊 楽天ROOM デイリーログ記入 {target_date}\n"
        f"  投稿: {row['actual_post']}/{row['target_post']}件\n"
        f"  備考: {row['note']}\n"
        f"  データ保存: {out_path}"
    )
    print(msg)
    slack_notify(msg)


if __name__ == "__main__":
    main()
