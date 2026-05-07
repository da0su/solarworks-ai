"""Cookie audit (P0-1 verification helper).

各 chrome profile の Cookies SQLite DB に rakuten session cookie が
存在するか調査する。CEO への報告用。
"""
import sqlite3
import sys
from pathlib import Path

# 2026-05-07: 楽天は OAuth/SSO に移行 (OSSO/ODID/Im/Re) — Rses/Raut は廃止
# OSSO @ login.account.rakuten.com = 主 SSO session
# Im @ .id.rakuten.co.jp = id auth token
# Re/Rg/Rz @ .rakuten.co.jp = Rakuten session
# s_user @ room.rakuten.co.jp = ROOM session marker
SESSION = ("OSSO", "ODID", "Im", "Re", "Rg", "Rz", "s_user", "Rses", "Raut", "rr_session", "Rat")
DATA = Path(__file__).resolve().parents[1] / "rakuten-room" / "bot" / "data"

profiles = [
    "chrome_profile",            # legacy
    "chrome_profile_post",
    "chrome_profile_like",
    "chrome_profile_followback",
    "chrome_profile_follow",
]

print("=== Cookie audit (rakuten host_key) ===")
for prof in profiles:
    db = DATA / prof / "Default" / "Network" / "Cookies"
    if not db.exists():
        print(f"  {prof}: (DB not found)")
        continue
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        rows = con.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%rakuten%'"
        ).fetchall()
        con.close()
        names = [r[0] for r in rows]
        sess = [n for n in names if n in SESSION]
        size_kb = db.stat().st_size // 1024
        print(f"  {prof}: db={size_kb}KB total_rakuten_cookies={len(names)} session_present={sess}")
    except Exception as e:
        print(f"  {prof}: ERROR {e}")
