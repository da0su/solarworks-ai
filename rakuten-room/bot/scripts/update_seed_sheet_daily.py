"""日次: 既存 seed_investigation.json をスプシ 06_フォロワー調査 タブに同期.

CEO 2026-05-10 指示: 「スプシの 06_フォロワー調査 の入力も毎日更新しなさい」.

設計:
- harvest や browser は使わない (高速・10秒以内で完了)
- seed_investigation.json (週次 investigate_seeds.py が更新する master) を読む
- 日本語ヘッダーで spreadsheet に書く
- F 列 (followed_overlap) は json に記録された値を使う

実行: 毎日 06:30 (DailyReset 後 / Dashboard Morning 前)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

import config
from logger.logger import setup_logger

logger = setup_logger()

REPO_ROOT = Path(__file__).resolve().parents[3]
INVESTIGATION_FILE = config.DATA_DIR / "seed_investigation.json"
SSOT_SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
GSPREAD_CREDS = REPO_ROOT / "credentials" / "sheets_service_account.json"
SHEET_NAME = "06_フォロワー調査"


def main():
    if not INVESTIGATION_FILE.exists():
        logger.error(f"investigation file not found: {INVESTIGATION_FILE}")
        return 1

    rows = json.loads(INVESTIGATION_FILE.read_text(encoding="utf-8"))
    logger.info(f"loaded {len(rows)} rows from {INVESTIGATION_FILE.name}")

    try:
        import gspread
    except ImportError:
        logger.error("gspread missing"); return 1
    if not GSPREAD_CREDS.exists():
        logger.error(f"creds missing: {GSPREAD_CREDS}"); return 1

    try:
        gc = gspread.service_account(filename=str(GSPREAD_CREDS))
        sh = gc.open_by_key(SSOT_SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET_NAME)
            ws.clear()
        except Exception:
            ws = sh.add_worksheet(title=SHEET_NAME, rows=500, cols=12)

        # 日本語ヘッダー (CEO 2026-05-10 指示)
        header = [
            "インフルエンサー名", "カテゴリ", "URL",
            "私のフォロー状況", "フォロワー数",
            "私がフォローした被り数",  # F: overlap
            "彼らのフォロー数",         # G: their following count
            "ボタン有無", "備考", "調査日時",
        ]
        values = [header]
        ts = datetime.now().isoformat(timespec="seconds")
        status_jp = {"following": "フォロー中", "not_following": "未フォロー", "error": "エラー", "unknown": "不明", "404": "404"}

        for r in rows:
            values.append([
                r.get("seed_user", ""),
                r.get("category", ""),
                r.get("url", ""),
                status_jp.get(r.get("my_status", "unknown"), r.get("my_status", "")),
                r.get("follower_count", 0),
                r.get("followed_overlap", 0),
                r.get("following_count", 0),
                "TRUE" if r.get("has_button") else "FALSE",
                r.get("notes", ""),
                ts,
            ])
        ws.update("A1", values)
        try:
            ws.format("A1:J1", {"textFormat": {"bold": True}})
        except Exception:
            pass

        # Stats
        sum_F = sum(int(r.get("followed_overlap", 0) or 0) for r in rows)
        sum_E = sum(int(r.get("follower_count", 0) or 0) for r in rows)
        n_following = sum(1 for r in rows if r.get("my_status") == "following")
        n_not = sum(1 for r in rows if r.get("my_status") == "not_following")
        logger.info(f"[OK] {len(rows)} rows synced. sum_F={sum_F:,} sum_E={sum_E:,} following={n_following} not_following={n_not}")
        return 0
    except Exception as e:
        logger.error(f"sheet write err: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
