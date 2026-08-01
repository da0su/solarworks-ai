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
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, date, timezone, timedelta
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


def ensure_row_for_date(worksheet, target_date: str, dry_run: bool = False) -> int | None:
    """target_date の行が無ければ末尾に追加して行番号を返す (冪等)。

    2026-08-01 CEO 指示で追加。当日行が無いと実績が一切記録できず、
    「投稿はしているのに記録が残らない」状態になっていた
    (7/26-7/31 の投稿列が全て 0 だった問題と併せて発覚)。

    設計上の約束:
      - 既に行があれば**何もしない**。CEO が手動で行を追加した場合と共存する。
      - 目標列 (B/E/H/K) は直近営業行の値を引き継ぐだけの暫定値。
        CEO が上書きしたらそれが正。こちらから再書き込みはしない。
      - 実績列は空のままにして、通常の write_to_sheet に書かせる。
    """
    d = datetime.strptime(target_date, "%Y-%m-%d").date()
    # 【安全弁1a】JST を明示 (Codex 指摘2)。ローカル TZ に依存すると、
    # 実行環境の TZ が JST でない場合に「当日なのに SKIP」= 未記録の誤失敗になる。
    today = datetime.now(timezone(timedelta(hours=9))).date()

    # 【安全弁1】自動追加は「今日」だけに限定する (Codex 指摘1)。
    # タイムゾーンずれ・引数ミス・バグで未来日や過去日の行を勝手に生やすと
    # 誤集計の温床になる。今日以外は追加せず、従来どおり失敗として返す。
    if d != today:
        print(f"  [SKIP] {target_date} は当日ではないため自動追加しません "
              f"(自動追加は当日のみ / 必要なら手動で行を追加)")
        return None

    col_a = worksheet.col_values(1)
    date_slash = d.strftime("%Y/%m/%d")

    # 直近の日付行 (= 目標値と書式の引き継ぎ元) を探す
    last_row_idx, last_row_vals = None, None
    for i, val in enumerate(col_a):
        if re.match(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}", str(val).strip()):
            last_row_idx = i + 1
    if last_row_idx:
        last_row_vals = worksheet.row_values(last_row_idx)
    else:
        # 引き継ぎ元が無くても行の追加自体は止めない (Codex 指摘3)。
        # シート初期化直後などに「永久に記録できない」状態を作らない。
        # 目標列は空になるので、CEO が入れるまで達成率は判定されないだけ。
        print("  [WARN] 日付行が無く目標を引き継げません。目標列は空で追加します")

    def _carry(idx: int) -> str:
        """直近行の目標値を引き継ぐ (取れなければ空)。

        last_row_vals は冒頭で None に初期化済みで、下の `if last_row_vals and`
        でガードしているため未定義参照は起こらない (2026-08-01 実測確認)。
        引き継ぎ元ゼロのシートを模擬しても NameError は発生せず、
        後段の再探索ガードが None を返して write_to_sheet が False になる
        = 虚偽成功にはならない。
        """
        try:
            return last_row_vals[idx] if last_row_vals and len(last_row_vals) > idx else ""
        except Exception:
            return ""

    # A=日付, B=目標投稿, E=目標フォロー, H=目標ライク, K=目標FB (実績列は空)
    new_row = [""] * 12
    new_row[0] = date_slash
    new_row[1] = _carry(1)    # B 目標投稿
    new_row[4] = _carry(4)    # E 目標フォロー
    new_row[7] = _carry(7)    # H 目標ライク
    new_row[10] = _carry(10)  # K 目標FB

    if dry_run:
        print(f"  [DRY-RUN] would append row for {date_slash}: {new_row[:11]}")
        return None

    # 【安全弁2】append_row 1回で書き切る (Codex 指摘1・4・5)。
    # 当初 insert_row(inherit_from_before=True) で数式を引き継ぐ実装にしたが、
    # このオプションが継承するのは書式・データ検証のみで数式はコピーされない。
    # 実際にシートを FORMULA レンダリングで確認したところ、D/G/J の達成率数式は
    # 111行中1行 (7/29) にしかなく、大半の行は空。つまり引き継ぐべき数式は無い。
    # → 支配的な行の形 (数式なし) をそのまま踏襲する。
    # 1リクエストで完結するので、途中失敗で日付だけの不完全行が残ることもない。
    worksheet.append_row(new_row, value_input_option="USER_ENTERED")

    # 【安全弁3】行番号は len() で推測せず必ず再探索する (Codex 指摘5)。
    row = find_row_for_date(worksheet, target_date)
    if row is None:
        print(f"  [ERROR] 行を追加したが {target_date} を再検出できません "
              f"(日付フォーマット不一致の可能性)")
        return None
    print(f"  [OK] {date_slash} の行を追加 (row={row}, 目標と書式は直近行から引き継ぎ)")
    return row


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
        # 行が無ければ自動追加してから書き込む (記録漏れを作らない)
        print(f"  [INFO] date {target_date} の行が無いため追加します")
        row = ensure_row_for_date(ws, target_date, dry_run=dry_run)
        if row is None:
            # dry_run でも「書き込めていない」事実は変わらないので False を返す。
            # ここで True を返すと未記録なのに成功扱いになる (Codex 指摘2)。
            print(f"[ERROR] date {target_date} の行が無く、書き込めませんでした"
                  + (" (dry-run)" if dry_run else ""))
            return False

    print(f"  target row: {row} (date={target_date})")
    print(f"  values: posted={posted}, follow={follow}, like={like}")

    if dry_run:
        print(f"  [DRY-RUN] would write C{row}={posted}, F{row}={follow}, I{row}={like}, L{row}=FB")
        return True

    # C列=投稿実績, F列=フォロー実績, I列=ライク実績, L列=FB実績
    # 2026-05-12 CEO 指示: 累積 N/O/P/Q 列は formula =前日+当日実績 で自動計算
    # → 当 writer は実績 (C/F/I/L) のみ書き込み. N/O/P/Q は触れない (formula 維持).
    # これにより Rakuten 公式 vs スプシ累積の差 = 過去 実績入力の誤り の診断ツールになる.
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
