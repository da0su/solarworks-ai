import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from scripts.supabase_client import get_client
c = get_client()

# cert_number あり (conf>=0.7), 高価格順
rows = (c.table('yahoo_sold_lots_staging')
    .select('yahoo_lot_id,lot_title,title_normalized,year,denomination,grade_text,cert_company,cert_number,sold_price_jpy,sold_date,parse_confidence')
    .gte('parse_confidence', 0.7)
    .not_.is_('cert_company', 'null')
    .not_.is_('cert_number', 'null')
    .order('sold_price_jpy', desc=True)
    .limit(100)
    .execute()
)
print(f'cert_number あり (conf>=0.7): {len(rows.data)} 件')
print()
for r in rows.data[:30]:
    yr = str(r['year']) if r['year'] else '?'
    dn = r['denomination'] or '?'
    gr = r['grade_text'] or '?'
    pr = r['sold_price_jpy'] or 0
    co = r['cert_company'] or ''
    cn = r['cert_number'] or ''
    ti = (r['title_normalized'] or r['lot_title'] or '')[:60]
    print(f"  conf={r['parse_confidence']:.2f} | {co} {cn} | yr={yr} {dn} | {gr} | JPY{pr:,} | {ti}")
