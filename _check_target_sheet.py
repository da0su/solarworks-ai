#!/usr/bin/env python3
"""CEO指定スプシ gid=1447646534 を読む"""
import sys, io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import gspread

SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
TARGET_GID = 1447646534
CREDS = r"C:\Users\infoa\Documents\solarworks-ai\credentials\sheets_service_account.json"

gc = gspread.service_account(filename=CREDS)
sh = gc.open_by_key(SPREADSHEET_ID)
print(f"Spreadsheet: {sh.title}")
print()
print("All worksheets with gid:")
target_ws = None
for ws in sh.worksheets():
    print(f"  gid={ws.id} | title={ws.title} (rows={ws.row_count}, cols={ws.col_count})")
    if ws.id == TARGET_GID:
        target_ws = ws

if target_ws is None:
    print(f"\nERROR: gid={TARGET_GID} not found")
    sys.exit(1)

print(f"\n=== Target sheet: {target_ws.title} ===")
vals = target_ws.get_all_values()
non_empty = [(i+1, r) for i,r in enumerate(vals) if any(c.strip() for c in r)]
print(f"Non-empty rows: {len(non_empty)}")
for rownum, row in non_empty[:50]:
    # Truncate long cells
    truncated = [c[:40] + "..." if len(c) > 40 else c for c in row[:15]]
    print(f"  [{rownum}] {truncated}")
