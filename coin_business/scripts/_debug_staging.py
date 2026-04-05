"""debug: check staging denomination parsing"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from scripts.daily_scan import parse_jp_title
from scripts.supabase_client import get_client

c = get_client()
r = c.table('yahoo_sold_lots_staging').select(
    'lot_title,grade_text,cert_company,year,denomination,sold_price_jpy'
).in_('cert_company',['NGC','PCGS']).gte('parse_confidence',0.7).gte('sold_price_jpy',100000).limit(25).execute()

print(f'{"lot_title":38s} | {"DB_denom":12s} | {"parsed_denom":15s} | grade_num')
print('-'*95)
denom_stats = {}
for row in r.data:
    parsed = parse_jp_title(
        title=row.get('lot_title',''),
        grade_text=row.get('grade_text',''),
        year=row.get('year'),
        cert_company=row.get('cert_company',''),
    )
    db_d = str(row.get('denomination','?'))[:12]
    pd = parsed['denomination']
    gn = parsed['grade_num']
    denom_stats[pd] = denom_stats.get(pd, 0) + 1
    print(f'{row["lot_title"][:38]:38s} | {db_d:12s} | {pd:15s} | {gn}')

print()
print('parsed_denom distribution:')
for d, n in sorted(denom_stats.items(), key=lambda x: -x[1]):
    print(f'  {n:3d}  {d}')
