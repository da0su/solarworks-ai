"""chrome_profile_post の全 rakuten cookie を詳細ダンプして session cookie を特定する."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "rakuten-room" / "bot" / "data"
db = DATA / "chrome_profile_post" / "Default" / "Network" / "Cookies"
print(f"DB: {db} exists={db.exists()}")

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
rows = con.execute("""
    SELECT host_key, name, length(value) AS raw_len, length(encrypted_value) AS enc_len,
           expires_utc, is_secure, is_httponly, samesite
    FROM cookies
    WHERE host_key LIKE '%rakuten%'
    ORDER BY length(encrypted_value) DESC, host_key, name
""").fetchall()
con.close()

EPOCH_OFFSET = 11644473600  # Chrome epoch (1601-01-01) → Unix
now = datetime.now(timezone.utc)
print(f"\n{'host_key':<28} {'name':<28} {'raw':>4} {'enc':>4} {'expires':<19} {'sec':<3} {'httpO':<5}")
print("-" * 105)
for r in rows:
    host_key, name, raw_len, enc_len, expires_utc, is_secure, is_httponly, samesite = r
    if expires_utc:
        try:
            exp_unix = expires_utc / 1_000_000 - EPOCH_OFFSET
            exp_dt = datetime.fromtimestamp(exp_unix, tz=timezone.utc)
            exp_str = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            exp_str = str(expires_utc)
    else:
        exp_str = "(session)"
    flag_sec = "y" if is_secure else "-"
    flag_http = "y" if is_httponly else "-"
    print(f"{host_key:<28} {name:<28} {raw_len:>4} {enc_len:>4} {exp_str:<19} {flag_sec:<3} {flag_http:<5}")

print(f"\n=== {len(rows)} rakuten cookies total ===")
print("\n[長い encrypted_value (>50bytes) かつ HttpOnly+Secure かつ未来 expiry のもの = session 候補]")
for r in rows:
    host_key, name, raw_len, enc_len, expires_utc, is_secure, is_httponly, samesite = r
    if enc_len and enc_len > 50 and is_secure and is_httponly:
        print(f"  ★ {host_key} / {name}  enc={enc_len}b")
