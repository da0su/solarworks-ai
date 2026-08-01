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

    # 2026-08-01 真因修正: Plan v6 の ranking_post (VM) は post_queue を経由しないため、
    # 上の posted は常に 0 になり、CEO のスプシに「投稿0件」を毎日書き込んでいた
    # (実際は投稿できている)。公開 ROOM API の真値を優先する。
    # 同じ誤りを patrol_v6 business.py でも修正済 (2026-07-29)。
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from ops.room_status import fetch_post_truth
        truth = fetch_post_truth()
        if truth and isinstance(truth.get("today_posted"), int):
            api_posted = truth["today_posted"]
            if api_posted != posted:
                print(f"[fix] posted {posted} (post_queue) -> {api_posted} (public API truth)")
            posted = api_posted
            planned = max(planned, posted)   # 実績が計画を超える場合の整合
        else:
            print("[WARN] 公開API 到達不能。post_queue の値のまま書き込みはしない")
            posted = None   # 取得不能を 0 と書かない (偽の 0 を残さない)
    except Exception as e:
        print(f"[WARN] post 真値取得に失敗: {type(e).__name__}: {e}")
        posted = None

    if posted is None:
        # 真値が取れない時は DB もシートも更新しない (誤った 0 で上書きしない)
        c.close()
        print("[SKIP] post 実績が確定できないため同期を中止")
        return

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
    # 2026-05-29 fix: encoding='utf-8' を明示してcp932 UnicodeDecodeError を防ぐ
    r = subprocess.run([sys.executable, str(DAILY_LOG_WRITER)], capture_output=True, text=True, encoding='utf-8', timeout=60)
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
