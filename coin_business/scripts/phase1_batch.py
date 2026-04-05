"""Phase 1 Batch処理: 画像取得→Claude Vision判定→Batch API送信"""
import sys, json, time, urllib.request, re, base64, os, argparse
from PIL import Image
from io import BytesIO
import anthropic

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.supabase_client import get_client

PROMPT = """Above are all product images from a Yahoo Auction listing of a graded coin.

Please identify:

1. FRONT: The image showing the FRONT of an NGC/PCGS grading slab.
   MUST have ALL of these features:
   - A clear rectangular plastic case outline visible
   - A text label area in the UPPER portion (year, grade, cert number, barcode)
   - A coin held by white/black prongs in the LOWER portion
   - NGC logo (scales icon) or PCGS logo (shield icon) visible
   If multiple slab front images exist, choose the one taken most directly from the front with the clearest, most readable text.
   REJECT: coin-only photos without case, box photos, website screenshots, angled photos where text is hard to read.
   A bare coin photo (no plastic case visible) is NOT a slab image.
   Note: If this is a SET listing, one image may show multiple slabs side by side. This is acceptable and preferred.

2. BACK:
   Priority 1: SLAB_BACK
   Priority 2: COIN_REVERSE
   Priority 3: NONE
   FRONT and BACK must be DIFFERENT image numbers.

3. SLAB_TEXT: Read ALL text from the FRONT slab label as JSON:
{"grader":"","line1":"","line2":"","line3":"","grade":"","cert_number":"","label_type":"","material_hint":""}

Answer format:
FRONT: [number] or NONE
BACK: [number] [SLAB_BACK/COIN_REVERSE/NONE]
SLAB_TEXT: {json}"""


def find_images(obj, imgs=None):
    if imgs is None:
        imgs = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and 'auctions.c.yimg.jp/images' in v and 'auc-pctr' not in v:
                imgs.append(v)
            else:
                find_images(v, imgs)
    elif isinstance(obj, list):
        for item in obj:
            find_images(item, imgs)
    return imgs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=500)
    parser.add_argument('--batch-num', type=int, default=1)
    args = parser.parse_args()

    db = get_client()
    claude = anthropic.Anthropic()

    # レコード取得
    all_records = []
    last_id = ''
    needed = args.start + args.count
    while len(all_records) < needed:
        qb = (db.table('market_transactions')
            .select('id, title, url')
            .eq('source', 'yahoo')
            .order('id')
            .limit(1000))
        if last_id:
            qb = qb.gt('id', last_id)
        resp = qb.execute()
        if not resp.data:
            break
        all_records.extend(resp.data)
        last_id = resp.data[-1]['id']

    targets = all_records[args.start:args.start + args.count]
    print(f'Batch {args.batch_num}: {len(targets)}件 (offset {args.start}-{args.start + args.count})')

    # 画像準備+リクエスト構築
    batch_requests = []
    skipped = 0

    for i, rec in enumerate(targets):
        if i % 50 == 0:
            print(f'  {i}/{len(targets)} 準備中...')
            sys.stdout.flush()

        try:
            req = urllib.request.Request(rec['url'], headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
        except:
            skipped += 1
            continue

        next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not next_data:
            skipped += 1
            continue

        data = json.loads(next_data.group(1))
        originals = list(dict.fromkeys(find_images(data)))
        if not originals:
            skipped += 1
            continue

        images_b64 = []
        for j, img_url in enumerate(originals):
            try:
                req = urllib.request.Request(img_url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://auctions.yahoo.co.jp/'
                })
                img_data = urllib.request.urlopen(req, timeout=10).read()
                img = Image.open(BytesIO(img_data))
                w, h = img.size
                if w > 500:
                    img = img.resize((500, int(h * 500 / w)), Image.LANCZOS)
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=60)
                images_b64.append({
                    'index': j,
                    'b64': base64.b64encode(buf.getvalue()).decode('utf-8')
                })
            except:
                pass

        if not images_b64:
            skipped += 1
            continue

        content = []
        for img in images_b64:
            content.append({"type": "text", "text": f"Image {img['index']}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": img['b64']
            }})
        content.append({"type": "text", "text": PROMPT})

        batch_requests.append({
            "custom_id": rec['id'],
            "params": {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": content}]
            }
        })

    print(f'リクエスト構築完了: {len(batch_requests)}件 (スキップ: {skipped}件)')
    sys.stdout.flush()

    # Batch送信
    print("Batch送信中...")
    sys.stdout.flush()
    batch = claude.messages.batches.create(requests=batch_requests)
    print(f'Batch ID: {batch.id}')
    print(f'Status: {batch.processing_status}')

    # 保存
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    with open(os.path.join(data_dir, f'batch_{args.batch_num}_id.json'), 'w') as f:
        json.dump({
            'batch_id': batch.id,
            'batch_num': args.batch_num,
            'count': len(batch_requests),
            'skipped': skipped,
            'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        }, f, indent=2)

    print(f'Batch {args.batch_num} 送信完了。')


if __name__ == '__main__':
    main()
