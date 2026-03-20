"""ヤフオク vs eBay クロスマーケット価格差分析

直近3か月を主軸に、eBayとヤフオクの価格差を分析し、
利益候補を抽出する。

使い方:
    python scripts/cross_market_analysis.py
    python scripts/cross_market_analysis.py --months 3   # 直近3か月のみ
    python scripts/cross_market_analysis.py --months 12  # 直近1年
"""

import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client

USD_JPY_RATE = 150.0
EBAY_FEE_RATE = 0.13   # eBay手数料13%
SHIPPING_JPY = 3000     # 国際送料概算


def fetch_all_records(source=None, months=None, exclude_noise=True, max_records=100000):
    """Supabaseから全件取得"""
    client = get_client()
    cols = "title,price_jpy,price,sold_date,source,country,year,grader,grade,series,tags"
    page_size = 1000
    all_records = []
    offset = 0

    cutoff = None
    if months:
        cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    while offset < max_records:
        q = client.table("market_transactions").select(cols)
        if source:
            q = q.eq("source", source)
        if exclude_noise:
            q = q.not_.contains("tags", '{"_noise:set"}')
            q = q.not_.contains("tags", '{"_noise:non_coin"}')
        if cutoff:
            q = q.gte("sold_date", cutoff)
        q = q.order("sold_date", desc=True).range(offset, offset + page_size - 1)
        batch = q.execute().data
        all_records.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return all_records


def safe_print(text):
    """cp932セーフな出力"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='replace').decode())


def normalize_title(title):
    """タイトルを正規化してマッチング用キーを生成"""
    t = title.upper()
    # 不要な修飾子を除去
    t = re.sub(r'\b(BEAUTIFUL|RARE|GORGEOUS|NICE|LOT|LOOK)\b', '', t)
    t = re.sub(r'[【】\[\]()（）「」]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def extract_coin_key(record, precision="standard"):
    """レコードからコイン同定キーを生成

    precision:
        "loose"    - grader + grade (最も粗い)
        "standard" - grader + grade + country + year
        "precise"  - grader + grade + country + year + series + denomination + material
    """
    grader = record.get("grader", "")
    grade = record.get("grade", "")
    country = record.get("country", "")
    year = record.get("year")
    series = record.get("series", "")
    denom = record.get("denomination", "")
    material = record.get("material", "")

    if not grader or not grade:
        return None

    if precision == "loose":
        return f"{grader}|{grade}"

    parts = [grader, grade]
    if country:
        parts.append(country)
    if year:
        parts.append(str(year))

    if precision == "precise":
        if series:
            parts.append(series)
        if denom:
            parts.append(denom)
        if material:
            parts.append(material)

    if len(parts) >= 3:
        return "|".join(parts)
    return None


def calc_stats(records):
    """統計値算出"""
    prices = [r.get("price_jpy", 0) for r in records if r.get("price_jpy")]
    if not prices:
        return {"count": 0, "avg": 0, "median": 0, "max": 0, "over_10man": 0, "over_30man": 0}
    prices.sort()
    return {
        "count": len(prices),
        "avg": int(sum(prices) / len(prices)),
        "median": prices[len(prices) // 2],
        "max": max(prices),
        "over_10man": sum(1 for p in prices if p >= 100000),
        "over_30man": sum(1 for p in prices if p >= 300000),
    }


def print_comparison_report(yahoo_recs, ebay_recs, period_label):
    """価格比較レポート出力"""
    y_stats = calc_stats(yahoo_recs)
    e_stats = calc_stats(ebay_recs)

    print(f"{'=' * 70}")
    print(f"  ヤフオク vs eBay 価格比較レポート ({period_label})")
    print(f"  基準日: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'=' * 70}")
    print()

    header = f"{'指標':<16} {'ヤフオク':>12} {'eBay(USD→JPY)':>14} {'差額':>10} {'差率':>8}"
    print(header)
    print("-" * 70)

    for label, y_val, e_val in [
        ("件数", y_stats["count"], e_stats["count"]),
        ("平均価格", y_stats["avg"], e_stats["avg"]),
        ("中央価格", y_stats["median"], e_stats["median"]),
        ("最高価格", y_stats["max"], e_stats["max"]),
        ("10万超件数", y_stats["over_10man"], e_stats["over_10man"]),
        ("30万超件数", y_stats["over_30man"], e_stats["over_30man"]),
    ]:
        diff = y_val - e_val
        pct = f"{diff / e_val * 100:+.1f}%" if e_val else "N/A"
        if label == "件数":
            print(f"  {label:<14} {y_val:>11,}件 {e_val:>13,}件 {diff:>+10,} {pct:>8}")
        else:
            print(f"  {label:<14} {y_val:>10,}円 {e_val:>12,}円 {diff:>+9,}円 {pct:>8}")
    print()


def _remove_outliers(prices, factor=2.5):
    """IQR法で外れ値を除去"""
    if len(prices) < 4:
        return prices
    s = sorted(prices)
    q1 = s[len(s) // 4]
    q3 = s[3 * len(s) // 4]
    iqr = q3 - q1
    lo = q1 - factor * iqr
    hi = q3 + factor * iqr
    return [p for p in prices if lo <= p <= hi]


def _extract_coin_type(title):
    """タイトルからコイン品種名キーワードを抽出（Eagle, Panda, Sovereign等）

    複数マッチした場合は全て結合して返す（区別精度向上）
    """
    t = title.upper()
    type_keywords = [
        # 英語名
        ("EAGLE", "Eagle"), ("PANDA", "Panda"), ("SOVEREIGN", "Sovereign"),
        ("BRITANNIA", "Britannia"), ("MORGAN", "Morgan"), ("LIBERTY", "Liberty"),
        ("KRUGERRAND", "Krugerrand"), ("MAPLE", "Maple"), ("KOALA", "Koala"),
        ("KOOKABURRA", "Kookaburra"), ("KANGAROO", "Kangaroo"),
        ("UNA", "Una"), ("NAPOLEON", "Napoleon"), ("LUNAR", "Lunar"),
        ("PHILHARMONIC", "Philharmonic"), ("GOLD BUFFALO", "Buffalo"),
        ("QUEEN'S BEAST", "QueensBeast"), ("PEARL HARBOR", "PearlHarbor"),
        ("JAMES BOND", "Bond"), ("THREE GRACES", "ThreeGraces"),
        ("WEDGE", "WedgeTail"),
        # 日本語名
        ("イーグル", "Eagle"), ("パンダ", "Panda"), ("ソブリン", "Sovereign"),
        ("ブリタニア", "Britannia"), ("モルガン", "Morgan"),
        ("クルーガーランド", "Krugerrand"), ("メイプル", "Maple"),
        ("ウナ", "Una"), ("ナポレオン", "Napoleon"),
        ("クッカバラ", "Kookaburra"), ("ワライカワセミ", "Kookaburra"),
        ("カンガルー", "Kangaroo"), ("コアラ", "Koala"),
        ("ルナ", "Lunar"), ("真珠湾", "PearlHarbor"),
        ("ジェームズボンド", "Bond"), ("スリーグレーセス", "ThreeGraces"),
        # 額面（重要: 10銭と1圓の混同防止）
        ("一圓", "1Yen"), ("壱圓", "1Yen"), ("1 YEN", "1Yen"),
        ("10 SEN", "10Sen"), ("10銭", "10Sen"), ("十銭", "10Sen"), ("10S ", "10Sen"),
        ("20 SEN", "20Sen"), ("20銭", "20Sen"), ("20S ", "20Sen"),
        ("50 SEN", "50Sen"), ("50銭", "50Sen"),
        ("CROWN", "Crown"), ("FRANC", "Franc"), ("MARK", "Mark"),
        # 金属（金貨 vs 銀貨の区別）
        ("GOLD", "Gold"), ("金貨", "Gold"),
        ("SILVER", "Silver"), ("銀貨", "Silver"),
        ("PLATINUM", "Platinum"), ("プラチナ", "Platinum"),
        # 国固有
        ("NANCY REAGAN", "NancyReagan"), ("ナンシー", "NancyReagan"),
        ("レーガン", "NancyReagan"),
        ("BASKETBALL", "Basketball"), ("GETTYSBURG", "Gettysburg"),
    ]
    found = set()
    for keyword, label in type_keywords:
        if keyword in t:
            found.add(label)
    if found:
        return "+".join(sorted(found))
    return ""


def find_profit_opportunities(yahoo_recs, ebay_recs, top_n=20, precision="standard"):
    """利益候補抽出: eBayで安く買ってヤフオクで高く売れるパターンを探す

    precision: "loose" / "standard" / "precise"
    改善点:
    - 外れ値除去（IQR法）
    - コイン品種名でサブグルーピング（Eagleとパンダの混同防止）
    """
    yahoo_by_key = defaultdict(list)
    ebay_by_key = defaultdict(list)

    for r in yahoo_recs:
        key = extract_coin_key(r, precision=precision)
        if key:
            # 品種名をキーに追加
            coin_type = _extract_coin_type(r.get("title", ""))
            full_key = f"{key}|{coin_type}" if coin_type else key
            yahoo_by_key[full_key].append(r)

    for r in ebay_recs:
        key = extract_coin_key(r, precision=precision)
        if key:
            coin_type = _extract_coin_type(r.get("title", ""))
            full_key = f"{key}|{coin_type}" if coin_type else key
            ebay_by_key[full_key].append(r)

    # 共通キーで比較
    opportunities = []
    common_keys = set(yahoo_by_key.keys()) & set(ebay_by_key.keys())

    for key in common_keys:
        y_prices = [r["price_jpy"] for r in yahoo_by_key[key] if r.get("price_jpy")]
        e_prices = [r["price_jpy"] for r in ebay_by_key[key] if r.get("price_jpy")]

        if not y_prices or not e_prices:
            continue

        # 外れ値除去
        y_clean = _remove_outliers(y_prices)
        e_clean = _remove_outliers(e_prices)
        if not y_clean or not e_clean:
            continue

        y_median = sorted(y_clean)[len(y_clean) // 2]
        e_median = sorted(e_clean)[len(e_clean) // 2]

        # eBay→ヤフオク転売の利益計算
        ebay_cost = e_median + SHIPPING_JPY
        yahoo_sell = y_median
        yahoo_fee = int(yahoo_sell * 0.088)
        profit = yahoo_sell - yahoo_fee - ebay_cost
        profit_pct = profit / ebay_cost * 100 if ebay_cost > 0 else 0

        if profit > 5000 and len(y_clean) >= 2 and len(e_clean) >= 2:
            sample_y = yahoo_by_key[key][0]
            sample_e = ebay_by_key[key][0]
            opportunities.append({
                "key": key,
                "yahoo_median": y_median,
                "ebay_median": e_median,
                "ebay_cost": ebay_cost,
                "yahoo_sell_after_fee": yahoo_sell - yahoo_fee,
                "profit": profit,
                "profit_pct": profit_pct,
                "yahoo_count": len(y_clean),
                "ebay_count": len(e_clean),
                "yahoo_title": sample_y.get("title", "")[:60],
                "ebay_title": sample_e.get("title", "")[:60],
            })

    # 利益率でソート
    opportunities.sort(key=lambda x: -x["profit_pct"])
    return opportunities[:top_n]


def print_profit_candidates(opportunities):
    """利益候補レポート出力"""
    print(f"{'=' * 70}")
    print(f"  利益候補ランキング（eBay仕入れ → ヤフオク販売）")
    print(f"  送料: {SHIPPING_JPY:,}円 / ヤフオク手数料: 8.8%")
    print(f"{'=' * 70}")
    print()

    if not opportunities:
        print("  利益候補が見つかりませんでした。")
        print("  データの粒度を上げるか、期間を拡張してください。")
        return

    for i, opp in enumerate(opportunities, 1):
        print(f"  --- #{i} ---")
        print(f"  条件: {opp['key']}")
        print(f"  eBay中央値: {opp['ebay_median']:>10,}円 (仕入+送料: {opp['ebay_cost']:>10,}円)")
        print(f"  ヤフオク中央値: {opp['yahoo_median']:>7,}円 (手数料後: {opp['yahoo_sell_after_fee']:>10,}円)")
        print(f"  推定利益: {opp['profit']:>+10,}円 ({opp['profit_pct']:>+.1f}%)")
        print(f"  実績件数: eBay {opp['ebay_count']}件 / ヤフオク {opp['yahoo_count']}件")
        safe_print(f"  eBay例: {opp['ebay_title']}")
        safe_print(f"  ヤフオク例: {opp['yahoo_title']}")
        print()

    print("=" * 70)


def grader_country_comparison(yahoo_recs, ebay_recs):
    """鑑定会社×国別の価格差比較"""
    print(f"{'=' * 70}")
    print(f"  鑑定会社×国別 日米価格差")
    print(f"{'=' * 70}")
    print()

    for grader in ["NGC", "PCGS"]:
        y_g = [r for r in yahoo_recs if r.get("grader") == grader]
        e_g = [r for r in ebay_recs if r.get("grader") == grader]

        if not y_g or not e_g:
            continue

        print(f"  [{grader}]")
        y_countries = defaultdict(list)
        e_countries = defaultdict(list)

        # eBay countryはコインの国、not sellerの国
        for r in y_g:
            c = r.get("country") or "(other)"
            y_countries[c].append(r.get("price_jpy", 0))
        for r in e_g:
            c = r.get("country") or "(other)"
            e_countries[c].append(r.get("price_jpy", 0))

        common = set(y_countries.keys()) & set(e_countries.keys())
        rows = []
        for country in sorted(common):
            yp = y_countries[country]
            ep = e_countries[country]
            if len(yp) < 5 or len(ep) < 5:
                continue
            y_avg = int(sum(yp) / len(yp))
            e_avg = int(sum(ep) / len(ep))
            diff = y_avg - e_avg
            pct = diff / e_avg * 100 if e_avg else 0
            rows.append((country, len(yp), y_avg, len(ep), e_avg, diff, pct))

        rows.sort(key=lambda x: -x[5])

        header = f"  {'国':<12} {'ヤフオク件数':>8} {'ヤフオク平均':>10} {'eBay件数':>8} {'eBay平均':>10} {'差額':>10} {'差率':>7}"
        print(header)
        print("  " + "-" * 68)
        for country, yc, ya, ec, ea, diff, pct in rows[:10]:
            print(f"  {country:<12} {yc:>7,}件 {ya:>9,}円 {ec:>7,}件 {ea:>9,}円 {diff:>+9,}円 {pct:>+6.1f}%")
        print()


def main():
    args = sys.argv[1:]
    months = 3  # デフォルト3か月

    i = 0
    while i < len(args):
        if args[i] == "--months" and i + 1 < len(args):
            months = int(args[i + 1])
            i += 2
        else:
            i += 1

    period_label = f"直近{months}か月"

    print(f"データ取得中 ({period_label})...")
    yahoo_recs = fetch_all_records(source="yahoo", months=months)
    ebay_recs = fetch_all_records(source="ebay", months=months)
    print(f"  ヤフオク: {len(yahoo_recs):,}件")
    print(f"  eBay:     {len(ebay_recs):,}件")
    print()

    # [1] 全体比較
    print_comparison_report(yahoo_recs, ebay_recs, period_label)

    # [2] 鑑定会社×国別比較
    grader_country_comparison(yahoo_recs, ebay_recs)

    # [3] 利益候補（精密マッチ → 標準マッチ）
    print()
    print(f"{'=' * 70}")
    print(f"  利益候補抽出（精密マッチ: grader+grade+country+year+series+denom+material）")
    print(f"{'=' * 70}")
    precise_opps = find_profit_opportunities(yahoo_recs, ebay_recs, top_n=15, precision="precise")
    if precise_opps:
        print_profit_candidates(precise_opps)
    else:
        print("  精密マッチでは候補なし。標準マッチに拡張します。")
        print()

    print()
    print(f"{'=' * 70}")
    print(f"  利益候補抽出（標準マッチ: grader+grade+country+year）")
    print(f"{'=' * 70}")
    standard_opps = find_profit_opportunities(yahoo_recs, ebay_recs, top_n=20, precision="standard")
    print_profit_candidates(standard_opps)

    # [4] 利益パターンのルール化
    print()
    print(f"{'=' * 70}")
    print(f"  利益パターン型整理")
    print(f"{'=' * 70}")
    print()
    all_opps = precise_opps + standard_opps
    # パターン分類
    patterns = defaultdict(list)
    for opp in all_opps:
        key = opp["key"]
        parts = key.split("|")
        grader = parts[0] if len(parts) > 0 else ""
        grade = parts[1] if len(parts) > 1 else ""
        # グレード系統分類
        if "70" in grade:
            cat = f"{grader} 最高鑑定 ({grade})"
        elif "69" in grade:
            cat = f"{grader} 準最高鑑定 ({grade})"
        elif "MS6" in grade:
            cat = f"{grader} 高グレードMS"
        else:
            cat = f"{grader} その他 ({grade})"
        patterns[cat].append(opp)

    for cat, opps in sorted(patterns.items(), key=lambda x: -len(x[1])):
        avg_profit = int(sum(o["profit"] for o in opps) / len(opps))
        avg_pct = sum(o["profit_pct"] for o in opps) / len(opps)
        print(f"  [{cat}] {len(opps)}件  平均利益{avg_profit:>+,}円 ({avg_pct:>+.0f}%)")
    print()

    # [5] 実験仕入れ候補3件選定
    print()
    print(f"{'=' * 70}")
    print(f"  実験仕入れ候補 TOP3（再現性・価格帯・リスクで選定）")
    print(f"{'=' * 70}")
    print()

    # 選定基準: eBay実績5件以上 & ヤフオク実績2件以上 & 仕入れ3万円以下 & 利益率100%以上
    candidates = [o for o in all_opps
                  if o["ebay_count"] >= 5
                  and o["yahoo_count"] >= 2
                  and o["ebay_cost"] <= 30000
                  and o["profit_pct"] >= 100]
    # 重複除去（keyベース）
    seen_keys = set()
    unique_candidates = []
    for c in candidates:
        if c["key"] not in seen_keys:
            seen_keys.add(c["key"])
            unique_candidates.append(c)
    # 利益額でソート
    unique_candidates.sort(key=lambda x: -x["profit"])

    for i, cand in enumerate(unique_candidates[:3], 1):
        print(f"  ===== 実験仕入れ #{i} =====")
        print(f"  条件: {cand['key']}")
        print(f"  eBay仕入れ目安: {cand['ebay_cost']:>10,}円（本体{cand['ebay_median']:,}円 + 送料3,000円）")
        print(f"  ヤフオク販売目安: {cand['yahoo_median']:>8,}円（手数料後{cand['yahoo_sell_after_fee']:,}円）")
        print(f"  推定利益: {cand['profit']:>+10,}円（{cand['profit_pct']:>+.0f}%）")
        print(f"  再現性: eBay {cand['ebay_count']}件 / ヤフオク {cand['yahoo_count']}件")
        safe_print(f"  eBay例: {cand['ebay_title']}")
        safe_print(f"  ヤフオク例: {cand['yahoo_title']}")
        risk = "低" if cand["ebay_count"] >= 10 and cand["yahoo_count"] >= 3 else "中"
        print(f"  リスク判定: {risk}")
        print()

    if not unique_candidates:
        print("  選定基準（eBay5件以上・ヤフ2件以上・仕入3万以下・利益率100%以上）で")
        print("  候補が不足。基準を緩和して再探索します。")
        relaxed = [o for o in all_opps
                   if o["ebay_count"] >= 3
                   and o["yahoo_count"] >= 2
                   and o["profit_pct"] >= 80]
        seen2 = set()
        for c in sorted(relaxed, key=lambda x: -x["profit"])[:3]:
            if c["key"] not in seen2:
                seen2.add(c["key"])
                print(f"  [{c['key']}] 利益{c['profit']:>+,}円 ({c['profit_pct']:>+.0f}%) eBay{c['ebay_count']}件/ヤフ{c['yahoo_count']}件")

    # [6] 1年分比較（補助参考）
    if months != 3:
        print()
        print("=" * 70)
        print("  参考: 直近3か月のサブセット")
        print("=" * 70)
        cutoff_3m = (datetime.now() - timedelta(days=91)).strftime("%Y-%m-%d")
        y_3m = [r for r in yahoo_recs if (r.get("sold_date") or "") >= cutoff_3m]
        e_3m = [r for r in ebay_recs if (r.get("sold_date") or "") >= cutoff_3m]
        print_comparison_report(y_3m, e_3m, "直近3か月 サブセット")


if __name__ == "__main__":
    main()
