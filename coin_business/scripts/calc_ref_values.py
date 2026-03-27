"""基準1/基準2 一括計算 & DB更新スクリプト

対象: coin_slab_data (status=completed_hit, purity NOT NULL)

基準1: プレミアム+地金連動方式
基準2: 直近ヤフオク落札価格ベース
"""

import json
import sys
import statistics
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client


def get_latest_rates(db):
    """最新の金属レートを取得"""
    resp = (db.table('daily_rates')
            .select('rate_date, gold_jpy_per_g, silver_jpy_per_g, platinum_jpy_per_g')
            .order('rate_date', desc=True)
            .limit(1)
            .execute())
    if not resp.data:
        raise RuntimeError("daily_rates にデータがありません")
    return resp.data[0]


def get_rate_for_date(db, date_str, rates_cache):
    """指定日の金属レートを取得（キャッシュ付き）"""
    if date_str in rates_cache:
        return rates_cache[date_str]

    # 完全一致
    resp = (db.table('daily_rates')
            .select('gold_jpy_per_g, silver_jpy_per_g, platinum_jpy_per_g')
            .eq('rate_date', date_str)
            .limit(1)
            .execute())
    if resp.data:
        rates_cache[date_str] = resp.data[0]
        return resp.data[0]

    # 直前のレートにフォールバック
    resp = (db.table('daily_rates')
            .select('gold_jpy_per_g, silver_jpy_per_g, platinum_jpy_per_g')
            .lte('rate_date', date_str)
            .order('rate_date', desc=True)
            .limit(1)
            .execute())
    if resp.data:
        rates_cache[date_str] = resp.data[0]
        return resp.data[0]

    return None


def metal_rate_per_g(rate_row, material):
    """素材に応じた1gあたり単価を返す"""
    if not rate_row:
        return None
    mat = (material or '').lower()
    if mat in ('gold', 'g', 'electrum'):
        return float(rate_row.get('gold_jpy_per_g') or 0)
    elif mat in ('silver', 's'):
        return float(rate_row.get('silver_jpy_per_g') or 0)
    elif mat in ('platinum', 'pt', 'p'):
        return float(rate_row.get('platinum_jpy_per_g') or 0)
    return None


def calc_median_5pattern(prices):
    """5パターン中央値計算

    5件以上: 上下1件カット→中央値
    4件: 上下1件カット→平均
    3件: 中央値
    2件: 安い方
    1件: そのまま
    """
    if not prices:
        return None
    n = len(prices)
    s = sorted(prices)

    if n >= 5:
        trimmed = s[1:-1]
        return int(statistics.median(trimmed))
    elif n == 4:
        trimmed = s[1:-1]  # 2件残る
        return int(sum(trimmed) / 2)
    elif n == 3:
        return int(statistics.median(s))
    elif n == 2:
        return int(min(s))
    else:
        return int(s[0])


def process_row(row, latest_rates, db, rates_cache):
    """1行を計算して更新用dictを返す"""
    material = row.get('material')
    is_ancient = row.get('is_ancient', False)
    purity = float(row.get('purity', 0))
    weight_g = float(row.get('weight_g', 0))

    # price_historyをパース
    ph_raw = row.get('price_history')
    if isinstance(ph_raw, str):
        try:
            price_history = json.loads(ph_raw)
        except (json.JSONDecodeError, TypeError):
            price_history = []
    elif isinstance(ph_raw, list):
        price_history = ph_raw
    else:
        price_history = []

    if not price_history:
        return None

    # 直近の落札 (date降順で最初)
    sorted_ph = sorted(price_history, key=lambda x: x.get('date', ''), reverse=True)
    latest_sale = sorted_ph[0]
    sold_date = latest_sale.get('date', '')
    yahoo_price = int(latest_sale.get('price', 0))

    # --- 現在の地金価値 ---
    current_rate = metal_rate_per_g(latest_rates, material)
    if is_ancient or current_rate is None:
        current_melt = 0
    else:
        current_melt = int(current_rate * weight_g * purity)

    # --- 落札日の金属レート ---
    sold_rate_row = get_rate_for_date(db, sold_date, rates_cache) if sold_date else None
    sold_rate = metal_rate_per_g(sold_rate_row, material)

    if is_ancient:
        sold_melt = 0
    elif sold_rate is not None:
        sold_melt = int(sold_rate * weight_g * purity)
    else:
        sold_melt = 0

    # --- 基準1: プレミアム+地金連動方式 ---
    prices = [int(p.get('price', 0)) for p in price_history if p.get('price')]
    median_price = calc_median_5pattern(prices)

    ref1_buy_limit = None
    premium = None

    if median_price is not None:
        # プレミアム = 中央値(5パターン) - 落札日の地金価値
        premium = median_price - sold_melt
        # 販売標準価格 = プレミアム + 現在の地金価値
        sales_standard = premium + current_melt
        # 販売手取り = 販売標準価格 × 0.9
        net_sales = int(sales_standard * 0.9)
        # 原価上限 = 販売手取り × 0.85
        cost_limit = int(net_sales * 0.85)
        # eBay仕入れ上限(JPY送料込み) = (原価上限 - 2000 - 750) / 1.1
        if cost_limit > 2750:
            ref1_buy_limit = int((cost_limit - 2000 - 750) / 1.1)
        else:
            ref1_buy_limit = 0

    # --- 基準2: 直近ヤフオク落札価格ベース ---
    ref2_net = int(yahoo_price * 0.9)
    ref2_cost_limit = int(ref2_net * 0.85)
    if ref2_cost_limit > 2750:
        ref2_buy_limit = int((ref2_cost_limit - 2000 - 750) / 1.1)
    else:
        ref2_buy_limit = 0

    update = {
        'metal_value_jpy': current_melt,
        'premium_value_jpy': premium,
        'ref1_buy_limit_jpy': ref1_buy_limit,
        'ref2_yahoo_price_jpy': yahoo_price,
        'ref2_metal_rate_per_g': round(sold_rate, 4) if sold_rate is not None else None,
        'ref2_sold_date': sold_date if sold_date else None,
    }

    return update


def main():
    db = get_client()

    # 最新レート取得
    latest_rates = get_latest_rates(db)
    print(f"最新レート日: {latest_rates['rate_date']}")
    print(f"  Gold:     {latest_rates['gold_jpy_per_g']} JPY/g")
    print(f"  Silver:   {latest_rates['silver_jpy_per_g']} JPY/g")
    print(f"  Platinum: {latest_rates['platinum_jpy_per_g']} JPY/g")

    # 対象レコード全件取得
    all_data = []
    last_id = '00000000-0000-0000-0000-000000000000'
    while True:
        resp = (db.table('coin_slab_data')
                .select('id, material, is_ancient, purity, weight_g, price_history')
                .eq('status', 'completed_hit')
                .not_.is_('purity', 'null')
                .gt('id', last_id)
                .order('id')
                .limit(1000)
                .execute())
        if not resp.data:
            break
        all_data.extend(resp.data)
        last_id = resp.data[-1]['id']

    print(f"\n対象件数: {len(all_data)}")

    rates_cache = {}
    updated = 0
    skipped = 0
    errors = 0

    for i, row in enumerate(all_data):
        try:
            update = process_row(row, latest_rates, db, rates_cache)
            if update is None:
                skipped += 1
                continue

            db.table('coin_slab_data').update(update).eq('id', row['id']).execute()
            updated += 1

            if (i + 1) % 100 == 0:
                print(f"  進捗: {i+1}/{len(all_data)} (更新={updated}, スキップ={skipped})")

        except Exception as e:
            errors += 1
            print(f"  エラー [{row['id']}]: {e}")

    print(f"\n完了!")
    print(f"  更新: {updated}")
    print(f"  スキップ: {skipped}")
    print(f"  エラー: {errors}")

    # サンプル表示
    print(f"\n=== 更新結果サンプル ===")
    resp = (db.table('coin_slab_data')
            .select('id, material, is_ancient, weight_g, purity, metal_value_jpy, premium_value_jpy, ref1_buy_limit_jpy, ref2_yahoo_price_jpy, ref2_sold_date')
            .eq('status', 'completed_hit')
            .not_.is_('purity', 'null')
            .not_.is_('ref1_buy_limit_jpy', 'null')
            .order('ref1_buy_limit_jpy', desc=True)
            .limit(10)
            .execute())
    for r in resp.data:
        print(f"  {r['material']:8s} w={r['weight_g']}g p={r['purity']} "
              f"melt=¥{r['metal_value_jpy']:>8,} prem=¥{r['premium_value_jpy']:>8,} "
              f"ref1=¥{r['ref1_buy_limit_jpy']:>8,} ref2_yahoo=¥{r['ref2_yahoo_price_jpy']:>8,}")


if __name__ == '__main__':
    main()
