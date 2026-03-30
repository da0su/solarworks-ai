"""eBayオークション検索 — DB完全一致 + eBay実価格確認 + ヤフオクライバル調査 + レポート出力"""
import sys, json, os, re, urllib.request, base64, urllib.parse, time
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)
from scripts.supabase_client import get_client


def get_ebay_token():
    app_id = os.environ['EBAY_APP_ID']
    cert_id = os.environ['EBAY_CERT_ID']
    credentials = base64.b64encode(f'{app_id}:{cert_id}'.encode()).decode()
    req = urllib.request.Request(
        'https://api.ebay.com/identity/v1/oauth2/token',
        data=b'grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope',
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {credentials}'}
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))['access_token']


def extract_year(text):
    m = re.search(r'(\d{4})', text or '')
    return m.group(1) if m else None


def extract_grade_parts(grade_str):
    m = re.match(r'(MS|PF|PR|AU|VF|XF|EF|SP|GEM)\s*(\d+)(\+?)', (grade_str or '').upper())
    if m:
        gt = m.group(1).replace('PR', 'PF')
        return gt, m.group(2), m.group(3)
    return None, None, ''


# ── 国名マッピング ──
COUNTRY_MAP = {
    'G.BRITAIN': ['britain', 'great britain', 'british', 'uk', 'england'],
    'G BRITAIN': ['britain', 'great britain', 'british', 'uk', 'england'],
    'GT BRITAIN': ['britain', 'great britain', 'british', 'uk', 'england'],
    'GREAT BRITAIN': ['britain', 'great britain', 'british', 'uk', 'england'],
    'SWITZERLAND': ['switzerland', 'swiss', 'helvetia'],
    'SWITZERLND': ['switzerland', 'swiss', 'helvetia'],
    'FRANCE': ['france', 'french'],
    'AUSTRALIA': ['australia', 'australian'],
    'JAPAN': ['japan', 'japanese'],
    'ITALY': ['italy', 'italian'],
    'GERMANY': ['germany', 'german', 'prussia', 'prussian', 'bavaria', 'bavarian'],
    'SPAIN': ['spain', 'spanish'],
    'CANADA': ['canada', 'canadian'],
    'CHINA': ['china', 'chinese', 'panda'],
    'MEXICO': ['mexico', 'mexican'],
    'SOUTH AFRICA': ['south africa', 'krugerrand'],
    'S. AFRICA': ['south africa', 'krugerrand'],
    'S AFRICA': ['south africa', 'krugerrand'],
    'AUSTRIA': ['austria', 'austrian'],
    'HUNGARY': ['hungary', 'hungarian'],
    'RUSSIA': ['russia', 'russian'],
    'NETHERLANDS': ['netherlands', 'dutch', 'holland'],
    'BELGIUM': ['belgium', 'belgian'],
    'COLOMBIA': ['colombia', 'colombian'],
    'INDIA': ['india', 'indian'],
    'PERU': ['peru', 'peruvian'],
    'PHILIPPINES': ['philippines', 'filipino'],
    'HONG KONG': ['hong kong'],
    'NEW ZEALAND': ['new zealand'],
    'GIBRALTAR': ['gibraltar'],
    'COOK ISLANDS': ['cook island'],
    'COOK IS': ['cook island'],
    'NIUE': ['niue'],
    'PALAU': ['palau'],
    'TUVALU': ['tuvalu'],
    'TONGA': ['tonga'],
    'FIJI': ['fiji'],
    'LIBERIA': ['liberia'],
    'SOMALIA': ['somalia'],
    'ISLE OF MAN': ['isle of man'],
    'BERMUDA': ['bermuda'],
    'TURKEY': ['turkey', 'turkish', 'ottoman'],
    'POLAND': ['poland', 'polish'],
    'CZECH': ['czech'],
    'NORWAY': ['norway', 'norwegian'],
    'SWEDEN': ['sweden', 'swedish'],
    'DENMARK': ['denmark', 'danish'],
    'EGYPT': ['egypt', 'egyptian'],
    'BRAZIL': ['brazil', 'brazilian'],
    'CHILE': ['chile', 'chilean'],
    'COSTA RICA': ['costa rica'],
    'CUBA': ['cuba', 'cuban'],
    'IRAN': ['iran', 'iranian', 'persia', 'persian'],
    'ISRAEL': ['israel', 'israeli'],
    'KOREA': ['korea', 'korean'],
    'THAILAND': ['thailand', 'thai'],
    'SINGAPORE': ['singapore'],
    'SIERRA LEONE': ['sierra leone'],
    'MONGOLIA': ['mongolia', 'mongolian'],
    'CHAD': ['chad'],
    'GHANA': ['ghana'],
    'CAMEROON': ['cameroon'],
    'CONGO': ['congo'],
}

# US判定: slab_line1に国名なし + $表記、またはUSA/UNITED STATES
US_PATTERNS_LINE1 = ['USA', 'UNITED STATES', 'AMERICA']


def extract_country(line1):
    """slab_line1から国名を抽出し、eBay照合用キーワードリストを返す"""
    up = (line1 or '').upper()

    # 長い国名から先にチェック
    sorted_keys = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in up:
            return COUNTRY_MAP[key]

    # US判定: USA/AMERICA等
    for pat in US_PATTERNS_LINE1:
        if pat in up:
            return ['american', 'united states', 'u.s.', 'us ', 'usa', 'america']

    # $表記かつG$/S$/Pt$プレフィックス → US coin (Eagle系)
    # ただし他国コインが$表記を使う場合もある (Cook Islands $20等) ので
    # 国名が既に見つかっている場合はここに到達しない
    if re.search(r'[GSP]?\$\d+', up):
        return ['american', 'united states', 'u.s.', 'us ', 'usa', 'america']

    return None  # 不明 — マッチ不可


# ── コインタイプ/額面マッピング ──
def extract_coin_type_checks(line1):
    """slab_line1から額面・コインタイプを抽出し、eBayタイトルに必要なキーワードセットを返す。
    返値: list of (any_of_keywords, regex_patterns, label)
      - any_of_keywords: 文字列リスト（1つでもあればOK）
      - regex_patterns: 正規表現リスト（1つでもマッチすればOK）
      - any_of_keywords と regex_patterns のいずれかに1つでもヒットすればパス
    """
    up = (line1 or '').upper()
    checks = []

    # ── Sovereign系 ──
    if '5 SOV' in up or '5SOV' in up:
        checks.append((['5 sovereign', '5 pound', '5 sov'], [], 'sovereign_5'))
    elif '2 SOV' in up or '2SOV' in up:
        checks.append((['2 sovereign', 'double sovereign', '2 sov'], [], 'sovereign_2'))
    elif '1/2 SOV' in up or '1/2SOV' in up or 'HALF SOV' in up:
        checks.append((['half sovereign', '1/2 sovereign', '1/2 sov', 'half sov'], [], 'sovereign_half'))
    elif '1 SOV' in up or '1SOV' in up:
        checks.append((['sovereign', 'sov'], [], 'sovereign_1'))
    elif 'SOV' in up:
        checks.append((['sovereign', 'sov'], [], 'sovereign'))

    # ── Franc系 ──
    m_fr = re.search(r'G?(\d+)\s*F\b', up)
    if m_fr:
        denom = m_fr.group(1)
        checks.append(([f'{denom} franc', f'{denom} fr'], [rf'{denom}f\b'], f'franc_{denom}'))

    # ── Dollar/Eagle系 (G$ = Gold, S$ = Silver, Pt$ = Platinum) ──
    m_dollar = re.search(r'(G|S|PT)?\$(\d+)', up, re.IGNORECASE)
    if m_dollar:
        prefix = (m_dollar.group(1) or '').upper()
        denom = m_dollar.group(2)
        # $5が$50にマッチしないよう正規表現で厳密照合
        checks.append((
            [f'{denom} dollar'],
            [rf'\${denom}(?!\d)'],
            f'dollar_{denom}'
        ))

        if prefix == 'S':
            checks.append((['silver eagle', 'silver dollar', 'silver'], [], 'silver_type'))
        elif prefix == 'G':
            checks.append((['gold eagle', 'gold'], [], 'gold_type'))
        elif prefix == 'PT':
            checks.append((['platinum eagle', 'platinum'], [], 'platinum_type'))

    # ── Eagle (standalone) ──
    if 'EAGLE' in up and not m_dollar:
        checks.append((['eagle'], [], 'eagle'))

    # ── Crown ──
    if 'CROWN' in up:
        checks.append((['crown'], [], 'crown'))

    # ── Yen ──
    m_yen = re.search(r'(\d+)\s*Y\b', up)
    if m_yen:
        denom = m_yen.group(1)
        checks.append(([f'{denom} yen', 'yen'], [rf'{denom}y\b'], f'yen_{denom}'))

    # ── Mark ──
    m_mark = re.search(r'(\d+)\s*M\b', up)
    if m_mark:
        pos = up.index(m_mark.group(0))
        after = up[pos:pos+len(m_mark.group(0))+2]
        if 'MS' not in after:
            denom = m_mark.group(1)
            checks.append(([f'{denom} mark', 'mark'], [rf'{denom}m\b'], f'mark_{denom}'))

    # ── Ducat ──
    if 'DUCAT' in up:
        checks.append((['ducat'], [], 'ducat'))

    # ── Panda ──
    if 'PANDA' in up:
        checks.append((['panda'], [], 'panda'))

    # ── Weight/Size (1oz, 1/4oz, 1/10oz等) ──
    m_weight = re.search(r'(\d+/\d+)\s*OZ', up)
    if m_weight:
        weight = m_weight.group(1)
        checks.append(([f'{weight}oz', f'{weight} oz'], [rf'{re.escape(weight)}\s*oz'], f'weight_{weight}'))
    else:
        m_weight_int = re.search(r'(\d+)\s*OZ', up)
        if m_weight_int:
            weight = m_weight_int.group(1)
            checks.append(([f'{weight}oz', f'{weight} oz'], [rf'\b{weight}\s*oz'], f'weight_{weight}'))

    # ── Maple ──
    if 'MAPLE' in up:
        checks.append((['maple'], [], 'maple'))

    # ── Krugerrand ──
    if 'KRUGER' in up:
        checks.append((['krugerrand', 'kruger'], [], 'krugerrand'))

    # ── Britannia ──
    if 'BRITANNIA' in up:
        checks.append((['britannia'], [], 'britannia'))

    # ── Peso ──
    m_peso = re.search(r'(\d+)\s*P\b', up)
    if m_peso:
        denom = m_peso.group(1)
        if int(denom) <= 100:
            checks.append(([f'{denom} peso', 'peso'], [], f'peso_{denom}'))

    # ── Lira ──
    m_lira = re.search(r'(\d+)\s*L\b', up)
    if m_lira:
        denom = m_lira.group(1)
        checks.append(([f'{denom} lir', 'lir'], [], f'lira_{denom}'))

    # ── £ (Pound) ──
    m_pound = re.search(r'G?£(\d+)', up)
    if m_pound:
        denom = m_pound.group(1)
        checks.append(([f'{denom} pound', f'{denom} sovereign'], [rf'£{denom}(?!\d)'], f'pound_{denom}'))

    # ── POUND (word, no £ symbol) ──
    if not m_pound:
        m_pound_word = re.search(r'(\d+)\s*POUND', up)
        if m_pound_word:
            denom = m_pound_word.group(1)
            checks.append(([f'{denom} pound', f'{denom} sovereign'], [rf'£{denom}(?!\d)'], f'pound_{denom}'))

    # ── Buffalo ──
    if 'BUFFALO' in up:
        checks.append((['buffalo'], [], 'buffalo'))

    # ── Liberty ──
    if 'LIBERTY' in up and 'EAGLE' not in up:
        checks.append((['liberty'], [], 'liberty'))

    return checks


def check_material_conflict(db_material, ebay_title_lower):
    """DB素材とeBayタイトルの素材矛盾をチェック。矛盾あればTrue"""
    mat = (db_material or '').upper()

    # 除外しない表現 (gold label, gold hoard, gold shield 等はグレーディング用語)
    safe_gold_words = ['gold label', 'gold hoard', 'gold shield', 'gold standard', 'gold foil']
    safe_silver_words = ['silver label', 'silver hoard', 'silver shield']

    def has_material_word(material_word, title, safe_list):
        """タイトルにmaterial_wordが含まれるが、safe_list内の表現のみの場合はFalse"""
        if material_word not in title:
            return False
        # safe表現を除去した上で再チェック
        cleaned = title
        for safe in safe_list:
            cleaned = cleaned.replace(safe, '')
        return material_word in cleaned

    if mat == 'GOLD':
        if has_material_word('silver', ebay_title_lower, safe_silver_words):
            return True
        if has_material_word('platinum', ebay_title_lower, []):
            return True
    elif mat == 'SILVER':
        if has_material_word('gold', ebay_title_lower, safe_gold_words):
            return True
        if has_material_word('platinum', ebay_title_lower, []):
            return True
    elif mat == 'PLATINUM':
        if has_material_word('silver', ebay_title_lower, safe_silver_words):
            return True
        if has_material_word('gold', ebay_title_lower, safe_gold_words):
            return True

    return False


def strict_match(ebay_title, db_coin):
    tl = ebay_title.upper()
    tl_lower = ebay_title.lower()
    grader = db_coin['grader'].upper()
    line1 = (db_coin['slab_line1'] or '').upper()

    # 1. grader
    if grader not in tl:
        return False

    # 2. year
    year = extract_year(db_coin['slab_line1'])
    if not year or year not in ebay_title:
        return False

    # 3. grade
    gt, gn, plus = extract_grade_parts(db_coin['grade'])
    if not gt or not gn:
        return False

    if plus:
        patterns = [rf'{gt}\s*{gn}\+', rf'{gt}-{gn}\+']
    else:
        patterns = [rf'{gt}\s*{gn}(?!\d|\+)', rf'{gt}-{gn}(?!\d|\+)']
    if gt == 'PF':
        if plus:
            patterns += [rf'PR\s*{gn}\+', rf'PR-{gn}\+']
        else:
            patterns += [rf'PR\s*{gn}(?!\d|\+)', rf'PR-{gn}(?!\d|\+)']

    if not any(re.search(p, tl) for p in patterns):
        return False

    # 4. ULTRA CAMEO
    db_grade_up = (db_coin['grade'] or '').upper()
    if 'ULTRA CAMEO' in db_grade_up:
        if not ('ULTRA CAMEO' in tl or 'UCAM' in tl or ' UC ' in tl or tl.endswith('UC')):
            return False

    # 5. 国名チェック（新規）
    country_keywords = extract_country(line1)
    if country_keywords is None:
        return False  # 国名不明はマッチ不可
    if not any(kw in tl_lower for kw in country_keywords):
        return False

    # 6. コインタイプ/額面チェック（新規）
    type_checks = extract_coin_type_checks(line1)
    for any_of_keywords, regex_patterns, label in type_checks:
        str_hit = any(kw in tl_lower for kw in any_of_keywords)
        regex_hit = any(re.search(p, tl_lower) for p in regex_patterns) if regex_patterns else False
        if not str_hit and not regex_hit:
            return False

    # 7. Sovereign額面の逆方向チェック
    if 'sovereign_1' in [lbl for _, _, lbl in type_checks]:
        if any(x in tl_lower for x in ['half sovereign', '1/2 sovereign', 'half sov',
                                         'double sovereign', '2 sovereign', '5 sovereign', '5 pound']):
            return False

    # 8. 素材矛盾チェック（新規）
    if check_material_conflict(db_coin.get('material'), tl_lower):
        return False

    # 9. slab_line2のシリーズ名チェック
    #    line2に固有のシリーズ名がある場合、eBayタイトルにもそれが含まれる必要がある
    line2 = (db_coin.get('slab_line2') or '').strip()
    if line2:
        line2_up = line2.upper()
        # 一般的なグレーディング/共通用語は除外
        skip_words = {'FIRST', 'DAY', 'ISSUE', 'EARLY', 'RELEASES', 'RELEASE', 'STRIKE',
                      'FIRST DAY', 'FIRST STRIKE', 'DCAM', 'CAMEO', 'ULTRA', 'DEEP',
                      'FINE', 'GOLD', 'SILVER', 'OZ', 'AG', 'AU', 'PT',
                      'HIGH RELIEF', 'COLORIZED', 'PROOF', 'BU', 'UNC',
                      'STARS', 'EDGE', 'ON', 'THE', 'OF', 'AND', 'WITH'}
        # 有意なワードを抽出
        words = [w for w in re.findall(r'[A-Z]+', line2_up) if w not in skip_words and len(w) >= 3]
        # シリーズ名が3文字以上の固有名詞を含む場合、少なくとも1つはマッチ必要
        if words and len(words) <= 5:  # シリーズ名が長すぎる場合はスキップ
            series_hit = any(w.lower() in tl_lower for w in words)
            if not series_hit:
                return False

    return True


# ── Step 2: eBayページから実価格を取得 ──

def fetch_ebay_page_price(ebay_url):
    """eBay出品ページのHTMLから実際の価格と鑑定会社を抽出する。
    返値: dict with 'page_price_usd', 'page_grader', 'error'
    """
    result = {'page_price_usd': None, 'page_grader': None, 'error': None}
    try:
        req = urllib.request.Request(ebay_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')

        # 価格抽出: eBayのオークション現在価格
        # パターン1: "prcIsum" / "prcIsum_bidPrice" id付き要素
        price_patterns = [
            r'(?:US\s+)?\$\s*([\d,]+\.?\d*)',  # US $123.45
            r'"price":\s*"?([\d,]+\.?\d*)"?',   # JSON-LD
            r'"convertedCurrentPrice":\s*\{"value"\s*:\s*"([\d.]+)"',  # API-like
        ]
        for pat in price_patterns:
            m = re.search(pat, html)
            if m:
                price_str = m.group(1).replace(',', '')
                try:
                    result['page_price_usd'] = float(price_str)
                    break
                except ValueError:
                    pass

        # 鑑定会社確認
        if 'NGC' in html.upper()[:5000]:
            result['page_grader'] = 'NGC'
        if 'PCGS' in html.upper()[:5000]:
            result['page_grader'] = 'PCGS'

    except Exception as e:
        result['error'] = str(e)
    return result


# ── Step 3: ヤフオクライバル価格を取得 ──

def search_yahoo_auctions(search_text, closed=False):
    """ヤフオクで検索し、出品中 or 落札済みの価格リストを返す。
    返値: list of {'title', 'price_jpy', 'bids', 'url'}
    """
    results = []
    try:
        query = urllib.parse.quote(search_text)

        if closed:
            url = f'https://auctions.yahoo.co.jp/closedsearch/closedsearch?p={query}&va={query}&b=1&n=20'
        else:
            url = f'https://auctions.yahoo.co.jp/search/search?p={query}&va={query}&b=1&n=20'

        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')

        if closed:
            # 落札済み: __NEXT_DATA__ JSON から抽出
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                # items は深い階層にある: props.pageProps.*.*.items
                items_list = _find_items_in_json(data)
                for item in items_list:
                    price = item.get('price')
                    title = item.get('title', '')
                    auction_id = item.get('auctionId', '')
                    bid_count = item.get('bidCount', 0)
                    if price and price > 0:
                        item_url = f'https://page.auctions.yahoo.co.jp/jp/auction/{auction_id}'
                        results.append({
                            'title': title,
                            'price_jpy': int(price),
                            'bids': int(bid_count) if bid_count else 0,
                            'url': item_url,
                        })
        else:
            # 出品中: 従来のHTML解析
            title_pattern = r'class="Product__titleLink[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
            titles = re.findall(title_pattern, html, re.DOTALL)

            price_pattern = r'class="Product__priceValue[^"]*"[^>]*>(.*?)</span>'
            prices = re.findall(price_pattern, html, re.DOTALL)

            bid_pattern = r'class="Product__bid[^"]*"[^>]*>(\d+)'
            bids = re.findall(bid_pattern, html)

            for i, (item_url, title) in enumerate(titles):
                price_jpy = None
                if i < len(prices):
                    price_str = re.sub(r'[^\d]', '', prices[i])
                    if price_str:
                        price_jpy = int(price_str)

                bid_count = 0
                if i < len(bids):
                    bid_count = int(bids[i])

                if price_jpy and price_jpy > 0:
                    results.append({
                        'title': title.strip(),
                        'price_jpy': price_jpy,
                        'bids': bid_count,
                        'url': item_url if item_url.startswith('http') else f'https://page.auctions.yahoo.co.jp{item_url}',
                    })

    except Exception as e:
        pass  # ヤフオク取得失敗は静かに処理

    return results


def _find_items_in_json(obj, depth=0):
    """__NEXT_DATA__ JSONから items リストを再帰的に探索"""
    if depth > 8:
        return []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'items' and isinstance(v, list) and v and isinstance(v[0], dict) and 'auctionId' in v[0]:
                return v
            result = _find_items_in_json(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_items_in_json(item, depth + 1)
            if result:
                return result
    return []


def build_yahoo_search_queries(match):
    """マッチ情報からヤフオク検索クエリを複数パターン生成。
    ヤフオクは日本語メインだが、コイン鑑定品は英語表記で出品されることも多い。
    複数クエリを試して最初にヒットしたものを使う。
    返値: list of str
    """
    grader = match['db_grader']
    line1 = match['db_line1'] or ''
    grade = match['db_grade'] or ''

    year = extract_year(line1) or ''

    # グレード短縮形
    grade_short = ''
    gm = re.match(r'(MS|PF|PR|AU|VF|XF|EF|SP)\s*(\d+)', grade.upper())
    if gm:
        grade_short = f'{gm.group(1)}{gm.group(2)}'

    # eBayタイトルからもキーワード抽出（実際にマッチしたタイトル）
    ebay_title = match.get('ebay_title', '')

    queries = []

    # パターン1: 鑑定会社 + 年 + グレード（最もシンプル。スラブ品はこれでヒットしやすい）
    if year and grade_short:
        queries.append(f'{grader} {year} {grade_short}')

    # パターン2: 鑑定会社 + コインの特徴的キーワード
    # eBayタイトルから主要な固有名詞を抽出
    title_words = ebay_title.split()
    # 共通語を除外して固有名詞を残す
    stop_words = {'NGC', 'PCGS', 'Gold', 'Silver', 'Platinum', 'Coin', 'Graded',
                  'The', 'Of', 'And', 'In', 'On', 'A', 'An', 'With', 'For',
                  'First', 'Day', 'Issue', 'Early', 'Releases', 'Release', 'Strike',
                  'Label', 'Hoard', 'Edge', 'oz', '1oz', '2oz', '3oz', '5oz',
                  'BU', 'UNC', 'Proof', 'PR70', 'MS70', 'MS69', 'PR69', 'PF70', 'PF69'}
    key_words = [w for w in title_words
                 if w not in stop_words and len(w) >= 3 and not re.match(r'^[\d.,$]+$', w)]

    if key_words and year:
        # 最大3つのキーワードを使用
        kw_str = ' '.join(key_words[:3])
        queries.append(f'{grader} {year} {kw_str}')

    # パターン3: コインタイプ特化（Sovereign, Franc等）
    line1_up = line1.upper()
    coin_type = ''
    if 'SOV' in line1_up:
        coin_type = 'ソブリン'
    elif re.search(r'\d+\s*F\b', line1_up):
        m_fr = re.search(r'(\d+)\s*F\b', line1_up)
        if m_fr:
            coin_type = f'{m_fr.group(1)}フラン'
    elif 'MARK' in line1_up or re.search(r'\d+M\b', line1_up):
        coin_type = 'マルク'
    elif 'EAGLE' in line1_up:
        coin_type = 'イーグル'
    elif 'PANDA' in line1_up:
        coin_type = 'パンダ'
    elif 'MAPLE' in line1_up:
        coin_type = 'メイプル'
    elif 'KRUGER' in line1_up:
        coin_type = 'クルーガーランド'
    elif 'BRITANNIA' in line1_up:
        coin_type = 'ブリタニア'

    if coin_type and year:
        queries.append(f'{grader} {year} {coin_type}')

    # パターン4: 超シンプル（鑑定会社 + 年のみ — 最後の手段）
    if year:
        queries.append(f'{grader} {year}')

    return queries


# ── Step 4: 判定ロジック ──

def judge_opportunity(match, yahoo_active, yahoo_closed, usd_jpy):
    """仕入判定を行い、(judgment, reason) を返す。"""
    buy_limit_jpy = match['ebay_limit_jpy']

    # ヤフオクに出品中のライバルがいるか
    if yahoo_active:
        # NG-3修正: 仕入上限の5%以下は開始入札価格とみなして除外
        _floor = buy_limit_jpy * 0.05
        _filtered_active = [r for r in yahoo_active if r['price_jpy'] >= _floor] or yahoo_active
        cheapest_active = min(r['price_jpy'] for r in _filtered_active)
        # ライバルが仕入上限より安い → 利益出ない
        if cheapest_active < buy_limit_jpy:
            return 'NG', f'ライバルが{cheapest_active:,}円で出品中。仕入上限{buy_limit_jpy:,}円を下回り利益出ない'

        # ライバルの最安値が仕入上限の0.9倍以上 かつ 入札あり → 売れる市場
        has_bids = any(r['bids'] > 0 for r in yahoo_active)
        if cheapest_active >= buy_limit_jpy * 0.9 and has_bids:
            return 'OK', f'ライバル{cheapest_active:,}円に入札あり。市場あり'

        # ライバルは高いが入札なし → 売れ残り
        if not has_bids:
            return 'NG', f'ライバルが{cheapest_active:,}円で売れ残り。仕入れ非推奨'

        return 'REVIEW', f'ライバル{cheapest_active:,}円。入札状況を要確認'
    elif yahoo_closed:
        # 落札済みのみある場合
        # NG-2修正: ref2_yahoo_price_jpy の1/10以下は誤認識落札として除外
        ref_price = match.get('ref2_yahoo_price_jpy') or 0
        _closed_valid = [r for r in yahoo_closed if ref_price <= 0 or r['price_jpy'] >= ref_price * 0.1] or yahoo_closed
        avg_closed = sum(r['price_jpy'] for r in _closed_valid) / len(_closed_valid)
        if avg_closed >= buy_limit_jpy * 1.2:
            return 'OK', f'直近落札平均{avg_closed:,.0f}円。仕入上限{buy_limit_jpy:,}円の1.2倍超で市場良好'
        elif avg_closed >= buy_limit_jpy:
            return 'REVIEW', f'直近落札平均{avg_closed:,.0f}円。利益薄い可能性、CEO判断'
        else:
            return 'NG', f'直近落札平均{avg_closed:,.0f}円。仕入上限を下回りNG'
    else:
        return 'CEO判断', 'ライバル情報なし。判断材料不足'


def main():
    db = get_client()
    token = get_ebay_token()

    dr = db.table('daily_rates').select('usd_jpy_calc').order('rate_date', desc=True).limit(1).execute()
    usd_jpy = float(dr.data[0]['usd_jpy_calc'])

    # DB全件取得（ref2_yahoo_price_jpyも取得）
    all_coins = []
    offset = 0
    while True:
        r = db.table('coin_slab_data').select(
            'id,management_no,grader,slab_line1,slab_line2,grade,material,ref1_buy_limit_jpy,ref2_yahoo_price_jpy'
        ).eq('status', 'completed_hit').gt('ref1_buy_limit_jpy', 0).order('id').range(offset, offset + 999).execute()
        if not r.data:
            break
        all_coins.extend(r.data)
        offset += len(r.data)
        if len(r.data) < 1000:
            break

    for c in all_coins:
        c['ebay_limit_usd'] = round(c['ref1_buy_limit_jpy'] / usd_jpy)

    # インデックス
    coin_index = defaultdict(list)
    for c in all_coins:
        year = extract_year(c.get('slab_line1', ''))
        grader = (c.get('grader', '') or '').upper()
        if year and grader:
            coin_index[(grader, year)].append(c)

    print(f'DB: {len(all_coins)}件, USD/JPY: {usd_jpy}')

    # ══════════════════════════════════════════
    # Step 1: eBay API検索 → DB完全一致マッチング
    #   4クエリ × 最大5ページ（200件/ページ）= 最大4000件
    # ══════════════════════════════════════════
    print('\n=== Step 1: eBay API検索 + DBマッチング ===')

    queries = [
        'NGC Gold',
        'NGC Silver',
        'PCGS Gold',
        'PCGS Silver',
    ]

    ITEMS_PER_PAGE = 200
    MAX_PAGES = 5

    all_matches = []
    seen_urls = set()
    seen_mgmt_nos = set()  # mgmt_no重複防止: 同一コインが複数URLにマッチしても先着1件のみ

    for qi, aq in enumerate(queries):
        query_match = 0
        query_items_total = 0

        for page in range(MAX_PAGES):
            page_offset = page * ITEMS_PER_PAGE
            url = (
                f'https://api.ebay.com/buy/browse/v1/item_summary/search'
                f'?q={urllib.parse.quote(aq)}'
                f'&limit={ITEMS_PER_PAGE}'
                f'&offset={page_offset}'
                f'&filter=buyingOptions:{{AUCTION}},priceCurrency:USD,itemLocationCountry:{{US|GB}}'
                f'&category_ids=11116'
            )
            req2 = urllib.request.Request(url, headers={
                'Authorization': f'Bearer {token}',
                'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
            })
            try:
                result = json.loads(urllib.request.urlopen(req2, timeout=15).read().decode('utf-8'))
                items = result.get('itemSummaries', [])
                total_available = result.get('total', 0)
                query_items_total += len(items)

                for item in items:
                    ebay_url = item.get('itemWebUrl', '')
                    base_url = ebay_url.split('?')[0] if '?' in ebay_url else ebay_url
                    if base_url in seen_urls:
                        continue

                    title = item.get('title', '')
                    ebay_grader = 'NGC' if 'NGC' in title.upper() else ('PCGS' if 'PCGS' in title.upper() else None)
                    ebay_year = extract_year(title)
                    if not ebay_grader or not ebay_year:
                        continue

                    # API価格取得
                    # AUCTIONアイテムは price=None、currentBidPrice に現在入札額が入る
                    api_price_usd = None
                    price_raw = item.get('price') or item.get('currentBidPrice') or {}
                    if price_raw:
                        try:
                            api_price_usd = float(price_raw.get('value', 0)) or None
                        except (ValueError, TypeError):
                            pass

                    candidates = coin_index.get((ebay_grader, ebay_year), [])
                    for db_coin in candidates:
                        mgmt_no = db_coin['management_no']
                        if mgmt_no in seen_mgmt_nos:
                            continue  # 同一コインが別URLにも出品されている場合は先着1件のみ
                        if strict_match(title, db_coin):
                            seen_urls.add(base_url)
                            seen_mgmt_nos.add(mgmt_no)
                            all_matches.append({
                                'mgmt_no': mgmt_no,
                                'db_grader': db_coin['grader'],
                                'db_line1': db_coin['slab_line1'],
                                'db_line2': db_coin.get('slab_line2', ''),
                                'db_grade': db_coin['grade'],
                                'db_material': db_coin.get('material', ''),
                                'ebay_limit_usd': db_coin['ebay_limit_usd'],
                                'ebay_limit_jpy': db_coin['ref1_buy_limit_jpy'],
                                'ref2_yahoo_price_jpy': db_coin.get('ref2_yahoo_price_jpy'),
                                'ebay_title': title,
                                'api_price_usd': api_price_usd,
                                'bid_count': item.get('bidCount', 0),
                                'ebay_url': ebay_url,
                            })
                            query_match += 1
                            break

                print(f'  [{qi+1}/{len(queries)}] {aq} page{page+1}: {len(items)}件取得, {query_match}マッチ累計')

                # 次ページがなければ終了
                if len(items) < ITEMS_PER_PAGE or page_offset + len(items) >= total_available:
                    break

            except Exception as e:
                print(f'  [{qi+1}/{len(queries)}] {aq} page{page+1}: Error: {e}')
                break

            time.sleep(1)  # rate limit対策

        print(f'  -> {aq}: 合計{query_items_total}件取得, {query_match}マッチ')

    print(f'\nStep 1完了: {len(queries)}クエリ, マッチ: {len(all_matches)}件')

    if not all_matches:
        print('\nマッチなし。終了。')
        # マッチなしでも結果ファイルを保存（Slack連携用）
        _save_matches_json([], 0)
        return

    # ══════════════════════════════════════════
    # Step 2: ヤフオクライバル価格を確認
    #   （eBayページ取得は時間がかかるためスキップ。API価格+「要ブラウザ確認」で出力）
    # ══════════════════════════════════════════
    print(f'\n=== Step 2: ヤフオクライバル調査 ({len(all_matches)}件) ===')
    for i, m in enumerate(all_matches):
        search_queries = build_yahoo_search_queries(m)
        active = []
        closed = []

        for qi, query in enumerate(search_queries):
            print(f'  [{i+1}/{len(all_matches)}] {m["mgmt_no"]} クエリ{qi+1}: "{query}" ... ', end='', flush=True)

            # 出品中
            a = search_yahoo_auctions(query, closed=False)
            time.sleep(2)

            # 落札済み
            c = search_yahoo_auctions(query, closed=True)
            time.sleep(2)

            print(f'出品中{len(a)}件, 落札済{len(c)}件')

            if a:
                active.extend(a)
            if c:
                closed.extend(c)

            # ヒットしたら次のクエリは不要
            if a or c:
                break

        # 重複除去（URLベース）
        seen = set()
        deduped_active = []
        for item in active:
            if item['url'] not in seen:
                seen.add(item['url'])
                deduped_active.append(item)
        deduped_closed = []
        for item in closed:
            if item['url'] not in seen:
                seen.add(item['url'])
                deduped_closed.append(item)

        m['yahoo_active'] = deduped_active
        m['yahoo_closed'] = deduped_closed

    # ══════════════════════════════════════════
    # Step 3: レポート生成
    # ══════════════════════════════════════════
    print(f'\n=== Step 3: レポート生成 ===')

    report_lines = []
    report_lines.append(f'eBay仕入調査レポート')
    report_lines.append(f'生成日時: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    report_lines.append(f'USD/JPY: {usd_jpy}')
    report_lines.append(f'マッチ件数: {len(all_matches)}')
    report_lines.append('=' * 60)

    ok_count = 0
    ng_count = 0
    review_count = 0
    ceo_count = 0

    for m in all_matches:
        # 判定 ── 結果を match dict に書き戻す（Slack連携・daily_candidates挿入に使う）
        judgment, reason = judge_opportunity(
            m, m.get('yahoo_active', []), m.get('yahoo_closed', []), usd_jpy
        )
        m['judgment'] = judgment
        m['judgment_reason'] = reason

        if judgment == 'OK':
            ok_count += 1
        elif judgment == 'NG':
            ng_count += 1
        elif judgment == 'REVIEW':
            review_count += 1
        else:
            ceo_count += 1

        report_lines.append('')
        report_lines.append(f'=== 管理番号: {m["mgmt_no"]} ===')
        report_lines.append(f'DB: {m["db_grader"]} | {m["db_line1"]} | {m["db_grade"]}')
        report_lines.append(f'仕入上限: USD{m["ebay_limit_usd"]:,} ({m["ebay_limit_jpy"]:,}円)')
        if m.get('ref2_yahoo_price_jpy'):
            report_lines.append(f'DB参考ヤフオク価格: {m["ref2_yahoo_price_jpy"]:,}円')

        # eBay出品情報
        report_lines.append('')
        report_lines.append('[eBay出品]')
        report_lines.append(f'タイトル: {m["ebay_title"]}')

        if m.get('api_price_usd'):
            api_jpy = round(m['api_price_usd'] * usd_jpy)
            report_lines.append(f'出品価格: ${m["api_price_usd"]:.2f} ({api_jpy:,}円) ※API価格。要ブラウザ確認')
        else:
            report_lines.append(f'出品価格: 取得不可。要ブラウザ確認')

        report_lines.append(f'入札: {m["bid_count"]}件')
        report_lines.append(f'URL: {m["ebay_url"]}')

        # ヤフオクライバル情報
        report_lines.append('')
        report_lines.append('[ヤフオクライバル]')

        yahoo_active = m.get('yahoo_active', [])
        yahoo_closed = m.get('yahoo_closed', [])

        if yahoo_active:
            for j, ya in enumerate(yahoo_active[:3]):  # 上位3件
                bids_str = f'{ya["bids"]}件入札' if ya['bids'] > 0 else '入札なし'
                report_lines.append(f'出品中: {ya["price_jpy"]:,}円（{bids_str}）{ya["url"]}')
        else:
            report_lines.append('出品中: なし')

        if yahoo_closed:
            for j, yc in enumerate(yahoo_closed[:3]):  # 上位3件
                bids_str = f'{yc["bids"]}件入札' if yc['bids'] > 0 else ''
                report_lines.append(f'直近落札: {yc["price_jpy"]:,}円 {bids_str}')
        else:
            report_lines.append('直近落札: なし')

        # 判定
        report_lines.append('')
        report_lines.append(f'-> {reason}')
        report_lines.append(f'[判定] {judgment}')

    # サマリー
    report_lines.append('')
    report_lines.append('=' * 60)
    report_lines.append(f'サマリー: OK={ok_count}, REVIEW={review_count}, NG={ng_count}, CEO判断={ceo_count}')

    # 出力
    report_text = '\n'.join(report_lines)
    print('\n' + report_text)

    # ファイル保存
    report_path = Path(__file__).parent.parent / 'data' / 'sourcing_report.txt'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding='utf-8')
    print(f'\nレポート保存: {report_path}')

    # マッチ結果をJSON保存（Slack連携用）
    total_searched = sum(1 for _ in seen_urls) + len(all_matches)  # 概算
    _save_matches_json(all_matches, total_searched)


def _save_matches_json(all_matches, total_searched):
    """マッチ結果をJSONに保存し、前回結果との差分（新規候補）を判定する。
    保存先: coin_business/data/ebay_matches_latest.json
    前回比較: coin_business/data/ebay_matches_previous.json
    """
    data_dir = Path(__file__).parent.parent / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    latest_file = data_dir / 'ebay_matches_latest.json'
    previous_file = data_dir / 'ebay_matches_previous.json'

    # 前回結果を読み込み（比較用）
    previous_urls = set()
    if latest_file.exists():
        try:
            prev_data = json.loads(latest_file.read_text(encoding='utf-8'))
            for m in prev_data.get('matches', []):
                previous_urls.add(m.get('ebay_url', '').split('?')[0])
        except (json.JSONDecodeError, KeyError):
            pass
        # 現在のlatestをpreviousに退避
        import shutil
        shutil.copy2(str(latest_file), str(previous_file))

    # マッチ結果を整形
    matches_out = []
    new_count = 0
    for m in all_matches:
        base_url = m.get('ebay_url', '').split('?')[0]
        is_new = base_url not in previous_urls
        if is_new:
            new_count += 1
        matches_out.append({
            'mgmt_no': m.get('mgmt_no', ''),
            'db_grader': m.get('db_grader', ''),
            'db_line1': m.get('db_line1', ''),
            'db_line2': m.get('db_line2', ''),
            'db_grade': m.get('db_grade', ''),
            'db_material': m.get('db_material', ''),
            'ebay_limit_usd': m.get('ebay_limit_usd', 0),
            'ebay_limit_jpy': m.get('ebay_limit_jpy', 0),
            'ref2_yahoo_price_jpy': m.get('ref2_yahoo_price_jpy'),
            'ebay_title': m.get('ebay_title', ''),
            'api_price_usd': m.get('api_price_usd'),
            'bid_count': m.get('bid_count', 0),
            'ebay_url': m.get('ebay_url', ''),
            'is_new': is_new,
            'judgment': m.get('judgment', ''),          # OK/NG/REVIEW/CEO判断
            'judgment_reason': m.get('judgment_reason', ''),  # 判定根拠
        })

    result = {
        'searched_at': datetime.now(timezone.utc).isoformat(),
        'total_searched': total_searched,
        'match_count': len(matches_out),
        'new_count': new_count,
        'matches': matches_out,
    }

    # atomic write
    tmp_file = latest_file.with_suffix('.tmp')
    tmp_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp_file.replace(latest_file)
    print(f'マッチJSON保存: {latest_file} ({len(matches_out)}件, 新規{new_count}件)')


if __name__ == '__main__':
    main()
