# -*- coding: utf-8 -*-
"""
apply_migration_011.py
======================
migration 011_cert_columns.sql を Supabase に適用するスクリプト。

使い方:
  cd coin_business
  python scripts/apply_migration_011.py

※ 直接 PostgreSQL 接続が必要なため、.env に以下のいずれかを追加してください:
   SUPABASE_DB_PASSWORD=xxxx   (Supabase Dashboard > Settings > Database > Database password)
   または
   DATABASE_URL=postgresql://postgres:xxxx@db.sgitwndpyxzsslyyvpyn.supabase.co:5432/postgres

接続できない場合は coin_business/migrations/011_cert_columns.sql を
Supabase SQL Editor に貼り付けて手動実行してください。
"""

from __future__ import annotations
import os
import sys
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# .env 読み込み
env: dict = {}
for _env_path in [ROOT / ".env", ROOT.parent / ".env"]:
    if _env_path.exists():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())

DB_PASS  = env.get("SUPABASE_DB_PASSWORD", "")
DB_URL   = env.get("DATABASE_URL", "")
SB_URL   = env.get("SUPABASE_URL", "")
SB_KEY   = env.get("SUPABASE_KEY", "")
REF      = SB_URL.replace("https://", "").split(".")[0] if SB_URL else ""

SQL = """
ALTER TABLE daily_candidates
  ADD COLUMN IF NOT EXISTS grading_company TEXT,
  ADD COLUMN IF NOT EXISTS cert_number     TEXT;

CREATE INDEX IF NOT EXISTS idx_dc_cert_number
  ON daily_candidates (cert_number)
  WHERE cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dc_grading_company
  ON daily_candidates (grading_company)
  WHERE grading_company IS NOT NULL;
"""


def check_already_applied() -> bool:
    """REST API で列存在チェック（適用済みかどうか）"""
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/daily_candidates?select=grading_company,cert_number&limit=1",
            headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "does not exist" in body or e.code == 400:
            return False
        raise


def apply_via_psycopg2(conn_str: str) -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        cur = conn.cursor()
        cur.execute(SQL)
        conn.commit()
        conn.close()
        return True
    except ImportError:
        print("[WARN] psycopg2 not installed. pip install psycopg2-binary")
        return False
    except Exception as e:
        print(f"[ERR] psycopg2 接続失敗: {e}")
        return False


def main():
    import io, sys as _sys
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=== migration 011_cert_columns apply script ===")

    # 1. check already applied
    print("[1/3] checking column existence...")
    if check_already_applied():
        print("OK - already applied (grading_company / cert_number columns exist)")
        return

    print("  -> not applied -> starting migration")

    # 2. try psycopg2 if DB credentials available
    conn_str = None
    if DB_URL:
        conn_str = DB_URL
    elif DB_PASS and REF:
        conn_str = f"postgresql://postgres:{DB_PASS}@db.{REF}.supabase.co:5432/postgres?sslmode=require"

    if conn_str:
        print("[2/3] trying ALTER TABLE via psycopg2...")
        if apply_via_psycopg2(conn_str):
            if check_already_applied():
                print("OK - migration 011 applied!")
                return
            else:
                print("WARN: columns still not visible after execution")
        else:
            print("WARN: psycopg2 connection failed")
    else:
        print("[2/3] SUPABASE_DB_PASSWORD / DATABASE_URL not found in .env")

    # 3. manual apply instructions
    sql_path = ROOT / "migrations" / "011_cert_columns.sql"
    print()
    print("=" * 55)
    print("ACTION REQUIRED: manual migration")
    print("=" * 55)
    print(f"SQL file: {sql_path}")
    print()
    print("Steps:")
    print("  1. Open https://supabase.com/dashboard")
    print("  2. SQL Editor -> New query")
    print("  3. Paste and Run:")
    print()
    print(SQL)
    print("=" * 55)


if __name__ == "__main__":
    main()
