"""Round 2: HIGH_GRADE + YEAR_DELTA 候補プール抽出
- confidence >= 0.7
- cert_company あり
- grade_text あり (Detailsなし)
- sold_price_jpy >= 50000 (裁定余地のある価格帯)
- 単品っぽいタイトル（ロット物除外ワード）
"""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from scripts.supabase_client import get_client
c = get_client()

# 除外キーワード（複数枚ロット・Details）
EXCLUDE_TITLE = ['セット', 'ロット', '枚組', 'lot', 'set ', 'details', 'Details',
                 '2枚', '3枚', '4枚', '5枚', '10枚', 'collection']
EXCLUDE_GRADE = ['details', 'Details', 'DETAILS', 'damage', 'bent', 'holed',
                 'cleaned', 'Cleaned', 'scratched', 'repaired']

rows = (c.table('yahoo_sold_lots_staging')
    .select('yahoo_lot_id,lot_title,title_normalized,year,country,denomination,'
            'grade,grade_text,cert_company,cert_number,sold_price_jpy,sold_date,parse_confidence')
    .gte('parse_confidence', 0.7)
    .not_.is_('cert_company', 'null')
    .not_.is_('grade_text', 'null')
    .gte('sold_price_jpy', 50000)
    .order('sold_price_jpy', desc=True)
    .limit(500)
    .execute()
)

# フィルタリング
pool = []
for r in rows.data:
    title = (r['title_normalized'] or r['lot_title'] or '').lower()
    grade_t = (r['grade_text'] or '').lower()

    # Details/問題コイン除外
    if any(ex in grade_t for ex in EXCLUDE_GRADE):
        continue
    # ロット除外
    if any(ex.lower() in title for ex in EXCLUDE_TITLE):
        continue
    # PF/PRのみも低優先（流通少ない）
    pool.append(r)

print(f'HIGH_GRADE/YEAR_DELTA プール: {len(pool)} 件（フィルター後）')
print(f'（元データ: {len(rows.data)} 件）')
print()

# グレード帯別集計
from collections import Counter
grade_counts = Counter()
for r in pool:
    g = (r['grade_text'] or '').upper()
    if 'MS7' in g: grade_counts['MS70'] += 1
    elif 'MS6' in g: grade_counts['MS6x'] += 1
    elif 'MS5' in g: grade_counts['MS5x'] += 1
    elif 'PF7' in g or 'PR7' in g: grade_counts['PF/PR70'] += 1
    elif 'PF6' in g or 'PR6' in g: grade_counts['PF/PR6x'] += 1
    else: grade_counts['Other'] += 1

print('=== グレード分布 ===')
for g, cnt in sorted(grade_counts.items(), key=lambda x: -x[1]):
    print(f'  {g}: {cnt}件')

print()
print('=== 価格帯上位30件（HIGH_GRADE/YEAR_DELTA候補） ===')
for r in pool[:30]:
    yr   = str(r['year']) if r['year'] else '?'
    co   = r['cert_company'] or ''
    cn   = r['cert_number'] or '-'
    gr   = r['grade_text'] or ''
    pr   = r['sold_price_jpy'] or 0
    conf = r['parse_confidence']
    ti   = (r['title_normalized'] or r['lot_title'] or '')[:60]
    print(f'  ¥{pr:>8,} | conf={conf:.2f} | {co} {cn[:12]:<12} | yr={yr:<6} | {gr:<10} | {ti}')
