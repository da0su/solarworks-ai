"""market_transactions 簡易集計レポート

使い方:
    python run.py stats                # 全体集計
    python run.py stats --clean        # ノイズ除外して集計
    python run.py stats --country XX   # 国指定
    python run.py stats --grader NGC   # 鑑定会社指定
    python run.py stats --time         # 時間軸4区分レポート（CEO判断用）
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client


def fetch_all(filters: dict | None = None, exclude_noise: bool = False,
              max_records: int = 100000) -> list[dict]:
    """market_transactionsから全件取得（ページネーション対応）"""
    client = get_client()
    cols = "title,price_jpy,sold_date,source,country,year,grader,grade,denomination,material,series,tags,seller_name"
    page_size = 1000  # Supabase API上限
    all_records = []
    offset = 0

    while offset < max_records:
        q = client.table("market_transactions").select(cols)
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        if exclude_noise:
            q = q.not_.contains("tags", '{"_noise:set"}')
            q = q.not_.contains("tags", '{"_noise:non_coin"}')
        q = q.order("sold_date", desc=True).range(offset, offset + page_size - 1)
        batch = q.execute().data
        all_records.extend(batch)
        if len(batch) < page_size:
            break  # 最終ページ
        offset += page_size
        print(f"  {len(all_records):,}件取得済み...")

    return all_records


# ============================================================
# 時間軸4区分レポート（CEO判断ルール準拠）
# ============================================================

def _calc_stats_block(records: list[dict]) -> dict:
    """レコード群から必須項目を算出"""
    prices = [r.get("price_jpy", 0) for r in records if r.get("price_jpy")]
    if not prices:
        return {"count": 0, "avg": 0, "median": 0, "max_price": 0,
                "over_10man": 0, "over_30man": 0}
    prices.sort()
    return {
        "count": len(prices),
        "avg": int(sum(prices) / len(prices)),
        "median": prices[len(prices) // 2],
        "max_price": max(prices),
        "over_10man": sum(1 for p in prices if p >= 100000),
        "over_30man": sum(1 for p in prices if p >= 300000),
    }


def _segment_records(records: list[dict], today: datetime = None) -> dict:
    """レコードを時間軸4区分に振り分け"""
    if today is None:
        today = datetime.now()

    cutoff_3m = (today - timedelta(days=91)).strftime("%Y-%m-%d")   # 約3か月
    cutoff_6m = (today - timedelta(days=182)).strftime("%Y-%m-%d")  # 約6か月
    cutoff_12m = (today - timedelta(days=365)).strftime("%Y-%m-%d") # 約12か月

    segments = {
        "直近3か月": [],
        "3~6か月": [],
        "6~12か月": [],
        "12か月超": [],
    }

    for r in records:
        d = r.get("sold_date", "")
        if not d:
            segments["12か月超"].append(r)
            continue
        if d >= cutoff_3m:
            segments["直近3か月"].append(r)
        elif d >= cutoff_6m:
            segments["3~6か月"].append(r)
        elif d >= cutoff_12m:
            segments["6~12か月"].append(r)
        else:
            segments["12か月超"].append(r)

    return segments


def print_time_report(records: list[dict], title_prefix: str = ""):
    """時間軸4区分レポート（CEO判断ルール準拠）"""
    label = f" ({title_prefix})" if title_prefix else ""
    print(f"{'=' * 70}")
    print(f"  時間軸4区分 相場レポート{label}")
    print(f"  基準日: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  総件数: {len(records):,}件（クリーンデータ）")
    print(f"{'=' * 70}")
    print()

    segments = _segment_records(records)

    # メインテーブル
    print("  【価格判断ルール】")
    print("  1. 直近3か月 = 仕入判断の主参考値（最優先）")
    print("  2. 3~6か月   = 参考値（母数補完）")
    print("  3. 6~12か月  = 長期傾向確認用（主判断根拠にしない）")
    print("  4. 12か月超  = 過去参考のみ")
    print()

    header = f"{'区分':<12} {'件数':>7} {'平均価格':>12} {'中央価格':>12} {'最高価格':>12} {'10万超':>7} {'30万超':>7}"
    print(header)
    print("-" * len(header.encode('cp932', errors='replace')))

    priority_marks = [">>>", "  >", "   ", "   "]
    for i, (seg_name, seg_records) in enumerate(segments.items()):
        s = _calc_stats_block(seg_records)
        mark = priority_marks[i]
        if s["count"] == 0:
            print(f"{mark} {seg_name:<10} {'0件':>7}")
        else:
            print(f"{mark} {seg_name:<10} {s['count']:>6,}件 {s['avg']:>11,}円 {s['median']:>11,}円 {s['max_price']:>11,}円 {s['over_10man']:>6,}件 {s['over_30man']:>6,}件")

    print()

    # 直近3か月の詳細ブレイクダウン
    recent = segments["直近3か月"]
    if recent:
        print(f"{'=' * 70}")
        print(f"  【直近3か月 詳細】（仕入判断の主参考値）")
        print(f"{'=' * 70}")
        print()

        # 鑑定会社別
        ngc_recs = [r for r in recent if r.get("grader") == "NGC"]
        pcgs_recs = [r for r in recent if r.get("grader") == "PCGS"]
        print("  [鑑定会社別]")
        for grader_name, grader_recs in [("NGC", ngc_recs), ("PCGS", pcgs_recs)]:
            s = _calc_stats_block(grader_recs)
            if s["count"] > 0:
                print(f"    {grader_name:<6} {s['count']:>5,}件  平均{s['avg']:>10,}円  中央{s['median']:>10,}円  最高{s['max_price']:>10,}円  10万超{s['over_10man']:>4,}件  30万超{s['over_30man']:>4,}件")
        print()

        # 国別 Top 8
        countries = {}
        for r in recent:
            c = r.get("country") or "(未抽出)"
            countries.setdefault(c, []).append(r)
        print("  [国別 Top 8]")
        for country, c_recs in sorted(countries.items(), key=lambda x: -len(x[1]))[:8]:
            s = _calc_stats_block(c_recs)
            if s["count"] > 0:
                print(f"    {country:<12} {s['count']:>5,}件  平均{s['avg']:>10,}円  中央{s['median']:>10,}円  10万超{s['over_10man']:>4,}件")
        print()

        # シリーズ別 Top 8
        series_map = {}
        for r in recent:
            s = r.get("series")
            if s:
                series_map.setdefault(s, []).append(r)
        if series_map:
            print("  [シリーズ別 Top 8]")
            for series_name, s_recs in sorted(series_map.items(), key=lambda x: -len(x[1]))[:8]:
                s = _calc_stats_block(s_recs)
                if s["count"] > 0:
                    print(f"    {series_name:<16} {s['count']:>5,}件  平均{s['avg']:>10,}円  中央{s['median']:>10,}円  10万超{s['over_10man']:>4,}件")
            print()

        # グレード別 Top 10
        grades = {}
        for r in recent:
            g = r.get("grade")
            if g:
                grades.setdefault(g, []).append(r)
        if grades:
            print("  [グレード別 Top 10]")
            for grade_name, g_recs in sorted(grades.items(), key=lambda x: -len(x[1]))[:10]:
                s = _calc_stats_block(g_recs)
                if s["count"] > 0:
                    print(f"    {grade_name:<14} {s['count']:>5,}件  平均{s['avg']:>10,}円  中央{s['median']:>10,}円")
            print()

    print("=" * 70)


def print_stats(records: list[dict], title_prefix: str = ""):
    """集計結果を出力"""
    total = len(records)
    if total == 0:
        print("データなし")
        return

    label = f" ({title_prefix})" if title_prefix else ""
    print(f"{'=' * 70}")
    print(f"  market_transactions 集計レポート{label}")
    print(f"{'=' * 70}")
    print(f"  総件数: {total:,}件")
    print()

    # 1. 国別件数
    countries = {}
    for r in records:
        c = r.get("country") or "(未抽出)"
        countries[c] = countries.get(c, 0) + 1
    print("[1] 国別件数 (Top 15):")
    for country, cnt in sorted(countries.items(), key=lambda x: -x[1])[:15]:
        pct = cnt / total * 100
        bar = "#" * max(1, int(pct / 2))
        print(f"    {country:<16} {cnt:>5,}件 ({pct:>5.1f}%) {bar}")
    print()

    # 2. グレード別件数
    grades = {}
    for r in records:
        g = r.get("grade") or "(未抽出)"
        grades[g] = grades.get(g, 0) + 1
    print("[2] グレード別件数 (Top 15):")
    for grade, cnt in sorted(grades.items(), key=lambda x: -x[1])[:15]:
        pct = cnt / total * 100
        print(f"    {grade:<18} {cnt:>5,}件 ({pct:>5.1f}%)")
    print()

    # 3. NGC/PCGS比率
    ngc = sum(1 for r in records if r.get("grader") == "NGC")
    pcgs = sum(1 for r in records if r.get("grader") == "PCGS")
    other = total - ngc - pcgs
    print("[3] 鑑定会社比率:")
    print(f"    NGC:  {ngc:>6,}件 ({ngc/total*100:.1f}%)")
    print(f"    PCGS: {pcgs:>6,}件 ({pcgs/total*100:.1f}%)")
    if other:
        print(f"    其他: {other:>6,}件 ({other/total*100:.1f}%)")
    print()

    # 4. 価格帯分布
    ranges = [
        ("~1万", 0, 10000),
        ("1-3万", 10000, 30000),
        ("3-5万", 30000, 50000),
        ("5-10万", 50000, 100000),
        ("10-30万", 100000, 300000),
        ("30-50万", 300000, 500000),
        ("50-100万", 500000, 1000000),
        ("100万~", 1000000, float("inf")),
    ]
    print("[4] 価格帯分布:")
    for label, lo, hi in ranges:
        cnt = sum(1 for r in records if lo <= (r.get("price_jpy") or 0) < hi)
        pct = cnt / total * 100
        bar = "#" * max(0, int(pct / 2))
        print(f"    {label:<10} {cnt:>5,}件 ({pct:>5.1f}%) {bar}")

    # 統計値
    prices = [r.get("price_jpy", 0) for r in records if r.get("price_jpy")]
    if prices:
        prices.sort()
        avg = sum(prices) / len(prices)
        median = prices[len(prices) // 2]
        print(f"    ---")
        print(f"    平均: {avg:>12,.0f}円")
        print(f"    中央: {median:>12,}円")
        print(f"    最高: {max(prices):>12,}円")
        print(f"    最低: {min(prices):>12,}円")
    print()

    # 5. ノイズ比率
    noise_set = sum(1 for r in records if "_noise:set" in (r.get("tags") or []))
    noise_nc = sum(1 for r in records if "_noise:non_coin" in (r.get("tags") or []))
    noise_any = sum(1 for r in records if any(
        t.startswith("_noise:") for t in (r.get("tags") or [])))
    clean = total - noise_any
    print("[5] ノイズ比率:")
    print(f"    セット売り:    {noise_set:>5,}件")
    print(f"    非コイン疑い:  {noise_nc:>5,}件")
    print(f"    ノイズ合計:    {noise_any:>5,}件 ({noise_any/total*100:.1f}%)")
    print(f"    クリーン:      {clean:>5,}件 ({clean/total*100:.1f}%)")
    print()

    # 6. 額面 Top 10
    denoms = {}
    for r in records:
        d = r.get("denomination") or "(未抽出)"
        denoms[d] = denoms.get(d, 0) + 1
    print("[6] 額面 Top 10:")
    for denom, cnt in sorted(denoms.items(), key=lambda x: -x[1])[:10]:
        print(f"    {denom:<16} {cnt:>5,}件")
    print()

    # 7. シリーズ Top 10
    series = {}
    for r in records:
        s = r.get("series") or "(未抽出)"
        series[s] = series.get(s, 0) + 1
    print("[7] シリーズ Top 10:")
    for s, cnt in sorted(series.items(), key=lambda x: -x[1])[:10]:
        print(f"    {s:<20} {cnt:>5,}件")
    print()

    # 8. 月別推移
    monthly = {}
    for r in records:
        m = (r.get("sold_date") or "")[:7]
        if m:
            monthly[m] = monthly.get(m, 0) + 1
    if monthly:
        print("[8] 月別取引件数:")
        for month, cnt in sorted(monthly.items()):
            bar = "#" * max(1, int(cnt / 40))
            print(f"    {month} {cnt:>5,}件 {bar}")
    print()

    # 9. 特徴タグ Top 10
    tag_counts = {}
    for r in records:
        for t in (r.get("tags") or []):
            if not t.startswith("_noise:"):
                tag_counts[t] = tag_counts.get(t, 0) + 1
    if tag_counts:
        print("[9] 特徴タグ Top 10:")
        for tag, cnt in sorted(tag_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    {tag:<20} {cnt:>5,}件")
        print()

    # 10. 出品者 Top 10
    sellers = {}
    for r in records:
        s = r.get("seller_name") or "(不明)"
        sellers[s] = sellers.get(s, 0) + 1
    print("[10] 出品者 Top 10:")
    for seller, cnt in sorted(sellers.items(), key=lambda x: -x[1])[:10]:
        print(f"    {seller:<25} {cnt:>5,}件")
    print()

    print("=" * 70)


def main():
    args = sys.argv[1:]
    args = [a for a in args if a != "stats"]

    exclude_noise = "--clean" in args
    time_mode = "--time" in args
    args = [a for a in args if a not in ("--clean", "--time")]

    filters = {}
    title_parts = []
    i = 0
    while i < len(args):
        if args[i] == "--country" and i + 1 < len(args):
            filters["country"] = args[i + 1]
            title_parts.append(f"country={args[i+1]}")
            i += 2
        elif args[i] == "--grader" and i + 1 < len(args):
            filters["grader"] = args[i + 1].upper()
            title_parts.append(f"grader={args[i+1].upper()}")
            i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            filters["source"] = args[i + 1]
            title_parts.append(f"source={args[i+1]}")
            i += 2
        else:
            i += 1

    if exclude_noise:
        title_parts.append("noise除外")

    title_prefix = ", ".join(title_parts)

    print("Supabaseからデータ取得中...")
    # --timeモードはノイズ除外がデフォルト（クリーンデータのみ）
    actual_exclude = exclude_noise or time_mode
    records = fetch_all(filters=filters, exclude_noise=actual_exclude)
    print(f"取得完了: {len(records):,}件")
    print()

    if time_mode:
        print_time_report(records, title_prefix)
    else:
        print_stats(records, title_prefix)


if __name__ == "__main__":
    main()
