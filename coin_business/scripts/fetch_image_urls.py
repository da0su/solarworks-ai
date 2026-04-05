"""
fetch_image_urls.py
MARKETING_REVIEW items の image_url を取得して DB 更新する。
- eBay: item page の main image
- NumisBids (Spink/Noble): lot page の main image
"""

import re
import sys
import time
import requests
from bs4 import BeautifulSoup
from scripts.supabase_client import get_client

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

def fetch_ebay_image(url: str) -> str | None:
    """eBay リスティングページからメイン画像URLを取得。"""
    try:
        # eBay item ID を URL から抽出
        m = re.search(r'/itm/(\d+)', url)
        if not m:
            return None
        item_id = m.group(1)

        # eBay の OG image は取得しやすい
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  [WARN] eBay HTTP {resp.status_code} for {item_id}")
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 1) og:image メタタグ
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            img = og['content'].strip()
            if img.startswith('http'):
                return img

        # 2) itemImage クラス or data-idx=0 の img
        img_tag = soup.find('img', {'data-idx': '0'})
        if img_tag:
            src = img_tag.get('src') or img_tag.get('data-src') or ''
            if src.startswith('http'):
                return src

        # 3) JSON-LD
        for script in soup.find_all('script', type='application/ld+json'):
            txt = script.string or ''
            m2 = re.search(r'"image"\s*:\s*"(https://[^"]+)"', txt)
            if m2:
                return m2.group(1)

        return None
    except Exception as e:
        print(f"  [ERR] ebay fetch: {e}")
        return None


def fetch_numisbids_image(url: str) -> str | None:
    """NumisBids lot ページからメイン画像URLを取得。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  [WARN] NumisBids HTTP {resp.status_code} for {url}")
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 1) og:image
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            img = og['content'].strip()
            if img.startswith('http'):
                return img

        # 2) .lot-image img
        lot_img = soup.find(class_='lot-image')
        if lot_img:
            img_tag = lot_img.find('img')
            if img_tag:
                src = img_tag.get('src') or ''
                if src.startswith('http'):
                    return src
                elif src.startswith('/'):
                    return 'https://www.numisbids.com' + src

        # 3) 最初の大きな img
        for img_tag in soup.find_all('img'):
            src = img_tag.get('src') or ''
            if ('lot' in src.lower() or 'coin' in src.lower()) and src.startswith('http'):
                return src

        return None
    except Exception as e:
        print(f"  [ERR] numisbids fetch: {e}")
        return None


def run(dry_run: bool = True):
    c = get_client()
    rows = (c.table('ceo_review_log')
              .select('id,title_snapshot,source_group,auction_house,url,image_url,evidence_status')
              .eq('marketing_status', 'MARKETING_REVIEW')
              .execute().data)

    print(f"MARKETING_REVIEW items: {len(rows)}")
    print()

    updates = []
    for r in rows:
        item_id = r['id']
        title = r['title_snapshot'][:60]
        url = r['url'] or ''
        house = r['auction_house']
        existing_img = r['image_url']

        if existing_img:
            print(f"[SKIP] {item_id[:8]} already has image: {existing_img[:60]}")
            continue

        print(f"[FETCH] {item_id[:8]} {house} | {title}")

        img_url = None
        if house == 'EBAY':
            img_url = fetch_ebay_image(url)
        elif url and 'numisbids.com' in url:
            img_url = fetch_numisbids_image(url)

        if img_url:
            print(f"  => {img_url[:80]}")
            updates.append({
                'id': item_id,
                'image_url': img_url,
                'evidence_status': 'スラブ確認済'
            })
        else:
            print(f"  => [NOT FOUND]")
            updates.append({
                'id': item_id,
                'image_url': None,
                'evidence_status': 'スラブ未確認(要手動)'
            })

        time.sleep(1.5)

    print()
    print(f"Updates prepared: {len(updates)}")
    found = sum(1 for u in updates if u['image_url'])
    print(f"  image found: {found} / not found: {len(updates)-found}")

    if dry_run:
        print("[DRY-RUN] no DB writes")
        return

    # DB update
    ok = 0
    for u in updates:
        try:
            c.table('ceo_review_log').update({
                'image_url': u['image_url'],
                'evidence_status': u['evidence_status']
            }).eq('id', u['id']).execute()
            ok += 1
        except Exception as e:
            print(f"  [ERR] update {u['id'][:8]}: {e}")

    print(f"DB updated: {ok}/{len(updates)} items")


if __name__ == '__main__':
    dry = '--no-dry-run' not in sys.argv
    if not dry:
        print("[LIVE MODE] will write to DB")
    else:
        print("[DRY-RUN MODE] pass --no-dry-run to write")
    print()
    run(dry_run=dry)
