"""debug: check why ROUND1 hits = 0"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from scripts.daily_scan import (load_staging, is_excluded_item, parse_ebay_title,
                                  match_round1, _known)

db = load_staging(verbose=False)

# Build index of staging by (cert, material, denomination, grade_num)
idx = {}
for c in db:
    if c['denomination'] == 'UNKNOWN' or not c['grade_num']:
        continue
    key = (c['cert_company'], c['material'], c['denomination'], c['grade_num'])
    idx.setdefault(key, []).append(c)

print(f'Staging unique (cert+mat+denom+grade) combos: {len(idx)}')
print('Sample keys:')
for k in list(idx.keys())[:10]:
    print(f'  {k}')

# Load today's scan output to avoid re-calling eBay API
# Just check staging denominations vs DENOM_PATTERNS
from scripts.daily_scan import DENOM_PATTERNS
ebay_denoms = {d for d, _ in DENOM_PATTERNS}
staging_denoms = set(c['denomination'] for c in db if c['denomination'] != 'UNKNOWN')
print()
print('Staging denoms that exist in DENOM_PATTERNS (potentially matchable):')
for d in sorted(staging_denoms & ebay_denoms):
    cnt = sum(1 for c in db if c['denomination'] == d)
    print(f'  {cnt:3d}  {d}')
print()
print('Staging denoms NOT in DENOM_PATTERNS (never matchable):')
for d in sorted(staging_denoms - ebay_denoms):
    cnt = sum(1 for c in db if c['denomination'] == d)
    print(f'  {cnt:3d}  {d}')
