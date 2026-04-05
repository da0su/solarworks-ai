"""Gate 2/3/4: cert照合 + coin_slab_data参照 + 収益計算"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from scripts.supabase_client import get_client
c = get_client()

# 4件の cert_number を coin_slab_data と照合
certs = [
    ('NGC',  '403024500',  'France 100F Angel 1904A MS62'),
    ('NGC',  '3960383026', 'France 2 Louis d\'Or 1786A AU Details'),
    ('PCGS', '51661004',   'Akita 4mon6bu Silver AU58'),
    ('PCGS', '82158327',   'USA JFK Half Dollar 1966 MS64'),
]

print('=== Gate 2: coin_slab_data 照合 ===')
for company, cert_no, desc in certs:
    rows = (c.table('coin_slab_data')
        .select('id,management_no,grader,grade,cert_number,ref1_buy_limit_jpy,ref2_yahoo_price_jpy,ref2_sold_date,weight_g,material,purity')
        .eq('cert_number', cert_no)
        .limit(5)
        .execute()
    )
    if rows.data:
        for r in rows.data:
            print(f'\n  MATCH: {desc}')
            print(f'    cert: {company} {cert_no}')
            print(f'    grade: {r["grade"]}')
            print(f'    ref2_yahoo_price: ¥{r["ref2_yahoo_price_jpy"]:,}' if r["ref2_yahoo_price_jpy"] else '    ref2_yahoo_price: None')
            print(f'    ref1_buy_limit:   ¥{r["ref1_buy_limit_jpy"]:,}' if r["ref1_buy_limit_jpy"] else '    ref1_buy_limit: None')
            print(f'    ref2_sold_date: {r["ref2_sold_date"]}')
            print(f'    material: {r["material"]}, purity: {r["purity"]}, weight: {r["weight_g"]}g')
            print(f'    mgmt_no: {r["management_no"]}')
    else:
        # 部分一致も試す (cert_no末尾のみ)
        print(f'\n  NO MATCH in coin_slab_data: {company} {cert_no} ({desc})')

print()
print('=== Gate 3: Audit判定 ===')
for company, cert_no, desc in certs:
    grade_text = ''
    if 'Details' in desc or 'DETAILS' in desc:
        status = 'AUDIT_FAIL (Details/問題コイン)'
    elif '銀判' in desc or 'Akita' in desc:
        status = 'AUDIT_HOLD (地方貨・US/UK以外の可能性)'
    else:
        status = 'AUDIT_PASS (候補)'
    print(f'  {company} {cert_no}: {status}')
