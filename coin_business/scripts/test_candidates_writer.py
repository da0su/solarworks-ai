"""
candidates_writer の4カラム追加を少量dry-runでテスト
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from candidates_writer import write_candidates, _fetch_ref_columns

# ── Step 1: ref_lookup 単体テスト ────────────────────────────────
print("=== _fetch_ref_columns テスト ===")
mgmt_nos = ['001001', '002101', '001889']
lookup = _fetch_ref_columns(mgmt_nos)
for mgmt, cols in lookup.items():
    print(f"  [{mgmt}]")
    for k, v in cols.items():
        print(f"    {k}: {v}")

# ── Step 2: write_candidates dry-run ─────────────────────────────
print("\n=== write_candidates dry-run テスト（3件）===")
test_lots = [
    {
        "management_no":   "001001",
        "source":          "ebay",
        "auction_house":   "eBay",
        "auction_id":      "test_001",
        "lot_title":       "1904 US $20 Liberty Gold NGC MS62",
        "lot_number":      "1",
        "lot_url":         "https://www.ebay.com/itm/168269238944",
        "current_price":   165000,
        "estimated_cost_jpy": 183700,
        "buy_limit_jpy":   511946,
        "estimated_margin_pct": 64.1,
        "coin_match_status": "matched",
        "match_score":     1.0,
        "dedup_key":       "test_dry_run_001001",
    },
    {
        "management_no":   "002101",
        "source":          "ebay",
        "auction_house":   "eBay",
        "auction_id":      "test_002",
        "lot_title":       "V3 Great Britain 1900 Silver 6 Pence NGC MS64",
        "lot_number":      "2",
        "lot_url":         "https://www.ebay.com/itm/206163730168",
        "current_price":   5400,
        "estimated_cost_jpy": 8910,
        "buy_limit_jpy":   128245,
        "estimated_margin_pct": 92.3,
        "coin_match_status": "matched",
        "match_score":     1.0,
        "dedup_key":       "test_dry_run_002101",
    },
]

result = write_candidates(test_lots, dry_run=True, skip_notify=True)
print(f"\n結果: {result}")

# dry-run後にlotに4カラムが付いているか確認
print("\n4カラム充填確認:")
for lot in test_lots:
    mgmt = lot.get('management_no')
    r1_20k = lot.get('ref1_buy_limit_20k_jpy')
    r1_15  = lot.get('ref1_buy_limit_15pct_jpy')
    r2_20k = lot.get('ref2_buy_limit_20k_jpy')
    r2_15  = lot.get('ref2_buy_limit_15pct_jpy')
    ok = all(v is not None for v in [r1_20k, r1_15, r2_20k, r2_15])
    mark = '✓' if ok else '✗'
    print(f"  [{mark}] {mgmt}: ref1_20k={r1_20k}, ref1_15={r1_15}, ref2_20k={r2_20k}, ref2_15={r2_15}")
