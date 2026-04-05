"""4カラム追加の動作確認 (coin_business/ から実行)"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from scripts.candidates_writer import write_candidates, _fetch_ref_columns

# ── ref_lookup 単体確認 ─────────────────────────────────────────
print("=== _fetch_ref_columns 確認 ===")
lookup = _fetch_ref_columns(['001001', '002101', '001889'])
for mgmt, cols in lookup.items():
    r1 = cols.get('ref1_buy_limit_20k_jpy')
    r2 = cols.get('ref2_buy_limit_20k_jpy')
    ok = r1 is not None and r2 is not None
    print(f"  [{'✓' if ok else '✗'}] {mgmt}: ref1_20k={r1:,} / ref2_20k={r2:,}" if ok else f"  [✗] {mgmt}: NULL")

# ── write_candidates dry-run ─────────────────────────────────────
print("\n=== write_candidates dry-run（2件）===")
test_lots = [
    {
        "management_no": "001001",
        "source": "ebay", "auction_house": "eBay",
        "auction_id": "test_dry_4col_001", "lot_number": "1",
        "lot_title": "1904 US $20 Liberty Gold NGC MS62",
        "lot_url": "https://www.ebay.com/itm/test",
        "current_price": 165000, "estimated_cost_jpy": 183700,
        "buy_limit_jpy": 511946, "estimated_margin_pct": 64.1,
        "coin_match_status": "matched", "match_score": 1.0,
        "dedup_key": "dry_run_test_001001_4col",
    },
    {
        "management_no": "002101",
        "source": "ebay", "auction_house": "eBay",
        "auction_id": "test_dry_4col_002", "lot_number": "2",
        "lot_title": "V3 Great Britain 1900 Silver 6 Pence NGC MS64",
        "lot_url": "https://www.ebay.com/itm/test2",
        "current_price": 5400, "estimated_cost_jpy": 8910,
        "buy_limit_jpy": 128245, "estimated_margin_pct": 92.3,
        "coin_match_status": "matched", "match_score": 1.0,
        "dedup_key": "dry_run_test_002101_4col",
    },
]

result = write_candidates(test_lots, dry_run=True, skip_notify=True)
print(f"判定結果: OK={result['ok']}, NG={result['ng']}, REVIEW={result['review']}")

# 4カラムがlotに付与されているか確認
print("\n4カラム付与確認:")
all_ok = True
for lot in test_lots:
    mgmt = lot.get('management_no')
    vals = {k: lot.get(k) for k in ['ref1_buy_limit_20k_jpy','ref1_buy_limit_15pct_jpy',
                                      'ref2_buy_limit_20k_jpy','ref2_buy_limit_15pct_jpy']}
    ok = all(v is not None for v in vals.values())
    all_ok = all_ok and ok
    mark = '✓' if ok else '✗'
    if ok:
        print(f"  [{mark}] {mgmt}: ref1_20k={vals['ref1_buy_limit_20k_jpy']:,} / "
              f"ref1_15={vals['ref1_buy_limit_15pct_jpy']:,} / "
              f"ref2_20k={vals['ref2_buy_limit_20k_jpy']:,} / "
              f"ref2_15={vals['ref2_buy_limit_15pct_jpy']:,}")
    else:
        print(f"  [{mark}] {mgmt}: NULLあり → {vals}")

print(f"\n{'✅ 4カラム全て正常' if all_ok else '❌ NULLあり — 要調査'}")
