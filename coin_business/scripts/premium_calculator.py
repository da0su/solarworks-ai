"""Phase 2: プレミアム販売標準価格 自動算定ロジック

CEO指示書（最新版）に基づくパターン判定。
常に最新履歴で再算定。過去の確定値に依存しない。
"""
import sys
import json
import statistics
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.supabase_client import get_client


def get_metal_price(db, sold_date, material):
    """落札日の地金価格を取得（JPY/g）"""
    resp = (db.table('daily_rates')
        .select('gold_jpy_per_g, silver_jpy_per_g, platinum_jpy_per_g')
        .eq('rate_date', sold_date)
        .limit(1)
        .execute())

    if not resp.data:
        resp = (db.table('daily_rates')
            .select('gold_jpy_per_g, silver_jpy_per_g, platinum_jpy_per_g')
            .lte('rate_date', sold_date)
            .order('rate_date', desc=True)
            .limit(1)
            .execute())

    if not resp.data:
        return None

    r = resp.data[0]
    if material in ['G', 'Gold', 'gold']:
        return r.get('gold_jpy_per_g')
    elif material in ['S', 'Silver', 'silver']:
        return r.get('silver_jpy_per_g')
    elif material in ['P', 'Pt', 'Platinum', 'platinum']:
        return r.get('platinum_jpy_per_g')
    return None


def calc_melt_value(metal_price_per_g, weight_oz, purity):
    """地金価値を算出"""
    if not metal_price_per_g or not weight_oz or not purity:
        return None
    weight_g = weight_oz * 31.1035
    return int(metal_price_per_g * weight_g * purity)


def calc_premium(price_jpy, melt_value):
    """プレミアム価格 = 落札価格 − 地金価値"""
    if melt_value is None:
        return None
    return price_jpy - melt_value


def calc_median(values):
    """中央値を算出（偶数件は中央2値の平均）"""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    else:
        mid = n // 2
        return int((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)


def determine_pattern(records):
    """落札履歴からパターンを判定し、プレミアム販売標準価格を算出

    records: list of dict with keys: price, bids, init_price, date, premium

    Returns:
        (pattern_name, premium_price, status)
        premium_price: プレミアム販売標準価格（int or None）
        status: 'auto_confirmed' or 'needs_review'
    """
    if not records:
        return 'no_data', None, 'needs_review'

    # premiumがNoneのレコードを除外
    valid = [r for r in records if r.get('premium') is not None]
    if not valid:
        return 'no_data', None, 'needs_review'

    # --- 1円スタート判定 ---
    # 1円スタート（init_price が 0 or 1）のレコード
    auction_start = [r for r in valid if r.get('init_price', 0) in [0, 1]]

    # 1円スタート + 入札10件以上
    auction_10plus = [r for r in auction_start if r.get('bids', 0) >= 10]

    # パターン1: 1円出品 + 入札10件以上 × 1件
    if len(auction_10plus) == 1:
        premium = auction_10plus[0]['premium']
        return 'pattern_1', premium, 'auto_confirmed'

    # パターン1複数: 1円出品 + 入札10件以上 × 2件以上
    if len(auction_10plus) >= 2:
        return 'pattern_1_multi', None, 'needs_review'

    # パターン2: 1円出品 + 入札10件未満（複数履歴なし）
    # 1円スタートは市場評価が出ていると判断 → 自動確定
    auction_under10 = [r for r in auction_start if r.get('bids', 0) < 10]
    if len(auction_under10) >= 1 and len(valid) == len(auction_under10):
        # 全て1円スタート+10件未満の場合、最新の1件で確定
        latest = sorted(auction_under10, key=lambda x: x.get('date', ''), reverse=True)[0]
        return 'pattern_2', latest['premium'], 'auto_confirmed'

    # --- 非1円スタートの履歴（入札10件未満） ---
    non_auction = [r for r in valid if r.get('init_price', 0) not in [0, 1]]
    # 1円スタート+10件未満も含める
    under_10 = non_auction + auction_under10
    premiums = [r['premium'] for r in under_10]

    n = len(premiums)

    # パターン3: 5件以上 → 上下1件除外 → 残りの中央値
    if n >= 5:
        sorted_p = sorted(premiums)
        trimmed = sorted_p[1:-1]  # 上限1件・下限1件除外
        median = calc_median(trimmed)
        return 'pattern_3', median, 'auto_confirmed'

    # パターン4: 4件 → 上下1件除外 → 残り2件の中央値（平均）
    if n == 4:
        sorted_p = sorted(premiums)
        trimmed = sorted_p[1:-1]  # 2件残る
        median = calc_median(trimmed)
        return 'pattern_4', median, 'auto_confirmed'

    # パターン5-1: 3件 → 中央値
    if n == 3:
        median = calc_median(premiums)
        return 'pattern_5_1', median, 'auto_confirmed'

    # パターン5-2: 2件 → 安い方
    if n == 2:
        cheaper = min(premiums)
        return 'pattern_5_2', cheaper, 'auto_confirmed'

    # パターン5-3: 1件 → CEO判断
    if n == 1:
        return 'pattern_5_3', None, 'needs_review'

    return 'no_data', None, 'needs_review'


def calc_purchase_price(premium_standard, melt_value_today):
    """仕入相場価格の算出

    仕入相場価格 = プレミアム仕入相場価格 + 当日地金価値

    販売手取り = (プレミアム販売標準価格 + 地金価値) × 0.9
    原価上限 = min(手取り − 20,000, 手取り × 0.85)
    仕入相場価格上限 = (原価上限 − 送料JPY − 2,750) / 1.1
    プレミアム仕入相場価格 = 仕入相場価格上限 − 地金価値
    """
    if premium_standard is None or melt_value_today is None:
        return None, None

    sales_price = premium_standard + melt_value_today
    net = int(sales_price * 0.9)  # ヤフオク手数料10%

    # 両条件を満たす原価上限
    max_cost_profit = net - 20000  # 最低利益¥20,000
    max_cost_rate = int(net * 0.85)  # 粗利率15%（手取りの85%が原価上限）
    cost_limit = min(max_cost_profit, max_cost_rate)

    if cost_limit <= 0:
        return 0, melt_value_today

    # 送料を考慮した仕入相場価格上限
    # 原価 = (仕入相場価格 × 1.1) + 送料JPY + 2,750
    # 仕入相場価格 = (原価上限 - 2,750) / 1.1  ※送料は個別で変動するため除外
    purchase_limit = int((cost_limit - 2750) / 1.1)
    premium_purchase = purchase_limit - melt_value_today

    if premium_purchase <= 0:
        return 0, melt_value_today

    purchase_price = premium_purchase + melt_value_today
    return premium_purchase, purchase_price


if __name__ == '__main__':
    print('=== プレミアム価格自動算定テスト ===\n')

    # パターン1: 1円出品 + 入札10件以上 × 1件
    test1 = [{'price': 50000, 'bids': 15, 'init_price': 1, 'premium': 45000, 'date': '2025-03-01'}]
    p, v, s = determine_pattern(test1)
    print(f'パターン1: {p} | premium=¥{v:,} | {s}')

    # パターン1複数
    test1m = [
        {'price': 50000, 'bids': 15, 'init_price': 1, 'premium': 45000, 'date': '2025-03-01'},
        {'price': 48000, 'bids': 12, 'init_price': 1, 'premium': 43000, 'date': '2025-02-01'},
    ]
    p, v, s = determine_pattern(test1m)
    print(f'パターン1複数: {p} | premium={v} | {s}')

    # パターン2: 1円出品 + 入札10件未満
    test2 = [{'price': 30000, 'bids': 5, 'init_price': 1, 'premium': 25000, 'date': '2025-03-01'}]
    p, v, s = determine_pattern(test2)
    print(f'パターン2: {p} | premium=¥{v:,} | {s}')

    # パターン3: 5件
    test3 = [
        {'price': 50000, 'bids': 5, 'init_price': 5000, 'premium': 45000},
        {'price': 48000, 'bids': 3, 'init_price': 5000, 'premium': 43000},
        {'price': 52000, 'bids': 7, 'init_price': 5000, 'premium': 47000},
        {'price': 35000, 'bids': 2, 'init_price': 5000, 'premium': 30000},
        {'price': 55000, 'bids': 8, 'init_price': 5000, 'premium': 50000},
    ]
    p, v, s = determine_pattern(test3)
    print(f'パターン3(5件): {p} | premium=¥{v:,} | {s}')
    # 期待: 30000,43000,45000,47000,50000 → 上下除外 → 43000,45000,47000 → 中央値45000

    # パターン3: 6件
    test3b = test3 + [{'price': 46000, 'bids': 4, 'init_price': 5000, 'premium': 41000}]
    p, v, s = determine_pattern(test3b)
    print(f'パターン3(6件): {p} | premium=¥{v:,} | {s}')
    # 期待: 30000,41000,43000,45000,47000,50000 → 上下除外 → 41000,43000,45000,47000 → 中央値44000

    # パターン4: 4件
    test4 = [
        {'price': 50000, 'bids': 5, 'init_price': 5000, 'premium': 45000},
        {'price': 48000, 'bids': 3, 'init_price': 5000, 'premium': 43000},
        {'price': 35000, 'bids': 2, 'init_price': 5000, 'premium': 30000},
        {'price': 55000, 'bids': 8, 'init_price': 5000, 'premium': 50000},
    ]
    p, v, s = determine_pattern(test4)
    print(f'パターン4: {p} | premium=¥{v:,} | {s}')
    # 期待: 30000,43000,45000,50000 → 上下除外 → 43000,45000 → 中央値44000

    # パターン5-1: 3件
    test51 = [
        {'price': 50000, 'bids': 5, 'init_price': 5000, 'premium': 45000},
        {'price': 48000, 'bids': 3, 'init_price': 5000, 'premium': 43000},
        {'price': 52000, 'bids': 7, 'init_price': 5000, 'premium': 47000},
    ]
    p, v, s = determine_pattern(test51)
    print(f'パターン5-1: {p} | premium=¥{v:,} | {s}')
    # 期待: 43000,45000,47000 → 中央値45000

    # パターン5-2: 2件
    test52 = [
        {'price': 50000, 'bids': 5, 'init_price': 5000, 'premium': 45000},
        {'price': 48000, 'bids': 3, 'init_price': 5000, 'premium': 43000},
    ]
    p, v, s = determine_pattern(test52)
    print(f'パターン5-2: {p} | premium=¥{v:,} | {s}')
    # 期待: 安い方 → 43000

    # パターン5-3: 1件
    test53 = [{'price': 50000, 'bids': 5, 'init_price': 5000, 'premium': 45000}]
    p, v, s = determine_pattern(test53)
    print(f'パターン5-3: {p} | premium={v} | {s}')
    # 期待: CEO判断

    # 仕入相場価格テスト
    print(f'\n=== 仕入相場価格テスト ===')
    prem_purchase, purchase = calc_purchase_price(200000, 50000)
    print(f'プレミアム¥200,000 + 地金¥50,000')
    print(f'  プレミアム仕入相場: ¥{prem_purchase:,}')
    print(f'  仕入相場価格: ¥{purchase:,}')
