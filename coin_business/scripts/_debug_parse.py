"""Parse debug script"""
import sys, io, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

COUNTRY_PATTERNS_E = [
    ('US', ['united states','american','liberty','morgan','saint-gaudens','double eagle','eagle gold','buffalo gold','indian head']),
    ('GB', ['great britain','british','england','united kingdom',' uk ','sovereign','5 pound','five pound','two pound','2 pound','guinea']),
    ('FR', ['france','french','napoleon','napoleon iii','rooster','angel','marianne','20 francs','100 francs']),
    ('JP', ['japan','japanese','meiji','1 yen']),
    ('CA', ['canada','canadian','maple leaf']),
    ('AU', ['australia','australian','kangaroo','nugget']),
    ('AT', ['austria','austrian','philharmonic','wiener']),
]
DENOM_PATTERNS_E = [
    ('Sovereign',    ['sovereign', 'half sovereign']),
    ('5 Pound',      ['5 pound', '5 pounds', 'five pound']),
    ('2 Pound',      ['2 pound', 'two pound']),
    ('Guinea',       ['guinea']),
    ('20 Francs',    ['20 franc', '20 francs']),
    ('100 Francs',   ['100 franc', '100 francs']),
    ('Dollar20',     ['double eagle', '20 dollar']),
    ('Dollar50',     ['american eagle', '50 dollar', '1 oz gold eagle']),
    ('1 Yen',        ['1 yen', 'one yen', 'yen silver']),
    ('Maple Leaf',   ['maple leaf']),
    ('Kangaroo',     ['kangaroo', 'nugget']),
    ('Philharmonic', ['philharmonic']),
]
TYPE_PATTERNS_E = [
    ('Napoleon III', ['napoleon iii']),
    ('Angel',        ['angel', 'ange']),
    ('Rooster',      ['rooster']),
    ('Double Eagle', ['double eagle', 'saint-gaudens']),
    ('Eagle',        ['american eagle', 'gold eagle']),
    ('Sovereign',    ['sovereign']),
    ('Napoleon',     ['napoleon']),
    ('Maple Leaf',   ['maple leaf']),
]

def parse_ebay(title):
    t = title.lower()
    cert = 'PCGS' if 'pcgs' in t else ('NGC' if 'ngc' in t else '')
    material = 'unknown'
    if any(k in t for k in ['gold','sovereign','guinea','napoleon','angel','eagle','double eagle','maple leaf']): material = 'gold'
    elif any(k in t for k in ['silver','yen','morgan','crown','thaler']): material = 'silver'
    elif 'platinum' in t: material = 'platinum'
    year = None
    m = re.search(r'\b(1[5-9]\d{2}|20[0-2]\d)\b', title)
    if m: year = int(m.group(1))
    grade_num = ''
    g = re.search(r'\b(MS|PF|PR|SP|AU|EF|VF|XF)\s*(\d{2})\b', title.upper())
    if g: grade_num = f'{g.group(1)}{g.group(2)}'
    country = 'UNKNOWN'
    for cnt, patterns in COUNTRY_PATTERNS_E:
        if any(p in t for p in patterns): country = cnt; break
    denomination = 'UNKNOWN'
    for denom, patterns in DENOM_PATTERNS_E:
        if any(p in t for p in patterns): denomination = denom; break
    coin_type = 'UNKNOWN'
    for ctype, patterns in TYPE_PATTERNS_E:
        if any(p in t for p in patterns): coin_type = ctype; break
    return {'cert_company': cert, 'material': material, 'country': country,
            'denomination': denomination, 'coin_type': coin_type, 'year': year, 'grade_num': grade_num}

JP_COUNTRY_P = [
    ('US', ['アメリカ','米国','20ドル','リバティ','morgan','double eagle','ダブルイーグル']),
    ('GB', ['イギリス','英国','britain','british','uk','sovereign','ソブリン','5ポンド','ギニー','エリザベス','英']),
    ('FR', ['フランス','france','ナポレオン','napoleon','angel','エンジェル','ロースター']),
    ('JP', ['日本','japan','1円','旧1円','明治','大正','昭和','meiji']),
]
JP_DENOM_P = [
    ('Sovereign',    ['ソブリン','sovereign']),
    ('5 Pound',      ['5ポンド','5 pound']),
    ('Guinea',       ['ギニー','guinea']),
    ('20 Francs',    ['20フラン','20 franc']),
    ('100 Francs',   ['100フラン','100 franc']),
    ('Dollar20',     ['20ドル','double eagle','ダブルイーグル']),
    ('Dollar50',     ['50ドル','イーグル金貨','50 dollar','american eagle']),
    ('1 Yen',        ['1円','旧1円','一円']),
]
JP_TYPE_P = [
    ('Napoleon III', ['ナポレオン3世','napoleon iii']),
    ('Napoleon',     ['ナポレオン','napoleon']),
    ('Angel',        ['エンジェル','angel']),
    ('Rooster',      ['ロースター','rooster']),
    ('Double Eagle', ['ダブルイーグル','double eagle','saint-gaudens']),
    ('Eagle',        ['イーグル金貨','american eagle']),
    ('Sovereign',    ['ソブリン','sovereign']),
    ('Maple Leaf',   ['メイプル','maple leaf']),
]

def parse_jp(title, grade_text='', year=None, cert_company=''):
    t = title.lower()
    material = 'unknown'
    GOLD_KW = ['金貨','ゴールド','ソブリン','ギニー','エンジェル','ナポレオン','メイプル','イーグル','ダブルイーグル',
               'angel','napoleon','eagle','sovereign','maple','philharmon']
    SILVER_KW = ['銀貨','シルバー','silver','1円','旧1円','morgan','crown','yen silver']
    if any(k.lower() in t for k in GOLD_KW): material = 'gold'
    elif any(k.lower() in t for k in SILVER_KW): material = 'silver'
    elif 'platinum' in t or 'プラチナ' in t: material = 'platinum'
    country = 'UNKNOWN'
    for cnt, kws in JP_COUNTRY_P:
        if any(k.lower() in t for k in kws): country = cnt; break
    denomination = 'UNKNOWN'
    for denom, kws in JP_DENOM_P:
        if any(k.lower() in t for k in kws): denomination = denom; break
    coin_type = 'UNKNOWN'
    for ctype, kws in JP_TYPE_P:
        if any(k.lower() in t for k in kws): coin_type = ctype; break
    grade_num = ''
    if grade_text:
        g = re.search(r'(MS|PF|PR|SP|AU|EF|VF|XF)\s*(\d{2})', grade_text.upper())
        if g: grade_num = f'{g.group(1)}{g.group(2)}'
    return {'cert_company': cert_company, 'material': material, 'country': country,
            'denomination': denomination, 'coin_type': coin_type, 'year': year, 'grade_num': grade_num}

# eBay titles from actual script run
ebay_tests = [
    'GB ELIZABETH II GOLD SOVEREIGN - 2005 ++ NGC GRADED MS 63',
    '1904 US Liberty Head Double Eagle Gold 20 Dollar NGC MS62',
    'France 100 Francs Angel Gold Coin 1909-A NGC MS63',
    'Great Britain 5 Pound Gold Coin 2022 PCGS PF70',
    'Japan 1 Yen Meiji 29 Silver Coin NGC MS64',
    '2023 American Gold Eagle NGC MS70 $50',
    'PCGS MS65 France 20 Francs Napoleon 1869',
]
print('=== eBay Parse ===')
for t in ebay_tests:
    p = parse_ebay(t)
    print(f'{t[:55]}')
    print(f'  [{p["cert_company"]} {p["grade_num"]}] {p["material"]} {p["country"]} {p["denomination"]} {p["coin_type"]} yr={p["year"]}')

# JP staging
jp_tests = [
    ('英国 エリザベス ソブリン金貨 2005年 NGC MS65', 'MS65', 2005, 'NGC'),
    ('アメリカ 20ドル金貨 1904年 NGC MS62 ダブルイーグル', 'MS62', 1904, 'NGC'),
    ('フランス エンジェル 100フラン 金貨 1909 NGC MS63', 'MS63', 1909, 'NGC'),
    ('イギリス 5ポンド 金貨 2022年 PCGS PF70UC', 'PF70UC', 2022, 'PCGS'),
    ('旧1円銀貨 明治29年 NGC MS64', 'MS64', 1896, 'NGC'),
    ('1882年 フランス エンジェル 100フラン 金貨 NGC MS63', 'MS63', 1882, 'NGC'),
]
print()
print('=== JP Staging Parse ===')
for title, grade, year, cert in jp_tests:
    p = parse_jp(title, grade, year, cert)
    print(f'{title[:55]}')
    print(f'  [{p["cert_company"]} {p["grade_num"]}] {p["material"]} {p["country"]} {p["denomination"]} {p["coin_type"]} yr={p["year"]}')

# Cross match test
print()
print('=== Cross-Match Test ===')
ep = parse_ebay('GB ELIZABETH II GOLD SOVEREIGN - 2005 ++ NGC GRADED MS 63')
db = parse_jp('英国 エリザベス ソブリン金貨 2005年 NGC MS65', 'MS65', 2005, 'NGC')
db['sold_price_jpy'] = 90000
print('eBay Sovereign MS63 vs JP Sovereign MS65 (grade delta):')
for k in ['cert_company','material','country','denomination','coin_type','year','grade_num']:
    match = ep[k] == db[k]
    print(f'  {k}: "{ep[k]}" vs "{db[k]}" {"OK" if match else "DIFF"}')

print()
ep2 = parse_ebay('France 100 Francs Angel Gold Coin 1909-A NGC MS63')
db2 = parse_jp('フランス エンジェル 100フラン 金貨 1909 NGC MS63', 'MS63', 1909, 'NGC')
db2['sold_price_jpy'] = 880000
print('eBay France Angel 1909 MS63 vs JP Angel 1909 MS63 (R1 exact):')
for k in ['cert_company','material','country','denomination','coin_type','year','grade_num']:
    match = ep2[k] == db2[k]
    print(f'  {k}: "{ep2[k]}" vs "{db2[k]}" {"OK" if match else "DIFF"}')
