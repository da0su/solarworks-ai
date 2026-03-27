"""Phase 1: DB精査プロセッサー
- ヤフオクURLから__NEXT_DATA__で画像+落札情報を取得
- Claude Visionでスラブ表面/裏面を判定+OCR読み取り
- Supabase Storageに画像保存
- DB更新（coin_id, slab_text, 画像パス等）
"""
import urllib.request, re, json, base64, os, sys, time, argparse
from PIL import Image
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv
import anthropic

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
load_dotenv(_env_path, override=True)
from scripts.supabase_client import get_client

API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
claude = anthropic.Anthropic(api_key=API_KEY)
db = get_client()

SLAB_DETECT_PROMPT = """Above are all product images from a Yahoo Auction listing of a graded coin.

Please identify:

1. FRONT: The image showing the FRONT of an NGC/PCGS grading slab.
   MUST have ALL of these features:
   - A clear rectangular plastic case outline visible
   - A text label area in the UPPER portion (year, grade, cert number, barcode)
   - A coin held by white/black prongs in the LOWER portion
   - NGC logo (scales icon) or PCGS logo (shield icon) visible
   CRITICAL for PCGS slabs:
   - The FRONT shows the coin's OBVERSE (portrait/head side) with detailed grade text, cert number, and barcode in the label area.
   - The BACK shows the PCGS shield logo PROMINENTLY at the top, with the coin's REVERSE visible below.
   - If the PCGS shield logo is the DOMINANT large element at the top of the image, it is the BACK, NOT the FRONT.
   - The FRONT label has smaller text with grade details (e.g. "MS64", "Genuine"), NOT a large shield logo.
   If multiple slab front images exist, choose the one taken most directly from the front with the clearest, most readable text.
   REJECT: coin-only photos without case, box photos, website screenshots, angled photos where text is hard to read.
   A bare coin photo (no plastic case visible) is NOT a slab image.
   Note: If this is a SET listing, one image may show multiple slabs side by side. This is acceptable and preferred - choose the image showing all slabs together rather than individual shots.

2. BACK:
   Priority 1: SLAB_BACK - The BACK of the slab case.
   - NGC back: hologram rainbow-colored label at top with "NGC" and "Numismatic Guaranty Company" text, coin reverse visible below
   - PCGS back: PCGS hologram or text at top, coin reverse visible below
   - The label must be at the TOP (not upside down)
   If multiple slab back images exist, choose the one most directly from the front.
   Note: For SET listings, prefer the image showing all slab backs together.

   Priority 2: COIN_REVERSE - A photo showing the opposite side of the coin from what appears in the FRONT slab image. Different design = reverse side.

   Priority 3: NONE

3. SLAB_TEXT: Read ALL text from the FRONT slab label and return as JSON:
{
  "grader": "NGC or PCGS",
  "line1": "first line of text (usually year + country + denomination)",
  "line2": "second line (usually series/type)",
  "line3": "third line if exists (label type like First Releases)",
  "grade": "full grade (e.g. MS 64, PF 70 ULTRA CAMEO, AU DETAILS CLEANED)",
  "cert_number": "certification number (e.g. 4259586-045)",
  "label_type": "special label if any (FIRST RELEASES, EARLY RELEASES, FIRST DAY OF ISSUE, etc.)",
  "material_hint": "S=Silver, G=Gold, P=Platinum, or UNKNOWN if not indicated"
}

IMPORTANT:
- FRONT and BACK must be DIFFERENT image numbers
- A bare coin photo (no plastic case) is NOT a slab image
- Read the slab text EXACTLY as printed, do not guess or infer

Answer in this EXACT format:
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


def find_value(obj, keys, results=None):
    if results is None:
        results = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                results[k] = v
            find_value(v, keys, results)
    elif isinstance(obj, list):
        for item in obj:
            find_value(item, keys, results)
    return results


def process_one(record):
    """1件のヤフオクレコードを処理"""
    url = record['url']
    rec_id = record['id']

    # 1. HTML取得
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
    except:
        return {'id': rec_id, 'status': 'html_fail', 'error': 'HTML取得失敗', 'originals': []}

    # 2. __NEXT_DATA__パース
    next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not next_data:
        return {'id': rec_id, 'status': 'no_next_data', 'error': '__NEXT_DATA__なし', 'originals': []}

    data = json.loads(next_data.group(1))
    originals = list(dict.fromkeys(find_images(data)))
    info = find_value(data, ['title', 'price', 'bids', 'initPrice', 'endTime'])

    if not originals:
        return {'id': rec_id, 'status': 'no_images', 'error': '画像0枚', 'originals': []}

    # 3. 全画像をダウンロード+base64化
    images_b64 = []
    images_raw = {}
    for i, img_url in enumerate(originals):
        try:
            req = urllib.request.Request(img_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://auctions.yahoo.co.jp/'
            })
            img_data = urllib.request.urlopen(req, timeout=10).read()
            img = Image.open(BytesIO(img_data))
            w, h = img.size
            images_raw[i] = {'data': img_data, 'w': w, 'h': h, 'url': img_url}

            if w > 500:
                img = img.resize((500, int(h * 500 / w)), Image.LANCZOS)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=60)
            images_b64.append({
                'index': i,
                'b64': base64.b64encode(buf.getvalue()).decode('utf-8')
            })
        except:
            pass

    if not images_b64:
        return {'id': rec_id, 'status': 'dl_fail', 'error': '画像DL失敗', 'originals': []}

    # 4. Claude Vision判定+OCR
    content = []
    for img in images_b64:
        content.append({"type": "text", "text": f"Image {img['index']}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": img['b64']
        }})
    content.append({"type": "text", "text": SLAB_DETECT_PROMPT})

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": content}]
        )
        answer = response.content[0].text.strip()
    except Exception as e:
        return {'id': rec_id, 'status': 'api_fail', 'error': str(e)[:50], 'originals': originals}

    # 5. レスポンスパース
    front_match = re.search(r'FRONT:\s*(\d+)', answer)
    back_match = re.search(r'BACK:\s*(\d+)', answer)
    slab_json_match = re.search(r'SLAB_TEXT:\s*(\{.*\})', answer, re.DOTALL)

    back_type = 'NONE'
    if 'SLAB_BACK' in answer:
        back_type = 'SLAB_BACK'
    elif 'COIN_REVERSE' in answer:
        back_type = 'COIN_REVERSE'

    if front_match and back_match and front_match.group(1) == back_match.group(1):
        back_match = None
        back_type = 'NONE'

    # 6. 画像をリサイズして保存用に準備
    front_img_data = None
    back_img_data = None

    if front_match:
        fidx = int(front_match.group(1))
        if fidx in images_raw:
            img = Image.open(BytesIO(images_raw[fidx]['data']))
            if img.size[0] > 800:
                img = img.resize((800, int(img.size[1] * 800 / img.size[0])), Image.LANCZOS)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            front_img_data = buf.getvalue()

    if back_match:
        bidx = int(back_match.group(1))
        if bidx in images_raw:
            img = Image.open(BytesIO(images_raw[bidx]['data']))
            if img.size[0] > 800:
                img = img.resize((800, int(img.size[1] * 800 / img.size[0])), Image.LANCZOS)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            back_img_data = buf.getvalue()

    # 7. スラブテキストをパース
    slab_text = {}
    if slab_json_match:
        try:
            slab_text = json.loads(slab_json_match.group(1))
        except:
            slab_text = {'raw': slab_json_match.group(1)}

    # front/back画像のURL取得
    front_url = None
    back_url = None
    if front_match:
        fidx = int(front_match.group(1))
        if fidx < len(originals):
            front_url = originals[fidx]
    if back_match:
        bidx = int(back_match.group(1))
        if bidx < len(originals):
            back_url = originals[bidx]

    return {
        'id': rec_id,
        'status': 'success',
        'front_img': front_img_data,
        'back_img': back_img_data,
        'front_img_url': front_url,
        'back_img_url': back_url,
        'back_type': back_type,
        'slab_text': slab_text,
        'auction_info': info,
        'image_count': len(originals),
        'raw_answer': answer,
        'originals': originals,
    }


def write_to_supabase(batch, db_client, retry=3):
    """100件バッチでcoin_slab_dataにupsert"""
    for attempt in range(retry):
        try:
            db_client.table('coin_slab_data').upsert(
                batch, on_conflict='market_transaction_id'
            ).execute()
            return True
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2)
            else:
                print(f'  DB書込み失敗(3回): {str(e)[:50]}')
                return False
    return False


def get_processed_ids(db_client):
    """処理済みIDをcoin_slab_dataから取得"""
    processed = set()
    last_id = ''
    while True:
        qb = (db_client.table('coin_slab_data')
            .select('market_transaction_id')
            .order('market_transaction_id')
            .limit(1000))
        if last_id:
            qb = qb.gt('market_transaction_id', last_id)
        resp = qb.execute()
        if not resp.data:
            break
        for r in resp.data:
            processed.add(r['market_transaction_id'])
        last_id = resp.data[-1]['market_transaction_id']
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-date', type=str, default='2025-01-01')
    parser.add_argument('--end-date', type=str, default=None)
    parser.add_argument('--min-price', type=int, default=0)
    args = parser.parse_args()

    global db
    db_client = get_client()

    # 処理済みIDを取得（再起動時のスキップ用）
    processed_ids = get_processed_ids(db_client)
    print(f'処理済み: {len(processed_ids)}件')

    # 全対象レコード取得（カーソルベース）
    all_records = []
    last_id = ''
    while True:
        qb = (db_client.table('market_transactions')
            .select('id, title, price_jpy, sold_date, url, grader')
            .eq('source', 'yahoo')
            .gte('sold_date', args.start_date))
        if args.end_date:
            qb = qb.lte('sold_date', args.end_date)
        if args.min_price > 0:
            qb = qb.gte('price_jpy', args.min_price)
        qb = qb.order('id').limit(1000)
        if last_id:
            qb = qb.gt('id', last_id)
        resp = qb.execute()
        if not resp.data:
            break
        all_records.extend(resp.data)
        last_id = resp.data[-1]['id']

    # 未処理のみフィルタ
    targets = [r for r in all_records if r['id'] not in processed_ids]
    print(f'全件: {len(all_records)} | 未処理: {len(targets)}件')

    stats = {'success': 0, 'html_fail': 0, 'no_next_data': 0,
             'no_images': 0, 'dl_fail': 0, 'api_fail': 0}
    start = time.time()
    batch = []
    backup = []
    total_processed = len(processed_ids)
    db_recreate_count = 0

    for i, rec in enumerate(targets, 1):
        elapsed = time.time() - start
        rate = i / elapsed * 60 if elapsed > 0 else 0
        print(f'[{total_processed + i}/{len(all_records)}] ({rate:.0f}/min) {rec["title"][:35]}', end=' ', flush=True)
        sys.stdout.flush()

        result = process_one(rec)
        status = result['status']
        stats[status] = stats.get(status, 0) + 1

        # coin_slab_data用のレコード作成
        slab = result.get('slab_text', {}) if isinstance(result.get('slab_text'), dict) else {}
        auction = result.get('auction_info', {}) if isinstance(result.get('auction_info'), dict) else {}
        originals_list = result.get('originals', [])

        if status == 'success' and slab.get('grader'):
            db_status = 'completed_hit'
        elif status == 'success':
            db_status = 'completed_no_hit'
        else:
            db_status = 'failed'

        # raw_slab_text: スラブ文字を一切加工せず結合
        raw_parts = []
        for key in ['line1', 'line2', 'line3', 'grade', 'cert_number', 'label', 'label_type']:
            val = slab.get(key, '')
            if val and val not in raw_parts:
                raw_parts.append(val)
        raw_slab_text = ' | '.join(raw_parts) if raw_parts else ''

        # coin_id = grader + "_" + raw_slab_text（cert_numberは除く）
        coin_id_parts = []
        for key in ['line1', 'line2', 'line3', 'grade', 'label', 'label_type']:
            val = slab.get(key, '')
            if val:
                coin_id_parts.append(val)
        coin_id_text = ' | '.join(coin_id_parts) if coin_id_parts else ''
        grader_val = slab.get('grader', '')
        coin_id = f'{grader_val}_{coin_id_text}' if grader_val and coin_id_text else ''

        # price_history: JSON配列
        price_entry = None
        if auction.get('price'):
            price_entry = json.dumps([{
                'price': auction.get('price'),
                'bids': auction.get('bids'),
                'init_price': auction.get('initPrice'),
                'date': rec.get('sold_date', ''),
            }])

        # VARCHAR制限に合わせてトリミング
        def trunc(val, maxlen):
            v = val or ''
            return v[:maxlen] if len(v) > maxlen else v

        row = {
            'market_transaction_id': rec['id'],
            'status': trunc(db_status, 20),
            'coin_id': coin_id,
            'raw_slab_text': raw_slab_text,
            'grader': trunc(grader_val, 10),
            'slab_line1': slab.get('line1', '') or slab.get('year', ''),
            'slab_line2': slab.get('line2', '') or slab.get('series', ''),
            'slab_line3': slab.get('line3', '') or slab.get('denomination', ''),
            'grade': trunc(slab.get('grade', ''), 30),
            'cert_number': trunc(slab.get('cert_number', ''), 30),
            'label_type': trunc(slab.get('label', '') or slab.get('label_type', ''), 30),
            'material': trunc(slab.get('material', '') or slab.get('material_hint', ''), 20),
            'front_img_url': result.get('front_img_url'),
            'back_img_url': result.get('back_img_url'),
            'back_type': trunc(result.get('back_type', 'NONE'), 20),
            'answer_raw': result.get('raw_answer', '')[:500] if result.get('raw_answer') else '',
            'price_jpy': auction.get('price'),
            'bids': auction.get('bids'),
            'init_price': auction.get('initPrice'),
            'price_history': price_entry,
        }
        batch.append(row)
        backup.append(row)

        if status == 'success':
            print(f'-> OK | {slab.get("grader","")} {slab.get("grade","")}')
        else:
            print(f'-> {status}')

        # 100件バッチでDB書き込み
        if len(batch) >= 100:
            write_to_supabase(batch, db_client)
            batch = []

        # 500件ごとにDB接続再生成
        if i % 500 == 0:
            db_recreate_count += 1
            db_client = get_client()
            print(f'  [DB再接続 #{db_recreate_count}]')

        # ローカルバックアップ（500件ごと）
        if i % 500 == 0 or i == len(targets):
            with open('data/phase1_backup.json', 'w', encoding='utf-8') as f:
                json.dump({
                    'stats': stats,
                    'total_processed': total_processed + i,
                    'backup_count': len(backup),
                }, f, ensure_ascii=False, indent=2)

        time.sleep(0.2)

    # 残りバッチを書き込み
    if batch:
        write_to_supabase(batch, db_client)

    elapsed = time.time() - start
    print(f'\n=== 完了 ===')
    print(f'処理: {len(targets)}件 / {elapsed:.0f}秒 ({elapsed/60:.1f}分)')
    print(f'成功: {stats["success"]} | HTMLエラー: {stats["html_fail"]} | '
          f'NEXT_DATAなし: {stats["no_next_data"]} | 画像なし: {stats["no_images"]} | '
          f'DL失敗: {stats["dl_fail"]} | API失敗: {stats["api_fail"]}')
    print(f'DB再接続: {db_recreate_count}回')


if __name__ == '__main__':
    main()
