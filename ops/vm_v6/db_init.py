#!/usr/bin/env python3
"""room_bot_v6.db 初期化 (Plan v4 P4)."""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v6.db"
SCHEMA = Path(__file__).resolve().parent / "schema_v6.sql"


def init():
    if not SCHEMA.exists():
        print(f"[ERROR] schema not found: {SCHEMA}")
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    sql = SCHEMA.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()
    # 確認
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"[ok] {DB_PATH.name} created with tables: {tables}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(init())
