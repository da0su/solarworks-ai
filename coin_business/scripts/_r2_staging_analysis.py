"""Round 2: staging 347件のコイン種別集計 + 複数件あるシリーズ特定"""
import sys, io, re
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from scripts.supabase_client import get_client
c = get_client()

# 全 conf>=0.7, cert_company あり, grade_text あり, 50000円以上
rows = (c.table('yahoo_sold_lots_staging')
    .select('yahoo_lot_id,lot_title,title_normalized,year,country,denomination,'
            'grade_text,cert_company,cert_number,sold_price_jpy,sold_date,parse_confidence')
    .gte('parse_confidence', 0.7)
    .not_.is_('cert_company', 'null')
    .not_.is_('grade_text', 'null')
    .gte('sold_price_jpy', 50000)
    .order('sold_price_jpy', desc=True)
    .limit(500)
    .execute()
)

EXCLUDE_GRADE = ['details','Details','DETAILS','damage','bent','holed','cleaned','scratched','repaired']
EXCLUDE_TITLE = ['セット','ロット','枚組','lot','2枚','3枚','4枚','5枚','10枚']

pool = []
for r in rows.data:
    title = (r['title_normalized'] or r['lot_title'] or '').lower()
    grade_t = (r['grade_text'] or '').lower()
    if any(ex in grade_t for ex in EXCLUDE_GRADE): continue
    if any(ex.lower() in title for ex in EXCLUDE_TITLE): continue
    pool.append(r)

# キーワードでシリーズ分類
def classify(r):
    t = (r['title_normalized'] or r['lot_title'] or '').lower()
    yr = r['year']
    co = r['cert_company'] or ''
    dn = r['denomination'] or ''
    if 'morgan' in t: return 'Morgan Dollar'
    if 'peace dollar' in t or ('peace' in t and 'dollar' in t): return 'Peace Dollar'
    if '100フラン' in t and ('エンジェル' in t or 'angel' in t or 'フランス' in t.lower() or 'france' in t): return 'France 100F Angel'
    if '100フラン' in t: return 'France 100F Other'
    if 'angel' in t and '100' in t and 'franc' in t: return 'France 100F Angel'
    if ('20ドル' in t or 'double eagle' in t or '20 dollar' in t.lower()) and ('st. gaudens' in t or 'saint' in t or 'liberty' in t or 'gaudens' in t): return 'US $20 Double Eagle'
    if ('20ドル' in t or 'double eagle' in t): return 'US $20 Double Eagle'
    if 'ソブリン' in t or 'sovereign' in t: return 'GB Sovereign'
    if '5ポンド' in t and ('金' in t or 'gold' in t or 'pound' in t.lower()): return 'GB 5 Pound Gold'
    if 'philharmonic' in t or 'フィルハーモニー' in t: return 'Austrian Philharmonic'
    if 'krugerrand' in t or 'クルーガーランド' in t: return 'SA Krugerrand'
    if 'maple' in t or 'メイプル' in t: return 'CA Maple Leaf'
    if ('eagle' in t and 'gold' in t and 'american' in t) or ('イーグル' in t and '金' in t): return 'US Gold Eagle'
    if 'napoleon' in t and ('100' in t or 'フラン' in t): return 'France Napoleon 100F'
    if '1円' in t and ('明治' in t or 'meiji' in t.lower()): return 'Japan 1 Yen Meiji'
    if '20フラン' in t or '20 franc' in t: return 'France 20F Napoleon'
    if 'british' in t or 'イギリス' in t or 'united kingdom' in t or 'great britain' in t: return 'UK Other'
    return 'Other'

# 集計
by_series = defaultdict(list)
for r in pool:
    s = classify(r)
    by_series[s].append(r)

print(f'=== コイン種別集計 (pool={len(pool)}件) ===\n')
for series, items in sorted(by_series.items(), key=lambda x: -len(x[1])):
    prices = sorted([r['sold_price_jpy'] for r in items if r['sold_price_jpy']], reverse=True)
    grades = [r['grade_text'] for r in items if r['grade_text']]
    print(f'  {series:<30} {len(items):>3}件  価格: ¥{min(prices):,}〜¥{max(prices):,}')
    for r in items[:3]:
        yr = str(r['year']) if r['year'] else '?'
        gr = r['grade_text'] or ''
        pr = r['sold_price_jpy'] or 0
        cn = r['cert_number'] or '-'
        ti = (r['title_normalized'] or r['lot_title'] or '')[:55]
        print(f'      ¥{pr:>8,} | yr={yr} | {r["cert_company"]} {cn[:12]} | {gr:<10} | {ti}')

print(f'\n=== 複数件シリーズ（YEAR_DELTA/HIGH_GRADE候補） ===')
multi = {s: v for s, v in by_series.items() if len(v) >= 3}
for series, items in sorted(multi.items(), key=lambda x: -len(x[1])):
    prices = [r['sold_price_jpy'] for r in items if r['sold_price_jpy']]
    avg = sum(prices)/len(prices) if prices else 0
    grade_list = list(set(r['grade_text'] for r in items if r['grade_text']))[:8]
    print(f'\n  [{series}] {len(items)}件 | 平均¥{avg:,.0f}')
    print(f'  グレード例: {", ".join(grade_list[:6])}')
