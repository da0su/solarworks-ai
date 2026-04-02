"""Staging データのCEOレビュー用サマリー表示"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from scripts.supabase_client import get_client

client = get_client()

res = client.table('yahoo_sold_lots_staging').select('id').execute()
total = len(res.data)
print(f'=== yahoo_sold_lots_staging サマリー ===')
print(f'総件数: {total:,}件 (status=PENDING_CEO)')

# High confidence top by price
res_hi = client.table('yahoo_sold_lots_staging').select(
    'yahoo_lot_id, lot_title, title_normalized, grade_text, cert_company, year, sold_price_jpy, sold_date, parse_confidence'
).gte('parse_confidence', 0.7).order('sold_price_jpy', desc=True).limit(15).execute()

print()
print('=== 高信頼度 Top 15 by price (confidence >= 0.7) ===')
for r in res_hi.data:
    c = r.get('parse_confidence') or 0
    price = r.get('sold_price_jpy') or 0
    cc = r.get('cert_company') or '---'
    gr = r.get('grade_text') or '---'
    yr = str(r.get('year') or '')
    title = r.get('title_normalized') or r.get('lot_title') or ''
    print(f'  JPY{price:>9,}  {cc:5} {gr:10}  {yr:6}  conf={c:.2f}  {title[:55]}')

# Confidence distribution
res_all = client.table('yahoo_sold_lots_staging').select('parse_confidence, cert_company').execute()
hi_n = sum(1 for r in res_all.data if (r.get('parse_confidence') or 0) >= 0.7)
mid_n = sum(1 for r in res_all.data if 0.3 <= (r.get('parse_confidence') or 0) < 0.7)
lo_n = sum(1 for r in res_all.data if (r.get('parse_confidence') or 0) < 0.3)
ngc = sum(1 for r in res_all.data if r.get('cert_company') == 'NGC')
pcgs = sum(1 for r in res_all.data if r.get('cert_company') == 'PCGS')
raw_n = sum(1 for r in res_all.data if not r.get('cert_company'))

print()
print('=== データ品質分布 ===')
print(f'  信頼度高 (>= 0.7): {hi_n:,}件 ({hi_n/total*100:.1f}%)')
print(f'  信頼度中 (0.3-0.7): {mid_n:,}件 ({mid_n/total*100:.1f}%)')
print(f'  信頼度低 (< 0.3):  {lo_n:,}件 ({lo_n/total*100:.1f}%)')
print()
print(f'  NGC鑑定: {ngc:,}件 | PCGS鑑定: {pcgs:,}件 | RAW/不明: {raw_n:,}件')

# Price range distribution
prices = [r.get('sold_price_jpy') or 0 for r in res_hi.data]
res_price = client.table('yahoo_sold_lots_staging').select('sold_price_jpy').execute()
all_prices = sorted([r.get('sold_price_jpy') or 0 for r in res_price.data], reverse=True)
print()
print('=== 落札価格分布 ===')
buckets = [
    ('50万円以上', sum(1 for p in all_prices if p >= 500000)),
    ('10-50万円', sum(1 for p in all_prices if 100000 <= p < 500000)),
    ('3-10万円', sum(1 for p in all_prices if 30000 <= p < 100000)),
    ('3万円未満', sum(1 for p in all_prices if 0 < p < 30000)),
]
for label, cnt in buckets:
    print(f'  {label}: {cnt:,}件')

# Sold date range
res_dates = client.table('yahoo_sold_lots_staging').select('sold_date').not_.is_('sold_date', 'null').order('sold_date').execute()
if res_dates.data:
    oldest = res_dates.data[0].get('sold_date')
    newest = res_dates.data[-1].get('sold_date')
    print()
    print(f'=== 落札日範囲 ===')
    print(f'  最古: {oldest}  最新: {newest}')
