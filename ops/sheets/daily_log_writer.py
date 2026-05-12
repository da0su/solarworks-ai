#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
楽天ROOM デイリーログ自動記入スクリプト

毎日23:30に実行し、room_bot.db + follow_history.json + like_history.json から
当日の実績を集計して、Google スプレッドシートの「楽天ROOM_デイリーログ」シートに書き込む。

必要:
  - pip install gspread
  - credentials/sheets_service_account.json (Google Cloud サービスアカウントキー)
  - スプシの共有設定でサービスアカウントのメールを「編集者」に追加

使い方:
  python ops/sheets/daily_log_writer.py              # 当日分を記入
  python ops/sheets/daily_log_writer.py --date 2026-04-10  # 指定日を記入
  python ops/sheets/daily_log_writer.py --dry-run     # 書き込みせず確認のみ
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, date
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOM_BOT_DB = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
FOLLOW_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json"
LIKE_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json"
CREDENTIALS_PATH = REPO_ROOT / "credentials" / "sheets_service_account.json"

SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
SHEET_NAME = "楽天ROOM_デイリーログ"
FOLLOW_RPA_LOG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
ROOM_BOT_V5_DB = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"


def get_posted_count(target_date: str) -> int:
    """room_bot.db daily_summary から投稿数を取得"""
    if not ROOM_BOT_DB.exists():
        return 0
    try:
        conn = sqlite3.connect(str(ROOM_BOT_DB))
        row = conn.execute(
            "SELECT posted FROM daily_summary WHERE summary_date = ?",
            (target_date,)
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def get_follow_count(target_date: str) -> int:
    """follow_history.json + VM follow_rpa_log.json から当日のフォロー数をカウント.

    2026-05-12 真因修正: source='skip_discover' は技術的な「Rakuten 側で既フォロー判定 → 再試行回避用」
    の記録であり、実フォロー行動ではない. CEO スプシ整合性のため除外する.
    """
    count = 0

    # ホストPC側のfollow_history.json
    if FOLLOW_HISTORY.exists():
        try:
            data = json.loads(FOLLOW_HISTORY.read_text(encoding="utf-8"))
            for entry in data:
                dt = entry.get("followed_at", "")[:10]
                if dt != target_date:
                    continue
                # 2026-05-12 skip_discover 除外 (実フォローではない)
                if entry.get("source") == "skip_discover":
                    continue
                count += 1
        except Exception:
            pass

    # VM側のfollow_rpa_log.json（共有フォルダ経由）
    vm_log = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
    if vm_log.exists():
        try:
            logs = json.loads(vm_log.read_text(encoding="utf-8"))
            for entry in logs:
                dt = entry.get("timestamp", "")[:10]
                if dt == target_date:
                    count += entry.get("success", 0)
        except Exception:
            pass

    return count


def get_followback_count(target_date: str) -> int:
    """follow_log (room_bot_v5.db) から当日のフォローバック数をカウント"""
    if not ROOM_BOT_V5_DB.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(ROOM_BOT_V5_DB))
        row = conn.execute(
            "SELECT COUNT(*) FROM follow_log WHERE action='followback' AND DATE(followed_at)=?",
            (target_date,)
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def get_like_count(target_date: str) -> int:
    """like_history.json から当日のライク数をカウント"""
    if not LIKE_HISTORY.exists():
        return 0
    try:
        data = json.loads(LIKE_HISTORY.read_text(encoding="utf-8"))
        count = 0
        for entry in data:
            dt = entry.get("liked_at", "")[:10]
            if dt == target_date:
                count += 1
        return count
    except Exception:
        return 0


def find_row_for_date(worksheet, target_date: str) -> int | None:
    """シート内でtarget_dateに対応する行番号を探す"""
    col_a = worksheet.col_values(1)  # A列の全値
    # 日付フォーマットを揃える
    target_variants = [
        target_date,                          # 2026-04-10
        target_date.replace("-", "/"),         # 2026/04/10
        datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y/%m/%d"),  # 2026/04/10
    ]
    for i, val in enumerate(col_a):
        for variant in target_variants:
            if variant in str(val):
                return i + 1  # 1-indexed
    return None


def write_to_sheet(target_date: str, posted: int, follow: int, like: int, fb: int | None = None, dry_run: bool = False):
    """Google Sheets に実績を書き込む"""
    import gspread

    if not CREDENTIALS_PATH.exists():
        print(f"[ERROR] credentials not found: {CREDENTIALS_PATH}")
        print("  → Google Cloud Console でサービスアカウントのJSONキーを取得し、上記パスに保存してください")
        return False

    gc = gspread.service_account(filename=str(CREDENTIALS_PATH))
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)

    row = find_row_for_date(ws, target_date)
    if row is None:
        print(f"[ERROR] date {target_date} not found in sheet")
        return False

    print(f"  target row: {row} (date={target_date})")
    print(f"  values: posted={posted}, follow={follow}, like={like}")

    if dry_run:
        print(f"  [DRY-RUN] would write C{row}={posted}, F{row}={follow}, I{row}={like}, L{row}=FB")
        return True

    # C列=投稿実績, F列=フォロー実績, I列=ライク実績, L列=FB実績
    ranges = [
        {"range": f"C{row}", "values": [[posted]]},
        {"range": f"F{row}", "values": [[follow]]},
        {"range": f"I{row}", "values": [[like]]},
    ]
    if fb is not None:
        ranges.append({"range": f"L{row}", "values": [[fb]]})
    ws.batch_update(ranges, value_input_option="USER_ENTERED")

    print(f"  [OK] C{row}={posted}, F{row}={follow}, I{row}={like}" + (f", L{row}={fb}" if fb is not None else "") + " written")
    return True


def read_goals_from_sheet(target_date: str) -> dict | None:
    """スプシから目標値を読み取る（翌日計画立案用）"""
    import gspread

    if not CREDENTIALS_PATH.exists():
        return None

    gc = gspread.service_account(filename=str(CREDENTIALS_PATH))
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)

    row = find_row_for_date(ws, target_date)
    if row is None:
        return None

    # B列=投稿目標, E列=フォロー目標, H列=ライク目標
    post_goal = ws.cell(row, 2).value    # B列
    follow_goal = ws.cell(row, 5).value  # E列
    like_goal = ws.cell(row, 8).value    # H列

    return {
        "date": target_date,
        "row": row,
        "post_goal": int(post_goal) if post_goal else 0,
        "follow_goal": int(follow_goal) if follow_goal else 0,
        "like_goal": int(like_goal) if like_goal else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="楽天ROOM デイリーログ自動記入")
    parser.add_argument("--date", type=str, default=None, help="対象日 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="書き込みせず確認のみ")
    parser.add_argument("--read-goals", type=str, default=None, help="指定日の目標を読み取る")
    args = parser.parse_args()

    if args.read_goals:
        goals = read_goals_from_sheet(args.read_goals)
        if goals:
            print(json.dumps(goals, ensure_ascii=False, indent=2))
        else:
            print("[ERROR] goals not found")
        return

    target = args.date or date.today().strftime("%Y-%m-%d")
    print(f"=== 楽天ROOM デイリーログ記入: {target} ===")

    posted = get_posted_count(target)
    follow = get_follow_count(target)
    like = get_like_count(target)
    fb = get_followback_count(target)

    print(f"  posted:     {posted}")
    print(f"  follow:     {follow}")
    print(f"  like:       {like}")
    print(f"  followback: {fb}")

    success = write_to_sheet(target, posted, follow, like, fb=fb, dry_run=args.dry_run)
    if success:
        print(f"\n  DONE: {target} の実績をスプシに記入{'（dry-run）' if args.dry_run else ''}完了")
    else:
        print(f"\n  FAILED: 書き込みに失敗しました")


if __name__ == "__main__":
    main()
