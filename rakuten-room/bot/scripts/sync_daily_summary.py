"""daily_summary を post_queue から強制 refresh + spreadsheet 同期.

CEO 5/10 指示: 「投稿が止まっている」(実は spreadsheet 表示だけ古い)
真因: daily_summary の更新が Batch1 の 09:00 で stop していた.

10分毎にこの script を回すことで:
- post_queue から today の posted/failed/skipped を集計
- daily_summary を upsert
- daily_log_writer.py を呼んで spreadsheet を最新化
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DB = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
DAILY_LOG_WRITER = REPO_ROOT / "ops" / "sheets" / "daily_log_writer.py"


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    c = sqlite3.connect(str(DB))
    posted = c.execute("SELECT COUNT(*) FROM post_queue WHERE status='posted' AND posted_at LIKE ?", (f"{today}%",)).fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM post_queue WHERE status='failed' AND queue_date=?", (today,)).fetchone()[0]
    skipped = c.execute("SELECT COUNT(*) FROM post_queue WHERE status='skipped' AND queue_date=?", (today,)).fetchone()[0]
    planned = c.execute("SELECT COUNT(*) FROM post_queue WHERE queue_date=?", (today,)).fetchone()[0]

    c.execute("""
        INSERT INTO daily_summary (summary_date, planned, posted, failed, skipped, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(summary_date) DO UPDATE SET
            planned = excluded.planned,
            posted = excluded.posted,
            failed = excluded.failed,
            skipped = excluded.skipped,
            updated_at = datetime('now','localtime')
    """, (today, planned, posted, failed, skipped))
    c.commit()
    c.close()
    print(f"[OK] daily_summary {today}: posted={posted} failed={failed} skipped={skipped} planned={planned}")

    # Spreadsheet 同期
    r = subprocess.run([sys.executable, str(DAILY_LOG_WRITER)], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        print("[OK] daily_log_writer succeeded")
        out = r.stdout or ""
        for line in out.splitlines()[-3:]:
            print(f"  {line}")
    else:
        print(f"[ERR] daily_log_writer rc={r.returncode}: {(r.stderr or '')[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
