"""eBay HTML parse debug"""
import requests, re
from bs4 import BeautifulSoup

url = 'https://www.ebay.com/sch/i.html'
params = {
    '_nkw': 'NGC coins',
    'LH_Complete': '1',
    'LH_Sold': '1',
    '_ipg': '240',
    'rt': 'nc',
    '_udlo': '10',
}
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}
resp = requests.get(url, params=params, headers=headers, timeout=30)
print(f"Status: {resp.status_code}, Length: {len(resp.text)}")

# Save HTML for debug
with open('data/ebay/_debug2.html', 'w', encoding='utf-8') as f:
    f.write(resp.text)

soup = BeautifulSoup(resp.text, 'html.parser')
title_tag = soup.find('title')
print(f"Page title: {title_tag.get_text()[:60] if title_tag else 'none'}")

srp = soup.find(class_='srp-results')
if not srp:
    print("srp-results NOT found! Checking alternatives...")
    # Check for CAPTCHA or different page
    captcha = soup.find(id='captcha_form') or soup.find(class_='captcha')
    if captcha:
        print("CAPTCHA detected!")
    else:
        all_classes = set()
        for tag in soup.find_all(True, class_=True):
            for c in tag.get('class', []):
                if 'srp' in c or 'result' in c or 'item' in c:
                    all_classes.add(c)
        print(f"Relevant classes: {sorted(all_classes)[:20]}")
    import sys
    sys.exit(1)

cards = srp.find_all('li', class_=re.compile(r's-card'))
print(f"Total cards: {len(cards)}")

MONTH_MAP = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
             'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}

for i, card in enumerate(cards[:5]):
    texts = [t.strip() for t in card.stripped_strings]

    # Sold date
    sold_date = None
    for t in texts:
        m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d+),?\s+(\d{4})', t)
        if m:
            sold_date = f"{m.group(3)}-{MONTH_MAP[m.group(1)]}-{int(m.group(2)):02d}"
            break

    # Price (dollar sign)
    price = None
    for t in texts:
        m = re.search(r'[\$]([\d,]+\.?\d*)', t)
        if m:
            price = float(m.group(1).replace(',', ''))
            break

    # Title from link
    link = card.find('a', href=re.compile(r'/itm/'))
    title = ''
    item_id = ''
    if link:
        title = link.get_text(strip=True)
        title = re.sub(r'Opens in a new window or tab$', '', title).strip()
        href = link.get('href', '')
        m = re.search(r'/itm/(\d+)', href)
        if m:
            item_id = m.group(1)

    safe_title = title[:50].encode('ascii', errors='replace').decode()
    safe_texts = [t[:25].encode('ascii', errors='replace').decode() for t in texts[:6]]
    print(f"Card {i}: date={sold_date} price=${price} id={item_id}")
    print(f"  title: {safe_title}")
    print(f"  texts: {safe_texts}")
    print()
