"""
chg033_near_miss_register.py
============================
CHG-033: 2026-04-03 市場調査結果をOBSERVATION 2件 + 市場状況メモ 1件として
ceo_review_log に登録する。

背景:
  - CHG-032 完了後、新規 CAP_BUY 候補 2〜3件を探索した（2026-04-03）
  - 金価格高騰（$3,000+/oz）により全ゴールドコインが buy_limit 超過
  - シルバーコインは日本市場価格が低すぎて採算不成立
  - ニアミス案件 2件を記録 → 次回スキャン時の参照用

登録内容:
  Entry A: 1883-CC Morgan Dollar PCGS MS65 (Non-GSA) — $9 over buy_limit
  Entry B: Elizabeth II Gold Sovereign NGC MS63 — APMEX 2026-03-22 near-miss
  Entry C: 市場状況メモ（ゴールド高騰バリア記録）
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client


def calc_profit(sell_jpy: int):
    """(buy_limit_jpy, roi_pct, total_cost_jpy, profit_jpy) or (None,None,None,None)"""
    YAHOO_FEE = 0.10
    CUSTOMS = 1.10
    FIXED_COST = 2_750
    MIN_PROFIT = 20_000
    MIN_MARGIN = 0.15
    revenue = sell_jpy * (1.0 - YAHOO_FEE)
    cost_limit = min(revenue - MIN_PROFIT, revenue * (1.0 - MIN_MARGIN))
    if cost_limit <= FIXED_COST:
        return None, None, None, None
    buy_jpy = int((cost_limit - FIXED_COST) / CUSTOMS)
    total_cost = int(buy_jpy * CUSTOMS + FIXED_COST)
    profit = int(revenue - total_cost)
    roi = round(profit / total_cost * 100, 1)
    return buy_jpy, roi, total_cost, profit


def main():
    c = get_client()
    now = datetime.now(timezone.utc).isoformat()
    USD_JPY = 150  # 計算用レート

    # ======================================================================
    # Entry A: 1883-CC Morgan Dollar PCGS MS65 (Non-GSA)
    # eBay: collectibles-currency 出品, $549.55 + $19.95 送料 = $569.50 total
    # Yahoo最新参照: ¥128,000 (2026-03-05, NGC MS65 GSA Hoard) → buy_limit $560
    # 差額: $569.50 - $560 = $9.50 over → CAP_NG (¥1,518 shortage)
    # 注意: eBay出品はPCGS非GSA vs Yahoo参照はNGC GSA Hoardで製品差あり
    #       GSA Hoardプレミアムなしなら期待売価 ¥70,000-90,000 → 実質 CAP_NG
    # ======================================================================
    sell_a_recent = 128_000   # 最新参照 (2026-03-05)
    sell_a_older  = 133_000   # 3〜6か月参照 (2025-12-07)
    buy_a_r, roi_a_r, cost_a_r, profit_a_r = calc_profit(sell_a_recent)
    buy_a_o, roi_a_o, cost_a_o, profit_a_o = calc_profit(sell_a_older)

    entry_a = {
        'item_id':                  'CHG033-MORGAN-1883CC-MS65-NEARMISS',
        'marketplace':              'ebay',
        'scan_date':                '2026-04-03',
        'review_bucket':            'CHG033',
        'duplicate_status':         'NEW',
        'title_snapshot':           '1883-CC Morgan Dollar PCGS MS65 (Carson City, Non-GSA)',
        'auction_house':            'EBAY',
        'source_group':             'ebay',
        'price_snapshot_usd':       569.50,
        'comparison_type':          'GRADE_DELTA',
        'yahoo_ref_title':          '【高鑑定:NGC MS65／信頼:GSAホード】1883年 カーソンシティ モルガンダラー CC MINT Morgan Dollar',
        'yahoo_ref_price_jpy':      128_000,
        'yahoo_ref_date':           '2026-03-05',
        'yahoo_ref_grade':          'MS65',
        'cap_bid_limit_jpy':        buy_a_r,
        'cap_bid_limit_usd':        round(buy_a_r / USD_JPY, 1) if buy_a_r else None,
        'estimated_sell_price_jpy': sell_a_recent,
        'total_cost_jpy':           cost_a_r,
        'expected_profit_jpy':      profit_a_r,
        'expected_roi_pct':         roi_a_r,
        'cap_judgment':             'CAP_NG',
        'cap_comment': (
            '[CHG-033 NEAR-MISS 2026-04-03] '
            'eBay listing: collectibles-currency, 1883-CC PCGS MS65, $549.55 + $19.95 shipping = $569.50 total. '
            'Most recent Yahoo reference: NGC MS65 GSA Hoard ¥128,000 (2026-03-05). '
            f'Calculated buy_limit (¥128k ref): ¥{buy_a_r:,} = ${round(buy_a_r/USD_JPY,1)}. '
            f'Gap: $569.50 - ${round(buy_a_r/USD_JPY,1)} = $9.50 over limit. Expected profit: ¥{profit_a_r:,} (¥{20000-profit_a_r:,} short of ¥20,000 minimum). '
            'With older ¥133,000 reference (2025-12-07): '
            f'buy_limit = ¥{buy_a_o:,} = ${round(buy_a_o/USD_JPY,1)} → profit ¥{profit_a_o:,} = technically CAP_BUY, '
            'but reference is 3-6 months old (lower time weight per CEO rule). '
            'CRITICAL PRODUCT DIFFERENCE: eBay coin is PCGS-graded, Non-GSA. '
            'Yahoo reference is NGC GSA Hoard (includes original GSA envelope/box). '
            'GSA Hoard premium in Japan market is significant: same-grade Non-GSA coin likely sells ¥70,000-90,000, not ¥128,000. '
            'If sell_est = ¥80,000: revenue = ¥72,000, cost_limit = ¥39,250, buy_limit = ¥33,182 = $221 → FAR below $569.50. '
            'VERDICT: CAP_NG. GSA vs Non-GSA product difference invalidates comparison. '
            'ACTION: Monitor for GSA Hoard NGC MS65 1883-CC listings below $560 on eBay from US/UK sellers.'
        ),
        'category':             'OBSERVATION',
        'evidence_status':      'Yahoo参照あり(GSA/非GSA製品差注意)',
        'marketing_status':     'OBSERVATION',
        'created_at':            now,
        'updated_at':            now,
    }

    result_a = c.table('ceo_review_log').insert(entry_a).execute()
    id_a = result_a.data[0]['id'] if result_a.data else 'unknown'
    print(f'Entry A inserted: id={id_a}')
    print(f'  1883-CC Morgan MS65: buy_limit=${round(buy_a_r/USD_JPY,1)} vs eBay $569.50 → profit=¥{profit_a_r:,} (¥{20000-profit_a_r:,} short)')

    # ======================================================================
    # Entry B: Elizabeth II Gold Sovereign NGC MS63 — APMEX watch
    # Yahoo参照: ¥270,000-271,000 (2026-02-08/2026-02-05)
    # buy_limit: ¥(270,000×0.90-20,000)/1.1 - 2,750/1.1 を計算
    # APMEX 2026-03-22 落札: $1,157.82 (< buy_limit $1,239) — 在庫なし
    # 現在eBay: $1,400-1,600 (buy_limit 超過)
    # ======================================================================
    sell_b = 270_000
    buy_b, roi_b, cost_b, profit_b = calc_profit(sell_b)

    entry_b = {
        'item_id':                  'CHG033-SOVEREIGN-ELIZII-MS63-WATCH',
        'marketplace':              'ebay',
        'scan_date':                '2026-04-03',
        'review_bucket':            'CHG033',
        'duplicate_status':         'NEW',
        'title_snapshot':           'Elizabeth II Gold Sovereign NGC MS63 (United Kingdom)',
        'auction_house':            'EBAY',
        'source_group':             'ebay',
        'price_snapshot_usd':       1400.0,   # 現時点のeBay最安値帯
        'comparison_type':          'YEAR_DELTA',
        'yahoo_ref_title':          'イギリス ソブリン金貨 エリザベス2世 NGC MS63 1968年',
        'yahoo_ref_price_jpy':      270_000,
        'yahoo_ref_date':           '2026-02-08',
        'yahoo_ref_grade':          'MS63',
        'cap_bid_limit_jpy':        buy_b,
        'cap_bid_limit_usd':        round(buy_b / USD_JPY, 1) if buy_b else None,
        'estimated_sell_price_jpy': sell_b,
        'total_cost_jpy':           cost_b,
        'expected_profit_jpy':      profit_b,
        'expected_roi_pct':         roi_b,
        'cap_judgment':             'CAP_NG',
        'cap_comment': (
            '[CHG-033 NEAR-MISS (GOLD) 2026-04-03] '
            'Yahoo references: NGC MS63 ¥270,000 (2026-02-08) and ¥271,000 (2026-02-05) — reliable, recent. '
            f'Calculated buy_limit (¥270k): ¥{buy_b:,} = ${round(buy_b/USD_JPY,1)}. '
            f'Expected profit if bought at limit: ¥{profit_b:,} at ROI {roi_b}%. '
            'NEAR-MISS EVENT: APMEX sold 1966 Elizabeth II Sovereign NGC MS63 at $1,157.82 on 2026-03-22 '
            f'— this is BELOW buy_limit ${round(buy_b/USD_JPY,1)} by ${round(buy_b/USD_JPY,1)-1157.82:.2f}. '
            'Would have been CAP_BUY (profit ~¥20,000+) if available. '
            'Current eBay market: $1,400-1,600 range (buy_limit exceeded by $161-361). '
            'ROOT CAUSE: Gold spot price ~$3,100+/oz in April 2026 (up from ~$2,400 in 2025). '
            'Sovereigns contain 0.2354 troy oz gold → gold content value alone = $730 at current spot. '
            'Numismatic premium over gold content has compressed as spot rose. '
            'WATCH TRIGGER: If gold corrects to ~$2,400/oz, eBay sovereign prices likely return to $900-1,100 range, '
            f'well below buy_limit ${round(buy_b/USD_JPY,1)}. '
            'ACTION: Monitor APMEX/eBay for Elizabeth II Sovereign NGC MS63 below $1,239 from US/UK sellers. '
            'Recommended re-check interval: weekly when gold spot is below $2,600/oz.'
        ),
        'category':             'OBSERVATION',
        'evidence_status':      'Yahoo参照あり(金価格高騰でNG・要Watch)',
        'marketing_status':     'OBSERVATION',
        'created_at':            now,
        'updated_at':            now,
    }

    result_b = c.table('ceo_review_log').insert(entry_b).execute()
    id_b = result_b.data[0]['id'] if result_b.data else 'unknown'
    print(f'Entry B inserted: id={id_b}')
    print(f'  Sovereign MS63: buy_limit=${round(buy_b/USD_JPY,1)} vs eBay $1,400-1,600 → CAP_NG (gold at $3,100+/oz)')

    # ======================================================================
    # Entry C: 市場状況メモ — 2026年4月 ゴールド高騰バリア記録
    # ======================================================================
    entry_c = {
        'item_id':                  'CHG033-MARKET-STATUS-202604-GOLD-BARRIER',
        'marketplace':              'ebay',
        'scan_date':                '2026-04-03',
        'review_bucket':            'CHG033',
        'duplicate_status':         'NEW',
        'title_snapshot':           '[市場メモ] 2026年4月 金価格高騰による仕入れ停滞記録',
        'auction_house':            'MARKET_NOTE',
        'source_group':             'ebay',
        'price_snapshot_usd':       3100.0,   # gold spot price (memo)
        'comparison_type':          'NONE',
        'yahoo_ref_title':          None,
        'yahoo_ref_price_jpy':      None,
        'yahoo_ref_date':           None,
        'yahoo_ref_grade':          None,
        'cap_bid_limit_jpy':        None,
        'cap_bid_limit_usd':        None,
        'estimated_sell_price_jpy': None,
        'total_cost_jpy':           None,
        'expected_profit_jpy':      None,
        'expected_roi_pct':         None,
        'cap_judgment':             'CAP_NG',
        'cap_comment': (
            '[CHG-033 MARKET STATUS 2026-04-03] '
            'STRUCTURAL BARRIER: Gold spot price ~$3,100+/oz as of 2026-04-03. '
            'This is causing all gold coin eBay prices to exceed buy_limits by wide margins. '
            'Affected products and gaps: '
            '(1) Elizabeth II Gold Sovereign NGC MS63: buy_limit $1,239, eBay $1,400-1,600 (gap +$161 to +$361). '
            '(2) 1914 GB Half-Sovereign NGC MS63: Yahoo ref ¥110,880, buy_limit ~$467, eBay $799+ (gap +$332+). '
            '(3) $20 Liberty Double Eagle NGC MS62: Yahoo ref ¥801,000, buy_limit $3,697, eBay $4,200-4,400 (gap +$500+). '
            'Silver coin situation: '
            '(4) Mexico Libertad 1oz PF70: Japan market ¥12,500-25,000 too low for minimum profit. '
            '(5) American Silver Eagle MS69: Japan market ¥20,405 → buy_limit negative. '
            '(6) 1883-CC Morgan Dollar PCGS MS65 (Non-GSA): eBay $569.50 vs buy_limit $560 ($9 over; also GSA product difference). '
            'RESUMPTION CONDITIONS: '
            'Gold coins will become viable when gold spot drops below ~$2,500/oz. '
            'Current gold trajectory (April 2026): uncertain, influenced by tariff policy and USD weakness. '
            'RECOMMENDED ACTIONS: '
            '(A) Monitor weekly: Elizabeth II Sovereign MS63 below $1,239 from US/UK sellers. '
            '(B) Monitor weekly: 1883-CC Morgan MS65 NGC GSA Hoard below $560 from US/UK sellers. '
            '(C) Expand Yahoo DB coverage for Silver coins (Britannia, Canadian Maple) to find viable silver arbitrage. '
            '(D) Re-run world_auction_scan.py (Heritage/Noble/Spink) for non-gold, non-bullion world coins. '
            'Next scheduled re-evaluation: when gold spot < $2,600/oz or after next monthly world auction scan.'
        ),
        'category':             'OBSERVATION',
        'evidence_status':      '市場状況記録（仕入れ停滞理由）',
        'marketing_status':     'OBSERVATION',
        'created_at':            now,
        'updated_at':            now,
    }

    result_c = c.table('ceo_review_log').insert(entry_c).execute()
    id_c = result_c.data[0]['id'] if result_c.data else 'unknown'
    print(f'Entry C inserted: id={id_c}')
    print('  Market status note: gold $3,100+/oz structural barrier documented')

    print()
    print('=== CHG-033 SUMMARY ===')
    print(f'Entry A (1883-CC Morgan MS65): id={id_a}')
    print(f'  buy_limit=¥{buy_a_r:,}=${round(buy_a_r/USD_JPY,1)} | eBay=$569.50 | gap=$9.50 over | profit=¥{profit_a_r:,}')
    print(f'Entry B (Sovereign MS63):      id={id_b}')
    print(f'  buy_limit=¥{buy_b:,}=${round(buy_b/USD_JPY,1)} | eBay=$1,400+ | gold $3,100+/oz barrier')
    print(f'Entry C (Market Status):       id={id_c}')
    print('  Structural barrier documented for CEO reference')
    print()
    print('3 OBSERVATION entries registered. Status: CHG-033 complete.')
    print('No CAP_BUY candidates available in current market (gold $3,100+/oz).')
    print('Re-scan recommended when gold < $2,600/oz or via world auction scan.')


if __name__ == '__main__':
    main()
