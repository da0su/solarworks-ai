"""
eBay仕入れモニター
- DBのcoin_slab_dataからグループ検索クエリを生成
- eBay Browse APIで検索
- 完全一致マッチング（grader + year + grade_type + grade_number + denomination）
- 仕入上限以下の候補をSlack通知
"""
import sys, os, json, re, time, base64, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=True)
from scripts.supabase_client import get_client


# === 正規化テーブル ===
COUNTRY_MAP = {
    'g.britain': 'great britain', 'g.brit': 'great britain', 'gt britain': 'great britain',
    'g britain': 'great britain', 'gb': 'great britain',
    'u.s.': 'united states', 'u.s': 'united states',
}

DENOM_PATTERNS = {
    # Gold
    'sovereign': {'keywords': ['sovereign', 'sov'], 'variants': {
        '5 sov': ['5 sovereign', '5 pound', 'quintuple'],
        '2 sov': ['2 sovereign', 'double sovereign'],
        '1 sov': ['sovereign', '1 sovereign'],
        '1/2 sov': ['half sovereign', '1/2 sovereign', 'half sov'],
    }},
    'franc': {'keywords': ['franc', 'fr'], 'variants': {
        '100 fr': ['100 franc'],
        '50 fr': ['50 franc'],
        '40 fr': ['40 franc'],
        '20 fr': ['20 franc'],
        '10 fr': ['10 franc'],
        '5 fr': ['5 franc'],
    }},
    'dollar_us': {'keywords': ['$'], 'variants': {
        '$20': ['20 dollar', '$20', 'double eagle', '20 gold'],
        '$10': ['10 dollar', '$10', 'eagle', '10 gold'],
        '$5': ['5 dollar', '$5', 'half eagle', '5 gold'],
        '$2.5': ['2.5 dollar', '$2.5', 'quarter eagle', '2 1/2'],
        's$1': ['silver eagle', 's$1', 'silver dollar eagle'],
        '$1': ['1 dollar', '$1', 'morgan', 'peace dollar'],
    }},
    'mark': {'keywords': ['mark'], 'variants': {
        '20 mark': ['20 mark'],
        '10 mark': ['10 mark'],
    }},
    'eagle_gold': {'keywords': ['eagle', 'gold'], 'variants': {
        'g$50': ['gold eagle 1oz', '$50 gold eagle', 'g$50'],
        'g$25': ['gold eagle 1/2', '$25 gold eagle', 'g$25'],
        'g$10': ['gold eagle 1/4', '$10 gold eagle', 'g$10'],
        'g$5': ['gold eagle 1/10', '$5 gold eagle', 'g$5', 'maple leaf'],
    }},
}

GRADE_TYPE_ALIASES = {
    'pf': ['pf', 'pr', 'proof'],
    'ms': ['ms', 'mint state'],
    'au': ['au', 'about uncirculated'],
    'xf': ['xf', 'ef', 'extremely fine'],
    'vf': ['vf', 'very fine'],
    'sp': ['sp', 'specimen'],
}


def extract_grade(grade_str):
    """PF 70 ULTRA CAMEO → ('pf', '70', True)"""
    if not grade_str:
        return None, None, False
    m = re.match(r'(MS|PF|PR|AU|VF|XF|EF|SP|GEM)\s*(\d+)', grade_str, re.I)
    if m:
        gt = m.group(1).upper()
        if gt == 'PR': gt = 'PF'
        gn = m.group(2)
        uc = bool(re.search(r'ULTRA\s*CAMEO|UCAM|UC\b', grade_str, re.I))
        return gt.lower(), gn, uc
    return None, None, False


def extract_year(text):
    """テキストから4桁年号を抽出（1876M, 1882A等のミントマーク付きにも対応）"""
    m = re.search(r'(1[5-9]\d{2}|20[0-2]\d)', text or '')
    return m.group(1) if m else None


def normalize_title(title):
    """eBayタイトルを正規化"""
    t = title.lower()
    for abbr, full in COUNTRY_MAP.items():
        t = t.replace(abbr, full)
    return t


def get_denomination_key(slab_line1):
    """slab_line1から額面キーを抽出（例: '1 sov', '$20', '20 fr'）"""
    line = (slab_line1 or '').lower()

    # Sovereign系
    if '5 sov' in line or '5sov' in line:
        return '5_sov'
    if '2 sov' in line or '2sov' in line:
        return '2_sov'
    if '1/2 sov' in line or '1/2sov' in line or 'half' in line:
        return 'half_sov'
    if '1 sov' in line or '1sov' in line:
        return '1_sov'

    # Franc系
    m = re.search(r'(\d+)\s*f[r]?\b', line)
    if m:
        return f'{m.group(1)}_fr'

    # Dollar系
    if 's$1' in line or ('eagle' in line and 's$' in line):
        return 'silver_eagle'
    m = re.search(r'[g]?\$(\d+)', line)
    if m:
        return f'${m.group(1)}'

    # Mark系
    m = re.search(r'(\d+)\s*m(?:ark)?\b', line)
    if m and 'mark' in line:
        return f'{m.group(1)}_mark'

    # Crown
    if 'crown' in line:
        return 'crown'

    # 日本円
    if 'yen' in line or '1 yen' in line:
        return 'yen'

    # Zecchino
    if '1z' in line or 'zec' in line:
        return 'zecchino'

    return line.strip()[:30]


def check_denomination_match(db_denom_key, ebay_title_lower):
    """額面の完全一致チェック"""
    t = ebay_title_lower

    if db_denom_key == '5_sov':
        return ('5 sovereign' in t or '5 pound' in t or '5 sov' in t or 'quintuple' in t) and 'half' not in t
    if db_denom_key == '2_sov':
        return ('2 sovereign' in t or 'double sovereign' in t or '2 sov' in t) and 'half' not in t and '1/2' not in t
    if db_denom_key == '1_sov':
        return ('sovereign' in t or 'sov' in t) and 'half' not in t and '1/2' not in t and '5 sov' not in t and '2 sov' not in t and '5 pound' not in t and 'double' not in t
    if db_denom_key == 'half_sov':
        return ('half sovereign' in t or '1/2 sovereign' in t or 'half sov' in t or '1/2 sov' in t)

    if db_denom_key.endswith('_fr'):
        num = db_denom_key.split('_')[0]
        return (f'{num} franc' in t or f'{num}f' in t or f'{num} fr' in t)

    if db_denom_key == 'silver_eagle':
        return ('silver eagle' in t or ('eagle' in t and 'silver' in t)) and 'gold' not in t

    if db_denom_key.startswith('$'):
        num = db_denom_key[1:]
        return (f'${num}' in t or f'{num} dollar' in t)

    if db_denom_key.endswith('_mark'):
        num = db_denom_key.split('_')[0]
        return f'{num} mark' in t

    if db_denom_key == 'crown':
        return 'crown' in t

    if db_denom_key == 'yen':
        return 'yen' in t

    if db_denom_key == 'zecchino':
        return 'zecchino' in t or 'zecch' in t or 'ducat' in t

    return False


COUNTRY_EXTRACT = {
    'france': ['france', 'french'],
    'g.britain': ['great britain', 'britain', 'g.britain', 'gb', 'uk', 'united kingdom', 'england'],
    'australia': ['australia', 'australian'],
    'canada': ['canada', 'canadian'],
    'switzerland': ['switzerland', 'swiss', 'helvetia'],
    'belgium': ['belgium', 'belgian'],
    'italy': ['italy', 'italian'],
    'germany': ['germany', 'german', 'prussia', 'prussian'],
    'austria': ['austria', 'austrian'],
    'spain': ['spain', 'spanish'],
    'netherlands': ['netherlands', 'dutch', 'holland'],
    'india': ['india', 'indian'],
    'china': ['china', 'chinese'],
    'japan': ['japan', 'japanese'],
    'chile': ['chile', 'chilean'],
    'colombia': ['colombia', 'colombian'],
    'romania': ['romania', 'romanian'],
    'russia': ['russia', 'russian'],
    'portugal': ['portugal', 'portuguese'],
    'tunisia': ['tunisia', 'tunisian'],
    'luxembourg': ['luxembourg'],
    'alderney': ['alderney'],
    'st.helena': ['st. helena', 'saint helena', 'st helena'],
    'isle of man': ['isle of man'],
    'cook is': ['cook island', 'cook is'],
    'niue': ['niue'],
    'gibraltar': ['gibraltar'],
    'egypt': ['egypt', 'egyptian'],
    'south africa': ['south africa'],
    'mexico': ['mexico', 'mexican'],
    'burundi': ['burundi'],
    'cameroon': ['cameroon'],
    'palau': ['palau'],
    'papua new guinea': ['papua new guinea', 'png'],
    'ceylon': ['ceylon', 'sri lanka'],
    'korea': ['korea', 'korean'],
    'iraq': ['iraq'],
    'thailand': ['thailand', 'thai', 'siam'],
    'cambodia': ['cambodia'],
    'mongolia': ['mongolia'],
    'panama': ['panama'],
    'san marino': ['san marino'],
    'vatican': ['vatican', 'papal'],
    'venice': ['venice', 'venetian'],
    'israel': ['israel'],
    'ivory coast': ['ivory coast'],
}


def extract_country_from_slab(slab_line1, slab_line2=''):
    """slab_line1から国名を抽出"""
    text = f'{slab_line1} {slab_line2}'.lower()
    for key, aliases in COUNTRY_EXTRACT.items():
        for alias in aliases:
            if alias in text:
                return key
    return None


def check_country_match(db_country, ebay_title_lower):
    """国名の一致チェック"""
    if not db_country:
        return True  # DB側に国名がない場合はスキップ
    aliases = COUNTRY_EXTRACT.get(db_country, [db_country])
    return any(a in ebay_title_lower for a in aliases)


def check_exact_match(ebay_title, db_coin):
    """完全一致マッチング: grader + year + country + grade_type + grade_number + denomination"""
    tl = normalize_title(ebay_title)

    # 1. Grader完全一致
    grader = (db_coin['grader'] or '').lower()
    if grader not in tl:
        return False, "grader不一致"

    # 2. Year完全一致（必須）
    db_year = extract_year(db_coin.get('slab_line1', ''))
    if db_year:
        if db_year not in ebay_title:
            return False, f"year不一致: DB={db_year}"
    else:
        # 年号がDBにない場合、eBay側の年号とは比較できないのでスキップしない
        pass

    # 3. Country一致（国名がDBにある場合は必須）
    db_country = extract_country_from_slab(
        db_coin.get('slab_line1', ''), db_coin.get('slab_line2', ''))
    if db_country:
        if not check_country_match(db_country, tl):
            return False, f"country不一致: DB={db_country}"

    # 4. Grade type + number 完全一致
    db_gt, db_gn, db_uc = extract_grade(db_coin.get('grade', ''))
    if db_gt and db_gn:
        ebay_gt, ebay_gn, ebay_uc = None, None, False
        for pattern in [r'(MS|PF|PR|AU|VF|XF|EF|SP)\s*[-]?\s*(\d+)', r'(MS|PF|PR|AU|VF|XF|EF|SP)(\d+)']:
            m = re.search(pattern, ebay_title, re.I)
            if m:
                ebay_gt = m.group(1).upper()
                if ebay_gt == 'PR': ebay_gt = 'PF'
                ebay_gn = m.group(2)
                ebay_uc = bool(re.search(r'ULTRA\s*CAMEO|UCAM|\bUC\b', ebay_title, re.I))
                break

        if not ebay_gt:
            return False, "eBayグレード読取不可"

        if db_gt.lower() != ebay_gt.lower():
            return False, f"grade_type不一致: DB={db_gt} eBay={ebay_gt}"

        if db_gn != ebay_gn:
            return False, f"grade_number不一致: DB={db_gn} eBay={ebay_gn}"

    # 5. Denomination完全一致
    db_denom = get_denomination_key(db_coin.get('slab_line1', ''))
    if not check_denomination_match(db_denom, tl):
        return False, f"denomination不一致: DB={db_denom}"

    # 5.5 slab_line1のタイプキーワード一致（HIGH RELIEF, PIEFORT等）
    line1 = (db_coin.get('slab_line1', '') or '').upper()
    type_keywords = ['HIGH RELIEF', 'PIEFORT', 'RESTRIKE', 'PROOF', 'PATTERN', 'MULE']
    for kw in type_keywords:
        if kw in line1:
            if kw.lower() not in tl:
                return False, f"type不一致: DB has {kw}"

    # 6. slab_line2（シリーズ名・タイプ名）一致
    line2 = (db_coin.get('slab_line2', '') or '').strip()
    if line2 and line2.upper() not in ['', 'NONE']:
        # line2のキーワードをトークン化してeBayタイトルに含まれるか確認
        line2_tokens = [t.lower() for t in re.split(r'[\s\-/]+', line2) if len(t) >= 3]
        if line2_tokens:
            matched_tokens = sum(1 for tok in line2_tokens if tok in tl)
            match_ratio = matched_tokens / len(line2_tokens)
            if match_ratio < 0.5:
                return False, f"line2不一致: DB={line2} (match={match_ratio:.0%})"

    return True, "完全一致"


class EbayAPI:
    def __init__(self):
        self.app_id = os.environ['EBAY_APP_ID']
        self.cert_id = os.environ['EBAY_CERT_ID']
        self.token = None
        self.token_expiry = 0
        self.call_count = 0

    def get_token(self):
        if self.token and time.time() < self.token_expiry:
            return self.token
        credentials = base64.b64encode(f'{self.app_id}:{self.cert_id}'.encode()).decode()
        req = urllib.request.Request(
            'https://api.ebay.com/identity/v1/oauth2/token',
            data=b'grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope',
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {credentials}'}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
        self.token = resp['access_token']
        self.token_expiry = time.time() + resp.get('expires_in', 7200) - 60
        return self.token

    def search(self, query, max_price_usd, limit=50):
        if self.call_count >= 4500:
            print("API呼び出し上限に近づいています。停止。")
            return [], 0

        token = self.get_token()
        url = (
            f'https://api.ebay.com/buy/browse/v1/item_summary/search'
            f'?q={urllib.parse.quote(query)}'
            f'&limit={limit}'
            f'&filter=price:[10..{max_price_usd}],priceCurrency:USD,itemLocationCountry:{{US|GB}}'
            f'&category_ids=11116'
        )
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {token}',
            'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
        })
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))
            self.call_count += 1
            return resp.get('itemSummaries', []), resp.get('total', 0)
        except Exception as e:
            print(f"  API Error: {e}")
            return [], 0


def generate_search_queries(coins, usd_jpy):
    """DBコインから個別eBay検索クエリを生成（年号+額面+grader+grade）"""
    queries = []
    seen_queries = set()

    # coin_id単位でグループ化（同じコインの重複を排除）
    coin_by_id = {}
    for c in coins:
        cid = c.get('coin_id', '')
        if cid and (cid not in coin_by_id or c.get('ref1_buy_limit_jpy', 0) > coin_by_id[cid].get('ref1_buy_limit_jpy', 0)):
            coin_by_id[cid] = c

    # 仕入上限が高い順にソート
    sorted_coins = sorted(coin_by_id.values(), key=lambda x: x.get('ref1_buy_limit_jpy', 0), reverse=True)

    for c in sorted_coins:
        cost_limit_jpy = c.get('ref1_buy_limit_jpy', 0)
        ebay_limit_jpy = round((cost_limit_jpy - 2750) / 1.1)
        limit_usd = round(ebay_limit_jpy / usd_jpy)
        if limit_usd < 50:
            continue

        grader = c.get('grader', '')
        line1 = c.get('slab_line1', '')
        grade = c.get('grade', '')
        material = (c.get('material', '') or '').lower()

        year = extract_year(line1)
        if not year:
            continue

        gt, gn, uc = extract_grade(grade)
        if not gt or not gn:
            continue

        # 国名抽出
        country = extract_country_from_slab(line1, c.get('slab_line2', ''))
        country_search = ''
        if country:
            country_map_search = {
                'g.britain': 'Great Britain', 'france': 'France', 'australia': 'Australia',
                'switzerland': 'Switzerland', 'belgium': 'Belgium', 'italy': 'Italy',
                'germany': 'Germany', 'austria': 'Austria', 'spain': 'Spain',
                'canada': 'Canada', 'netherlands': 'Netherlands', 'chile': 'Chile',
                'colombia': 'Colombia', 'romania': 'Romania', 'portugal': 'Portugal',
                'india': 'India', 'china': 'China', 'japan': 'Japan',
            }
            country_search = country_map_search.get(country, '')

        # 額面
        denom = get_denomination_key(line1)
        denom_search = denom.replace('_', ' ')
        if 'sov' in denom_search:
            denom_search = denom_search.replace('sov', 'sovereign')
        if 'fr' in denom_search:
            denom_search = denom_search.replace('fr', 'francs')
        if denom_search.startswith('$'):
            pass  # そのまま

        grade_search = f'{gt.upper()}{gn}'

        # line2からシリーズ名を追加（HIGH RELIEF, WIRE RIM等）
        line2 = (c.get('slab_line2', '') or '').strip()
        line2_search = ''
        if line2 and line2.upper() not in ['NONE', '']:
            # 短いトークンや一般的な語を除外
            line2_tokens = [t for t in line2.split() if len(t) >= 3 and t.upper() not in ['THE', 'AND', 'FOR']]
            line2_search = ' '.join(line2_tokens[:3])  # 最大3語

        # line1のタイプキーワード
        line1_upper = line1.upper()
        type_kw = ''
        for kw in ['HIGH RELIEF', 'PIEFORT', 'RESTRIKE']:
            if kw in line1_upper:
                type_kw = kw
                break

        q = f'{year} {country_search} {denom_search} {type_kw} {line2_search} {grader} {grade_search}'.strip()
        # 特殊文字（£, €等）を除去
        q = re.sub(r'[£€¥]', '', q)
        q = re.sub(r'\s+', ' ', q)

        if q in seen_queries:
            continue
        seen_queries.add(q)

        queries.append({
            'query': q,
            'max_price_usd': limit_usd,
            'coins': [c],
            'group_key': q,
            'count': 1,
        })

    return queries


def send_slack(candidates, dry_run=False):
    """Slack通知"""
    token = os.environ.get('SLACK_BOT_TOKEN', '')
    channel = 'C0ALSAPMYHY'  # #ceo-room

    if dry_run or not token:
        return

    for c in candidates[:10]:
        msg = (
            f":coin: *eBay仕入れ候補*\n"
            f"管理番号: *{c['management_no']}*\n"
            f"DB: {c['db_grader']} | {c['db_line1']} | {c['db_grade']}\n"
            f"eBay: {c['ebay_title'][:80]}\n"
            f"eBay価格: ${c['ebay_price_usd']:,.2f} ({c['ebay_price_jpy']:,}円)\n"
            f"仕入上限: {c['buy_limit_jpy']:,}円\n"
            f"マージン: {c['margin_jpy']:+,}円\n"
            f"セラー: {c['seller']} ({c['feedback']}%)\n"
            f"<{c['ebay_url']}|eBayリンク>"
        )
        data = json.dumps({'channel': channel, 'text': msg}).encode('utf-8')
        req = urllib.request.Request('https://slack.com/api/chat.postMessage', data=data, headers={
            'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'
        })
        try:
            urllib.request.urlopen(req, timeout=10)
        except:
            pass

    if len(candidates) > 10:
        summary = f":chart_with_upwards_trend: 他{len(candidates)-10}件の候補あり。"
        data = json.dumps({'channel': channel, 'text': summary}).encode('utf-8')
        req = urllib.request.Request('https://slack.com/api/chat.postMessage', data=data, headers={
            'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'
        })
        try:
            urllib.request.urlopen(req, timeout=10)
        except:
            pass


def run_monitor(dry_run=False, limit=None, slack=True):
    """メイン実行"""
    db = get_client()
    ebay = EbayAPI()

    # 最新レート
    dr = db.table('daily_rates').select('usd_jpy_calc').order('rate_date', desc=True).limit(1).execute()
    usd_jpy = float(dr.data[0]['usd_jpy_calc'])
    print(f"USD/JPY: {usd_jpy}")

    # DB全件取得
    all_coins = []
    last_id = None
    while True:
        q = db.table('coin_slab_data').select(
            'id,coin_id,management_no,grader,slab_line1,slab_line2,grade,material,ref1_buy_limit_jpy,ref2_yahoo_price_jpy,price_jpy'
        ).eq('status', 'completed_hit').gt('ref1_buy_limit_jpy', 0).order('id').limit(1000)
        if last_id:
            q = q.gt('id', last_id)
        r = q.execute()
        if not r.data:
            break
        all_coins.extend(r.data)
        last_id = r.data[-1]['id']
        if len(r.data) < 1000:
            break

    print(f"DB: {len(all_coins)}件")

    # 検索クエリ生成
    queries = generate_search_queries(all_coins, usd_jpy)
    if limit:
        queries = queries[:limit]
    print(f"検索クエリ: {len(queries)}件")
    print()

    # 通知済み追跡
    tracker_path = Path(__file__).parent.parent / 'data' / 'ebay_sourcing' / 'notified_items.json'
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    notified = {}
    if tracker_path.exists():
        notified = json.loads(tracker_path.read_text(encoding='utf-8'))

    candidates = []
    total_searched = 0
    total_items = 0

    for qi, qdata in enumerate(queries):
        q = qdata['query']
        max_usd = qdata['max_price_usd']
        print(f"[{qi+1}/{len(queries)}] \"{q}\" (上限${max_usd:,}) ... ", end='', flush=True)

        items, total = ebay.search(q, max_usd)
        total_searched += 1
        total_items += len(items)
        print(f"{total}件ヒット, {len(items)}件取得")

        for item in items:
            item_id = item.get('itemId', '')
            if item_id in notified:
                continue

            title = item.get('title', '')
            price_usd = float(item.get('price', {}).get('value', 0))
            price_jpy = round(price_usd * usd_jpy)

            # 全DBコインとマッチング
            for db_coin in qdata['coins']:
                is_match, reason = check_exact_match(title, db_coin)
                if not is_match:
                    continue

                # ref1_buy_limit_jpyは原価上限。eBay仕入上限は(原価上限 - 2750) / 1.1
                cost_limit = db_coin.get('ref1_buy_limit_jpy', 0)
                buy_limit = round((cost_limit - 2750) / 1.1)
                if buy_limit <= 0 or price_jpy >= buy_limit:
                    continue

                margin = buy_limit - price_jpy
                seller = item.get('seller', {})

                candidate = {
                    'management_no': db_coin.get('management_no', ''),
                    'db_grader': db_coin.get('grader', ''),
                    'db_line1': db_coin.get('slab_line1', ''),
                    'db_line2': db_coin.get('slab_line2', ''),
                    'db_grade': db_coin.get('grade', ''),
                    'buy_limit_jpy': buy_limit,
                    'ref2_price': db_coin.get('ref2_yahoo_price_jpy', 0),
                    'ebay_title': title,
                    'ebay_price_usd': price_usd,
                    'ebay_price_jpy': price_jpy,
                    'margin_jpy': margin,
                    'margin_pct': round(margin / price_jpy * 100, 1) if price_jpy > 0 else 0,
                    'seller': seller.get('username', '?'),
                    'feedback': seller.get('feedbackPercentage', '?'),
                    'ebay_url': item.get('itemWebUrl', ''),
                    'item_id': item_id,
                }
                candidates.append(candidate)
                notified[item_id] = datetime.now().isoformat()

                print(f"  ★ MATCH! #{candidate['management_no']} | ${price_usd:,.2f} ({price_jpy:,}円) | マージン{margin:+,}円")
                print(f"    DB: {db_coin['grader']} | {db_coin['slab_line1']} | {db_coin['grade']}")
                print(f"    eBay: {title[:80]}")
                print(f"    {item.get('itemWebUrl', '')[:100]}")
                break

        time.sleep(0.5)

    # 結果サマリー
    print()
    print(f"=== 結果 ===")
    print(f"検索クエリ: {total_searched}")
    print(f"eBay取得件数: {total_items}")
    print(f"API呼び出し: {ebay.call_count}")
    print(f"完全一致候補: {len(candidates)}件")

    if candidates:
        candidates.sort(key=lambda x: x['margin_jpy'], reverse=True)
        print()
        print(f"=== 候補一覧（マージン順） ===")
        for i, c in enumerate(candidates):
            print(f"{i+1:3d}. #{c['management_no']} | ${c['ebay_price_usd']:>8,.2f} ({c['ebay_price_jpy']:>10,}円) | 上限{c['buy_limit_jpy']:>10,}円 | マージン{c['margin_jpy']:>+10,}円 ({c['margin_pct']:>5.1f}%)")
            print(f"     {c['db_grader']} | {c['db_line1']} | {c['db_grade']}")
            print(f"     {c['ebay_title'][:80]}")

        # Slack通知
        if slack and not dry_run:
            send_slack(candidates)
            print(f"\nSlack通知: {min(len(candidates), 10)}件送信")

    # 通知済み保存（7日以上前を削除）
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    notified = {k: v for k, v in notified.items() if v > cutoff}
    tracker_path.write_text(json.dumps(notified, indent=2), encoding='utf-8')

    return candidates


def main():
    import argparse
    parser = argparse.ArgumentParser(description='eBay仕入れモニター')
    parser.add_argument('--dry-run', action='store_true', help='通知なしのテスト実行')
    parser.add_argument('--no-slack', action='store_true', help='Slack通知なし')
    parser.add_argument('--limit', type=int, default=None, help='検索クエリ数制限')
    args = parser.parse_args()

    run_monitor(
        dry_run=args.dry_run,
        limit=args.limit,
        slack=not args.no_slack and not args.dry_run,
    )


if __name__ == '__main__':
    main()
