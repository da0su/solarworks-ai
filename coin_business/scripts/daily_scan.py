"""
daily_scan.py — 日次コイン仕入れスキャン（6バケット市場先行方式）
================================================================
探索方針:
  1. eBay側を「NGC/PCGS × gold/silver/platinum」の6バケットで先取得
  2. 取得後にDB（staging）を照合
  3. ROUND1: 全属性一致 / ROUND2: 年号差 or グレード差1つのみ許可
  4. cert番号は最終照合専用（検索入口に使わない）

使い方:
    python daily_scan.py              # 通常実行（7日、Auction優先）
    python daily_scan.py --days 7     # 直近N日分（デフォルト7）
    python daily_scan.py --limit 50   # バケット1件あたり取得上限（デフォルト50）
    python daily_scan.py --no-auction # オークション限定外す（補助調査用）

出力:
    docs/daily_scan/YYYYMMDD_A_summary.md
    docs/daily_scan/YYYYMMDD_B_candidates.md
    docs/daily_scan/YYYYMMDD_C_final.md
"""
import sys, io, os, re, requests, base64, argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / 'docs' / 'daily_scan'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ----- 計算定数 -----
USD_JPY     = 145.0
IMPORT_DUTY = 1.12
FWD_JPY     = 2000
DOM_JPY     = 750
YAHOO_FEE   = 0.10
ROI_MIN     = 0.15
PROFIT_MIN  = 20_000    # 最低利益条件(JPY) — CEO確定ルール
CONF_MIN    = 0.7
PRICE_MIN   = 100_000   # staging参照最低価格(JPY) — DB対象は¥10万以上落札のみ

# ----- 注目候補 価格帯ガード -----
# DB照合なしでも「高額コイン」として注目候補に含めてよいgold denominations
# silver/platinum は DB照合なしの場合 原則除外（ほぼ¥10万未満）
HIGH_VALUE_GOLD_DENOMS = {
    '$20 Eagle',          # Saint-Gaudens / Liberty Head Double Eagle  ¥200k〜¥1M+
    '$50 Eagle',          # American Gold Eagle 1oz                    ¥300k〜¥900k
    '$25 Eagle',          # American Gold Eagle 1/2oz                  ¥150k〜¥400k
    '100 Francs',         # France Angel / Napoleon 100F               ¥500k〜¥1M+
    '5 Pound',            # British 5 Pound Gold                       ¥200k〜¥500k
    '2 Pound',            # British 2 Pound Gold                       ¥150k〜¥300k
    'Maple Leaf',         # Canada Gold Maple Leaf 1oz                 ¥300k〜¥900k
    'Philharmonic',       # Austria Gold Philharmonic 1oz              ¥300k〜¥900k
    'Kangaroo',           # Australia Gold Kangaroo 1oz                ¥300k〜¥900k
    'Sovereign',          # British Sovereign                          ¥80k〜¥120k
    '20 Francs',          # France Napoleon/Rooster 20F                ¥40k〜¥100k (境界)
    # ★ 古典米国金貨（注目候補に必ず含める）
    'Half Eagle',         # Liberty/Indian Head $5 Half Eagle          ¥80k〜¥500k
    'Classic $10 Eagle',  # Liberty/Indian Head $10 Eagle              ¥100k〜¥800k
    'Guinea',             # British Guinea                             ¥100k〜¥300k
    '1 Pound',            # British Gold Pound                         ¥100k〜¥200k
}
# 20 Francs は境界帯。高グレード(MS65+)なら¥10万超あり → score_notableで加点
# Sovereignも同様に境界帯。高グレード時のみ含める

# DB照合なし・silver でも「高額品」として注目候補に入れてよい denomination × grade の組み合わせ
# (Yahoo Japan で¥100k以上の実績がある silver コイン)
HIGH_VALUE_SILVER_COMBOS: dict[str, int] = {
    # denomination: 注目候補に含める最低グレード番号 (grade_numの数字部分)
    # ★ DB照合なし・かつ Yahoo Japan で¥10万以上の実績がある silver coin のみ列挙

    # ---- ブリオン ----
    'Kangaroo':       69,   # Silver Kangaroo PF69/70: ¥150k-¥300k
    'Philharmonic':   69,   # Silver Philharmonic PF69/70: ¥100k+
    'Sovereign':      65,   # Silver Proof Sovereign (稀): PF65+
    '1 Yen':          64,   # 旧1円銀貨 NGC MS64+: ¥100k-¥300k

    # Silver Eagle ($50 Eagle) は年号依存が大きい（2000年以降MS70: ¥15k-¥30k）
    # → DB照合なし時は除外（DB照合ありの場合は通る）

    # ---- 古典米国銀貨（¥10万以上帯に乗りうる銘柄） ----
    'Morgan Dollar':    62,  # Morgan $1 MS62+: ¥100k-¥500k (日付・ミントマーク次第)
    'Peace Dollar':     63,  # Peace $1 MS63+: ¥100k-¥300k
    'Walking Liberty':  63,  # Walking Liberty Half MS63+: ¥80k-¥200k
    'Mercury Dime':     65,  # Mercury Dime MS65+: ¥100k+ (key dates)
    'Seated Liberty':   62,  # Seated Liberty $1 MS62+: ¥150k+
    'Franklin Half':    65,  # Franklin Half MS65+: ¥100k (FBL特に)
    'Kennedy Half':     66,  # Kennedy Half MS66+ (proof set: PF68+): ¥100k+
    'Barber':           62,  # Barber silver MS62+: ¥100k+
    'Standing Liberty': 64,  # Standing Liberty Quarter MS64+: ¥100k+
}


# ================================================================
# STEP 1. eBay 6バケット検索（ROUND1の入口は常にこの6本のみ）
# ================================================================

# ROUND1用 固定6バケット
# cert × material のペアで eBay検索文字列を定義
ROUND1_BUCKETS = [
    # gold: "coin" を外してヒット率向上（category_ids=11116でコインに絞り込み済み）
    ('NGC',  'gold',     'NGC gold graded'),
    ('NGC',  'silver',   'NGC silver coin graded'),
    ('NGC',  'platinum', 'NGC platinum graded'),
    ('PCGS', 'gold',     'PCGS gold graded'),
    ('PCGS', 'silver',   'PCGS silver coin graded'),
    ('PCGS', 'platinum', 'PCGS platinum graded'),
]


def get_ebay_token() -> str:
    sys.path.insert(0, str(BASE_DIR))
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
    app_id     = os.environ.get('EBAY_CLIENT_ID', '')
    app_secret = os.environ.get('EBAY_CLIENT_SECRET', '')
    creds = base64.b64encode(f'{app_id}:{app_secret}'.encode()).decode()
    r = requests.post(
        'https://api.ebay.com/identity/v1/oauth2/token',
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        data='grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope',
        timeout=10
    )
    return r.json().get('access_token', '')


def search_bucket(token: str, query: str, days: int, limit: int,
                  auction_only: bool = True) -> list:
    """6バケット1本の eBay検索。ページネーション対応（最大200×ページ数）。

    auction_only=True の場合:
      - buyingOptions:{AUCTION} フィルタのみ使用
      - itemStartDate は不要（Auctionは有効期限切れで自動非表示）
      - → CEO手動確認と同等の件数が取得可能

    days パラメータは auction_only=False 時のみ有効。
    """
    h = {
        'Authorization':              f'Bearer {token}',
        'X-EBAY-C-MARKETPLACE-ID':    'EBAY_US',
    }
    filters = []
    if auction_only:
        filters.append('buyingOptions:{AUCTION}')
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            '%Y-%m-%dT%H:%M:%S.000Z')
        filters.append(f'itemStartDate:[{since}..]')

    all_items: list = []
    page_size = min(limit, 200)   # eBay Browse API 上限200
    pages = max(1, (limit + 199) // 200)   # limit=200→1page, 400→2pages

    for page in range(pages):
        p = {
            'q':            query,
            'category_ids': '11116',
            'limit':        str(page_size),
            'offset':       str(page * page_size),
            'filter':       ','.join(filters) if filters else None,
            'sort':         'endingSoonest',
        }
        # filter=None の場合はパラメータから除外
        p = {k: v for k, v in p.items() if v is not None}
        r = requests.get(
            'https://api.ebay.com/buy/browse/v1/item_summary/search',
            headers=h, params=p, timeout=20
        )
        if r.status_code != 200:
            break
        data  = r.json()
        items = data.get('itemSummaries', [])
        all_items.extend(items)
        # 取得件数がpage_size未満なら最終ページ
        if len(items) < page_size:
            break

    # 重複除去（item_id で）
    seen: set = set()
    unique: list = []
    for it in all_items:
        iid = it.get('itemId', '')
        if iid and iid not in seen:
            seen.add(iid)
            unique.append(it)

    # 入札ありを先頭に並べ替え
    unique.sort(key=lambda x: -x.get('bidCount', 0))
    return unique


# ================================================================
# STEP 2. eBayアイテムタイトルのパース（英語）
# ================================================================

# 除外キーワード（タイトルに含まれていたらスキップ）
EXCLUDE_KW = [
    'details', 'cleaned', 'damage', 'problem', 'chopmarked', 'chop mark',
    'countermark', 'counterstamp', 'lot of', '2 coins', '3 coins', '4 coins',
    'set of', 'replica', 'restrike copy', 'fantasy', 'token',
    'slab lot', '1 slab', 'estate sale', 'mystery lot', 'grab bag',
    # 安価コイン除外
    'libertad',       # Mexican Libertad — 日本市場での¥10万超は稀
    'commemorat',     # commemorative/commemoration — 現代記念コインは安価帯
    'james madison',  # US現代記念コイン
    'eisenhower',     # Eisenhower Dollar (¥5k-20k)
    'stormtrooper', 'star wars', 'pokemon', 'darth vader',  # キャラクターコイン
    'spongebob', 'hello kitty', 'mickey mouse',              # キャラクター追加
    'olympic games',  # カナダ等のオリンピック記念銀貨 (¥10k-30k)
    'olympiade',      # 同上
    'clad',           # クラッド（メッキ/貼り合わせ）コインは安価
    'girl scouts', 'boy scouts',  # 現代記念コイン
    'silver set', 'gold set', 'coin set',  # セット物（単品評価不可）
    'silver medal',   # メダル（コインではない）
    'america the beautiful',  # US 25セント現代シリーズ
]

COUNTRY_PATTERNS = [
    ('US', ['united states','american','america','usa','us coin','liberty','morgan','saint-gaudens',
            'double eagle','eagle gold','buffalo gold','indian head']),
    ('GB', ['great britain','british','england','united kingdom',' uk ','sovereign',
            '5 pound','5£','five pound','two pound','2 pound','guinea','half sovereign']),
    ('FR', ['france','french','napoleon','napoleon iii','rooster','angel','marianne',
            '20 francs','100 francs']),
    ('JP', ['japan','japanese','meiji','taisho','showa','yen','1 yen']),
    ('CA', ['canada','canadian','maple leaf']),
    ('AU', ['australia','australian','kangaroo','nugget']),
    ('AT', ['austria','austrian','philharmonic','wiener']),
    ('DE', ['germany','german','mark','reich']),
]

DENOM_PATTERNS = [
    # -------- 欧州金貨 --------
    ('Sovereign',    ['sovereign', 'half sovereign', '1/2 sovereign']),
    ('5 Pound',      ['5 pound', '5£', 'five pound']),
    ('2 Pound',      ['2 pound', 'two pound', '2£']),
    ('1 Pound',      ['1 pound', 'one pound', '1£']),
    ('Guinea',       ['guinea', 'half guinea', '1/2 guinea']),
    ('20 Francs',    ['20 franc', '20f', 'vingt franc']),
    ('100 Francs',   ['100 franc', 'cent franc']),

    # -------- 古典米国金貨（$20Eagle より先に定義必須） --------
    # Half Eagle ($5 gold, classic/type) — 分数American Eagleとは別物
    ('Half Eagle',    ['half eagle', '$5 gold coin', '5 dollar gold',
                       'liberty head $5', 'indian head $5',
                       'half-eagle']),
    # Classic Eagle ($10 gold, classic type)
    ('Classic $10 Eagle', ['indian head $10', 'indian $10', '$10 gold coin',
                            '10 dollar gold', 'liberty head $10',
                            'liberty eagle', '$10 liberty gold']),

    # -------- 米国金貨 American Eagle --------
    ('$20 Eagle',    ['double eagle', '20 dollar', '$20', '20-dollar']),
    # ★ 分数イーグル: $50(1oz) と区別するため先に定義する（first-match方式）
    ('$5 Eagle',     ['1/10 oz', '1/10oz', '1/10-oz', 'g$5', '$5 gold eagle',
                      'tenth ounce', 'tenth oz',
                      # 中国パンダ等の超小型金貨（1/25oz以下）→ $5Eagle同様に除外
                      '1/25 oz', '1/25oz', '1/20 oz', '1/20oz',
                      '1/15 oz', '1/15oz', 'g15y', 'g20y', 'g$15',
                      # マイクログラム金貨除外（0.5g, 1g など）
                      '0.5 gram', '0.5gram', '1 gram gold', '1gram gold',
                      '0.25 gram', '0.1 gram', 'gram of gold', '0.5 g gold']),
    ('$10 Eagle',    ['1/4 oz', '1/4oz', '1/4-oz', 'g$10',
                      '$10 gold eagle', 'quarter ounce gold']),
    ('$25 Eagle',    ['1/2 oz', '1/2oz', '1/2-oz', 'g$25',
                      '$25 gold', '25 dollar gold', '1/2 oz gold eagle']),
    # Silver Eagle も $50 Eagle にマッピング（silver/gold両方を包含）
    ('$50 Eagle',    ['american eagle', 'american gold eagle', 'american silver eagle',
                      'gold eagle', 'silver eagle',
                      '50 dollar', '$50 gold', '$50', 'eagle 1 oz',
                      '1 oz gold eagle', '1oz gold eagle',
                      '1 oz silver eagle', '1oz silver eagle']),

    # -------- 古典米国銀貨（高額帯） --------
    ('Morgan Dollar',     ['morgan dollar', 'morgan silver dollar', 'morgan $1',
                           'morgan s$1']),
    ('Peace Dollar',      ['peace dollar', 'peace silver dollar', 'peace $1']),
    ('Walking Liberty',   ['walking liberty half', 'walking lib', 'walk. lib.',
                           'walking liberty $0.50', 'walking liberty 50c']),
    ('Mercury Dime',      ['mercury dime', 'winged liberty dime', 'mercury 10c']),
    ('Seated Liberty',    ['seated liberty dollar', 'seated liberty $1',
                           'seated liberty half', 'gobrecht']),
    ('Franklin Half',     ['franklin half dollar', 'franklin half', 'franklin 50c']),
    ('Kennedy Half',      ['kennedy half dollar', 'kennedy half', 'kennedy 50c',
                           'kennedy silver']),
    ('Barber',            ['barber half', 'barber quarter', 'barber dime',
                           'barber silver']),
    ('Standing Liberty',  ['standing liberty quarter', 'standing liberty 25c']),

    # -------- ブリオンコイン --------
    ('1 Yen',        ['1 yen', 'one yen', 'yen silver']),
    ('Maple Leaf',   ['maple leaf', 'maple gold']),
    ('Kangaroo',     ['kangaroo', 'nugget', 'lunar gold']),
    ('Philharmonic', ['philharmonic', 'philharmoniker']),
    ('Panda',        ['panda gold', 'china panda', 'chinese panda', 'gold panda']),
]

TYPE_PATTERNS = [
    ('Napoleon III',  ['napoleon iii', 'napoléon iii']),
    ('Napoleon I',    ['napoleon i', 'napoleon i ', 'napoléon i']),
    ('Napoleon',      ['napoleon']),
    ('Angel',         ['angel', 'ange']),
    ('Rooster',       ['rooster', 'coq']),
    ('Double Eagle',  ['double eagle', 'saint-gaudens', 'liberty head double']),
    ('Eagle',         ['american eagle', 'gold eagle']),
    ('Sovereign',     ['sovereign']),
    ('Maple Leaf',    ['maple leaf']),
    ('Kangaroo',      ['kangaroo', 'nugget']),
    ('Philharmonic',  ['philharmonic']),
]


def parse_ebay_title(title: str) -> dict:
    """eBay英語タイトルから属性を抽出する"""
    t = title.lower()

    # --- cert_company ---
    if 'pcgs' in t: cert = 'PCGS'
    elif 'ngc' in t: cert = 'NGC'
    else: cert = ''

    # --- material ---
    material = 'unknown'
    # 注: 'or ' は仏語"or"(gold)だが単語境界なしだと 'error' にもマッチするため除外
    if any(k in t for k in ['gold','gold coin','aurum',' or coin',' or ',' d\'or']): material = 'gold'
    elif any(k in t for k in ['silver','argent','plata']): material = 'silver'
    elif 'platinum' in t: material = 'platinum'

    # --- year ---
    year = None
    m = re.search(r'\b(1[5-9]\d{2}|20[0-2]\d)\b', title)
    if m: year = int(m.group(1))

    # --- grade ---
    grade_num = ''
    g = re.search(r'\b(MS|PF|PR|SP|AU|EF|VF|XF)\s*(\d{2})\b', title.upper())
    if g: grade_num = f'{g.group(1)}{g.group(2)}'

    # --- country ---
    country = 'UNKNOWN'
    for cnt, patterns in COUNTRY_PATTERNS:
        if any(p in t for p in patterns):
            country = cnt
            break

    # --- denomination ---
    denomination = 'UNKNOWN'
    for denom, patterns in DENOM_PATTERNS:
        if any(p in t for p in patterns):
            denomination = denom
            break

    # --- coin_type ---
    coin_type = 'UNKNOWN'
    for ctype, patterns in TYPE_PATTERNS:
        if any(p in t for p in patterns):
            coin_type = ctype
            break

    return {
        'cert_company': cert,
        'material':     material,
        'country':      country,
        'denomination': denomination,
        'coin_type':    coin_type,
        'year':         year,
        'grade_num':    grade_num,
        '_title':       title,
    }


def is_excluded_item(item: dict) -> tuple[bool, str]:
    """除外判定"""
    title = item.get('title', '')
    t_low = title.lower()

    for kw in EXCLUDE_KW:
        if kw in t_low:
            return True, f'kw:{kw}'

    # lot pattern: "5 coins", "lot of 3"
    if re.search(r'\b\d+\s+coins?\b', t_low):
        return True, 'multi-lot'

    # multi-lot: "1875CC & 1904" など2つの年号が & / ; で並ぶ場合
    # ※ ミントマーク付き(1875CC等)も拾えるよう \b なしで抽出
    years_approx = re.findall(r'(1[5-9]\d{2}|20[0-2]\d)', title)
    if len(set(years_approx)) >= 2 and re.search(r'[&;]', title):
        return True, 'multi-lot-years'

    # NGC/PCGS確認
    t_up = title.upper()
    if 'NGC' not in t_up and 'PCGS' not in t_up:
        return True, 'non-NGC/PCGS'

    # 発送元
    loc_country = item.get('itemLocation', {}).get('country', '')
    if loc_country and loc_country not in ('US', 'GB'):
        return True, f'non-US/GB({loc_country})'

    return False, ''


# ================================================================
# STEP 3. Staging DB読み取りと属性正規化
# ================================================================

# 日本語 → 正規化用パターン
JP_MATERIAL = {
    'gold':     ['金貨','ゴールド','gold','ソブリン','ギニー','ダカット','エンジェル','ナポレオン',
                 'フラン金','メイプル','イーグル','ダブルイーグル','クルーガーランド','パンダ',
                 'ウィーン','フィルハーモニー','ポンド金','guinea','angel','napoleon','eagle',
                 'sovereign','maple','krugerrand','philharmon','sovereign'],
    'silver':   ['銀貨','シルバー','silver','タラー','クラウン','モルガン','旧1円','1円','明治銀',
                 '大正銀','リバティダラー','ピース','morgan','crown','thaler','peso'],
    'platinum': ['プラチナ','白金','platinum'],
}

JP_COUNTRY = [
    ('US', ['アメリカ','米国','usa','american','20ドル','20 dollar','リバティ','morgan']),
    ('GB', ['イギリス','英国','britain','british','uk','sovereign','ソブリン','5ポンド','ギニー',
             'エリザベス','ヴィクトリア','george','charles','victoria','elizabeth']),
    ('FR', ['フランス','france','french','ナポレオン','napoleon','angel','エンジェル','ロースター']),
    ('JP', ['日本','japan','japanese','1円','旧1円','明治','大正','昭和','meiji']),
    ('CA', ['カナダ','canada','canadian','メイプル','maple']),
    ('AU', ['オーストラリア','australia','カンガルー','kangaroo']),
    ('AT', ['オーストリア','austria','ウィーン','philharmon']),
]

JP_DENOM = [
    ('Sovereign',    ['ソブリン','sovereign','1/2 sovereign']),
    ('5 Pound',      ['5ポンド','5 pound','five pound']),
    ('2 Pound',      ['2ポンド','2 pound']),
    ('Guinea',       ['ギニー','guinea']),
    ('20 Francs',    ['20フラン','20 franc','20francs','ナポレオン20']),
    ('100 Francs',   ['100フラン','100 franc','cent franc']),
    ('$20 Eagle',    ['20ドル','double eagle','ダブルイーグル','$20']),
    # ★ 分数イーグル: $50(1oz)と区別（先に定義）
    ('$5 Eagle',     ['1/10oz','1/10 oz','g$5','5ドル金貨','$5イーグル']),
    ('$10 Eagle',    ['1/4oz','1/4 oz','g$10','10ドル金貨','$10イーグル']),
    ('$25 Eagle',    ['1/2oz','1/2 oz','g$25','25ドル金貨','$25イーグル']),
    ('$50 Eagle',    ['50ドル','イーグル金貨','50 dollar','american eagle']),
    ('1 Yen',        ['1円','旧1円','一円']),
    ('Maple Leaf',   ['メイプル','maple leaf']),
    ('Kangaroo',     ['カンガルー','kangaroo']),
    ('Philharmonic', ['ウィーン','フィルハーモニー','philharmon']),
]

JP_TYPE = [
    ('Napoleon III', ['ナポレオン3世','napoleon iii','napoléon iii']),
    ('Napoleon',     ['ナポレオン','napoleon']),
    ('Angel',        ['エンジェル','angel','ange']),
    ('Rooster',      ['ロースター','rooster']),
    ('Double Eagle', ['ダブルイーグル','double eagle','saint-gaudens','リバティヘッド20']),
    ('Eagle',        ['イーグル','american eagle']),
    ('Sovereign',    ['ソブリン','sovereign']),
    ('Maple Leaf',   ['メイプル','maple leaf']),
    ('Kangaroo',     ['カンガルー','kangaroo']),
    ('Philharmonic', ['フィルハーモニー','philharmon']),
]


def parse_jp_title(title: str, grade_text: str = '', year: int = None,
                   cert_company: str = '') -> dict:
    """Staging日本語タイトルから属性を正規化"""
    t = title.lower()

    # material
    material = 'unknown'
    for mat, kws in JP_MATERIAL.items():
        if any(k.lower() in t for k in kws):
            material = mat
            break

    # country
    country = 'UNKNOWN'
    for cnt, kws in JP_COUNTRY:
        if any(k.lower() in t for k in kws):
            country = cnt
            break

    # denomination
    denomination = 'UNKNOWN'
    for denom, kws in JP_DENOM:
        if any(k.lower() in t for k in kws):
            denomination = denom
            break

    # coin_type
    coin_type = 'UNKNOWN'
    for ctype, kws in JP_TYPE:
        if any(k.lower() in t for k in kws):
            coin_type = ctype
            break

    # grade_num
    grade_num = ''
    if grade_text:
        g = re.search(r'(MS|PF|PR|SP|AU|EF|VF|XF)\s*(\d{2})', grade_text.upper())
        if g: grade_num = f'{g.group(1)}{g.group(2)}'

    return {
        'cert_company': cert_company,
        'material':     material,
        'country':      country,
        'denomination': denomination,
        'coin_type':    coin_type,
        'year':         year,
        'grade_num':    grade_num,
        '_title':       title,
    }


def load_staging(verbose: bool = False) -> list:
    """Staging DB (read-only) から NGC/PCGS・conf>=0.7・¥50k+ を取得"""
    sys.path.insert(0, str(BASE_DIR))
    from scripts.supabase_client import get_client
    sb = get_client()

    recs, offset = [], 0
    while True:
        r = sb.table('yahoo_sold_lots_staging').select(
            'id,lot_title,year,cert_company,cert_number,grade_text,sold_price_jpy,parse_confidence'
        ).in_('cert_company', ['NGC', 'PCGS'])\
         .gte('parse_confidence', CONF_MIN)\
         .gte('sold_price_jpy', PRICE_MIN)\
         .order('sold_price_jpy', desc=True)\
         .range(offset, offset + 499)\
         .execute()
        if not r.data: break
        recs.extend(r.data)
        if len(r.data) < 500: break
        offset += 500

    # 属性正規化
    parsed = []
    for rec in recs:
        coin = parse_jp_title(
            title=rec.get('lot_title', ''),
            grade_text=rec.get('grade_text', ''),
            year=rec.get('year'),
            cert_company=rec.get('cert_company', ''),
        )
        if coin['material'] not in ('gold', 'silver', 'platinum'):
            continue
        coin.update({
            'staging_id':     rec.get('id', ''),
            'cert_number':    rec.get('cert_number', ''),
            'sold_price_jpy': rec.get('sold_price_jpy', 0) or 0,
            'lot_title':      rec.get('lot_title', ''),
        })
        parsed.append(coin)

    if verbose:
        print(f'  Staging: {len(recs)}件読み込み → gold/silver/platinum: {len(parsed)}件')
    return parsed


# ================================================================
# STEP 3-B. CEO確認台帳 照会・保存（重複禁止ルール）
# ================================================================

# 再提出許可条件のしきい値
DEDUP_DAYS         = 7       # 同一案件の再提出禁止期間（日）
PRICE_DROP_THRESH  = 0.10    # 前回比10%以上下落で再提出許可
BIDS_PLUS_THRESH   = 5       # 入札数前回比+5以上で再提出許可
ENDING_HOURS_THRESH = 24.0   # 終了まで24時間以内で再提出許可


def load_recent_submissions(days: int = DEDUP_DAYS) -> dict:
    """
    直近 days 日間に CEO に提出済みの案件を ceo_review_log から取得。

    Returns:
        dict: { item_id: {'submitted_at': datetime, 'bid_count': int,
                          'price_usd': float, 'ceo_decision': str} }
        テーブルが存在しない場合は空 dict を返す（graceful degradation）。
    """
    sys.path.insert(0, str(BASE_DIR))
    from scripts.supabase_client import get_client
    sb = get_client()

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        r = sb.table('ceo_review_log')\
              .select('item_id,submitted_to_ceo_at,bid_count_snapshot,price_snapshot_usd,ceo_decision')\
              .gte('submitted_to_ceo_at', since)\
              .not_.is_('submitted_to_ceo_at', 'null')\
              .execute()
        result = {}
        for row in (r.data or []):
            iid = row.get('item_id', '')
            if not iid:
                continue
            # 同一 item_id が複数回あれば最新を使う
            if iid not in result or (row.get('submitted_to_ceo_at', '') >
                                     result[iid].get('submitted_at', '')):
                result[iid] = {
                    'submitted_at': row.get('submitted_to_ceo_at', ''),
                    'bid_count':    row.get('bid_count_snapshot', 0) or 0,
                    'price_usd':    float(row.get('price_snapshot_usd', 0) or 0),
                    'ceo_decision': row.get('ceo_decision', ''),
                }
        return result
    except Exception:
        # テーブル未作成 or 接続エラー → dedup スキップ
        return {}


def check_resubmit_reason(ep: dict, item: dict,
                          prev: dict | None) -> str | None:
    """
    同一案件が既提出の場合、再提出が許可されるかを判定。

    Returns:
        再提出理由コード (str) または None（ブロック）
    """
    if prev is None:
        return None  # 未提出 → ブロック判定不要

    # 価格10%以上下落
    cur_price = float(item.get('price', {}).get('value', 0) or 0)
    prev_price = prev.get('price_usd', 0)
    if prev_price > 0 and cur_price > 0 and (prev_price - cur_price) / prev_price >= PRICE_DROP_THRESH:
        return 'price_drop_10pct'

    # 入札数 +5 以上
    cur_bids = item.get('bidCount', 0)
    prev_bids = prev.get('bid_count', 0)
    if cur_bids - prev_bids >= BIDS_PLUS_THRESH:
        return 'bids_plus_5'

    # 終了まで24時間以内
    end_dt = item.get('itemEndDate', '')
    if end_dt:
        try:
            end = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
            hrs = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            if 0 < hrs <= ENDING_HOURS_THRESH:
                return 'ending_24h'
        except Exception:
            pass

    # CEO HOLD → 再確認 (前回 HOLD なら常に再提出可)
    if prev.get('ceo_decision', '') == 'HOLD':
        return 'ceo_hold_recheck'

    return None  # ブロック


def dedup_filter(scored_items: list, recent_submissions: dict) -> tuple:
    """
    スコアリング済みリストから重複提出ブロック対象を除外する。

    - 7日以内提出済み かつ 再提出理由なし → ブロック（n_blocked++）
    - 7日以内提出済み かつ 再提出理由あり → RESUBMITTED として通過
    - 未提出 → NEW として通過

    Returns:
        (passed: list, blocked: list, n_new: int, n_resubmit: int, n_blocked: int)
        passed 要素: (score, ep, item, db_match, match_type, profit_jpy, dup_status, resubmit_reason)
    """
    passed  = []
    blocked = []
    n_new = n_resub = n_block = 0

    for entry in scored_items:
        score, ep, item, db_match, match_type, profit_jpy = entry[:6]
        item_id = item.get('itemId', '')

        prev = recent_submissions.get(item_id)

        if prev is None:
            # 未提出 (NEW)
            n_new += 1
            passed.append((score, ep, item, db_match, match_type, profit_jpy, 'NEW', None))
        else:
            reason = check_resubmit_reason(ep, item, prev)
            if reason:
                # 再提出許可
                n_resub += 1
                passed.append((score, ep, item, db_match, match_type, profit_jpy, 'RESUBMITTED', reason))
            else:
                # ブロック
                n_block += 1
                blocked.append((score, ep, item, db_match, match_type, profit_jpy, 'DUPLICATE_BLOCKED', None))

    return passed, blocked, n_new, n_resub, n_block


def save_ceo_review_entries(notable_items: list, candidates: list,
                            scan_date: str, recent_submissions: dict,
                            verbose: bool = False) -> int:
    """
    Top20/50/100/BID/WATCH の案件を ceo_review_log に upsert（提出記録）。

    - marketplace + item_id の組み合わせが既存なら update
    - 新規なら insert
    - テーブルが存在しない場合は graceful skip（0件返す）

    Returns:
        保存成功件数
    """
    sys.path.insert(0, str(BASE_DIR))
    from scripts.supabase_client import get_client
    sb = get_client()

    records = []
    now_iso = datetime.now(timezone.utc).isoformat()

    def _make_record(entry, bucket: str) -> dict:
        # notable_items の要素: (score, ep, item, db_match, match_type, profit_jpy, dup_status, resubmit_reason)
        if len(entry) >= 8:
            score, ep, item, db_match, match_type, profit_jpy, dup_status, resub_reason = entry[:8]
        else:
            score, ep, item, db_match, match_type, profit_jpy = entry[:6]
            dup_status, resub_reason = 'NEW', None

        item_id   = item.get('itemId', '')
        url       = item.get('itemWebUrl', item.get('itemHref', ''))
        title     = item.get('title', '')
        bids      = item.get('bidCount', 0)
        price_usd = float(item.get('price', {}).get('value', 0) or 0)
        ref_jpy   = db_match['sold_price_jpy'] if db_match else None

        # 提出日時: DUPLICATE_BLOCKED は None (未提出), それ以外は now
        submitted_at = None if dup_status == 'DUPLICATE_BLOCKED' else now_iso

        # submit_count
        prev = recent_submissions.get(item_id, {})
        prev_count = 0  # 新規の場合
        if prev:
            prev_count = 1  # 既提出回数 (簡易: 実際はDB側でカウント)

        return {
            'marketplace':           'eBay',
            'source_group':          'EBAY',
            'auction_house':         'EBAY',
            'item_id':               item_id,
            'url':                   url,
            'title_snapshot':        title[:200],
            'cert_company':          ep.get('cert_company', ''),
            'cert_number':           None,   # eBay タイトルから cert_number は現状未取得
            'grade':                 ep.get('grade_num', ''),
            'country':               ep.get('country', ''),
            'year':                  ep.get('year'),
            'denomination':          ep.get('denomination', ''),
            'material':              ep.get('material', ''),
            'bid_count_snapshot':    bids,
            'price_snapshot_usd':    price_usd if price_usd > 0 else None,
            'yahoo_ref_price':       ref_jpy,
            'profit_estimate':       profit_jpy,
            'db_similarity':         db_label(match_type),
            'db_ref_id':             db_match.get('staging_id', '') if db_match else None,
            'snapshot_score':        score,
            'scan_date':             scan_date,
            'review_bucket':         bucket,
            'first_seen_at':         now_iso,
            'submitted_to_ceo_at':   submitted_at,
            'submit_count':          prev_count + (0 if dup_status == 'DUPLICATE_BLOCKED' else 1),
            'duplicate_status':      dup_status,
            'resubmit_reason':       resub_reason,
        }

    # notable_items は dedup_filter 後の passed リスト
    n = len(notable_items)
    for i, entry in enumerate(notable_items):
        if i < 20:
            bucket = 'Top20'
        elif i < 50:
            bucket = 'Top50'
        else:
            bucket = 'Top100'
        records.append(_make_record(entry, bucket))

    # 入札候補 (candidates)
    for c in candidates:
        if c['tier'] not in ('PASS', 'HOLD'):
            continue
        fake_entry = (0, c['ep'], c['item'], c.get('ref_db'), None, c.get('profit_jpy'), 'NEW', None)
        records.append(_make_record(fake_entry, 'BID'))

    if not records:
        return 0

    try:
        # upsert: marketplace + item_id + scan_date を一意とする
        r = sb.table('ceo_review_log').upsert(
            records,
            on_conflict='marketplace,item_id,scan_date'
        ).execute()
        saved = len(r.data) if r.data else 0
        if verbose:
            print(f'  ceo_review_log: {saved}件 upsert')
        return saved
    except Exception as e:
        if verbose:
            print(f'  ceo_review_log upsert スキップ (テーブル未作成等): {e}')
        return 0


# ================================================================
# STEP 4. ROUND1 / ROUND2 照合
# ================================================================

def _known(v) -> bool:
    """値が有効（UNKNOWN/None/'' でない）かどうか"""
    return v not in ('UNKNOWN', None, '')


def _soft_match(ev, dv) -> bool:
    """どちらかが UNKNOWN/None/'' → スキップ(True)。両方わかれば一致判定。"""
    if not _known(ev) or not _known(dv): return True
    return ev == dv


def match_round1(ebay: dict, db_coins: list) -> list:
    """ROUND1: cert + material + denomination + grade を厳格必須一致。
       country / coin_type / year はどちらか不明ならスキップ。
       DB side の denomination/grade が UNKNOWN/'' → マッチ不可。
    """
    hits = []
    cert  = ebay.get('cert_company', '')
    denom = ebay.get('denomination', 'UNKNOWN')
    grade = ebay.get('grade_num', '')
    mat   = ebay.get('material', 'unknown')

    if cert  not in ('NGC', 'PCGS'):               return hits
    if mat   not in ('gold', 'silver', 'platinum'): return hits
    if not _known(denom):                           return hits  # eBay denomination不明
    if not _known(grade):                           return hits  # eBay grade不明

    for db in db_coins:
        if db['cert_company'] != cert: continue
        if db['material']     != mat:  continue
        # denomination: 両方必須
        if not _known(db['denomination']): continue
        if db['denomination'] != denom:    continue
        # grade: 両方必須
        if not _known(db['grade_num']): continue
        if db['grade_num'] != grade:    continue
        # country / coin_type / year: soft match
        if not _soft_match(ebay.get('country','UNKNOWN'), db['country']): continue
        if not _soft_match(ebay.get('coin_type','UNKNOWN'), db['coin_type']): continue
        if not _soft_match(ebay.get('year'), db['year']): continue
        hits.append(db)
    return hits


def match_round2(ebay: dict, db_coins: list, variant: str) -> list:
    """ROUND2: cert + material + denomination は厳格必須一致。
       year_delta: grade一致 + 年号差。grade_delta: 年号一致 + グレード差。
       どちらも DB side の denomination は known 必須。
    """
    hits = []
    cert  = ebay.get('cert_company', '')
    denom = ebay.get('denomination', 'UNKNOWN')
    mat   = ebay.get('material', 'unknown')
    e_grade = ebay.get('grade_num', '')
    e_year  = ebay.get('year')

    if cert not in ('NGC', 'PCGS'):               return hits
    if mat  not in ('gold', 'silver', 'platinum'): return hits
    if not _known(denom):                           return hits

    for db in db_coins:
        if db['cert_company'] != cert: continue
        if db['material']     != mat:  continue
        if not _known(db['denomination']): continue
        if db['denomination'] != denom:    continue
        if not _soft_match(ebay.get('country','UNKNOWN'), db['country']): continue
        if not _soft_match(ebay.get('coin_type','UNKNOWN'), db['coin_type']): continue

        d_grade = db.get('grade_num', '')
        d_year  = db.get('year')

        if variant == 'year_delta':
            # グレードは一致 + 年号は明確に異なる
            if (not _known(e_grade) or not _known(d_grade)): continue  # grade不明は除外
            if e_grade != d_grade:                           continue  # grade違い
            if not _known(e_year) or not _known(d_year):    continue  # year不明は除外
            if e_year == d_year:                             continue  # 同年は除外
            hits.append(db)

        elif variant == 'grade_delta':
            # 年号一致（soft ok) + グレードは明確に異なる
            if not _soft_match(e_year, d_year): continue  # year mismatch (both known)
            if (not _known(e_grade) or not _known(d_grade)): continue
            if e_grade == d_grade:                            continue  # 同グレードは除外
            hits.append(db)
    return hits


# ================================================================
# STEP 5. 費用計算 / 除外
# ================================================================

def calc_cost(ebay_usd: float, yahoo_jpy: int) -> dict:
    total  = ebay_usd * USD_JPY * IMPORT_DUTY + FWD_JPY + DOM_JPY
    net    = yahoo_jpy * (1 - YAHOO_FEE)
    profit = net - total
    roi    = profit / total if total > 0 else 0.0
    # 買い上限: ROI≥15% かつ 利益≥¥20,000 の両方を満たす最大価格
    # profit≥20k → net - buy×145×1.12 - 2750 ≥ 20000 → buy ≤ (net-22750)/(145×1.12)
    bl_roi    = yahoo_jpy * (1 - YAHOO_FEE) * (1 - ROI_MIN) / USD_JPY
    bl_profit = (yahoo_jpy * (1 - YAHOO_FEE) - PROFIT_MIN - FWD_JPY - DOM_JPY) / (USD_JPY * IMPORT_DUTY)
    bl        = min(bl_roi, bl_profit)   # 両条件を満たす厳しい方を採用
    # PASS: ROI≥15% かつ 利益≥¥20,000
    if profit >= PROFIT_MIN and roi >= ROI_MIN:
        tier = 'PASS'
    elif profit > 0:
        tier = 'HOLD'
    else:
        tier = 'FAIL'
    return {
        'total_jpy': int(total), 'net_jpy': int(net),
        'profit_jpy': int(profit), 'roi': roi, 'bl_usd': bl, 'tier': tier,
    }


# ================================================================
# STEP 6. Phase A: スキャン本体（6バケット→DB照合）
# ================================================================

def run_phase_a(days: int = 7, limit_per_bucket: int = 200,
                auction_only: bool = True, verbose: bool = True) -> tuple:
    """
    【第1段階】市場取得: eBay 6バケット → 300件以上確保
    【第2段階】スコープ判定: 120〜180件 (DB完全一致なしでは落とさない)
    【第3段階】スコアリング: Top20/50/100

    STEP1: eBay 6バケット取得
    STEP2: eBayアイテム属性パース + 除外
    STEP3: Staging DB ロード
    STEP4: ROUND1照合 (入札候補選定)
    STEP5: ROUND2照合 (入札候補選定)
    STEP6-A: 第2段階 スコープ判定 (120-180件確保)
    STEP6-B: 第3段階 スコアリング (Top20/50/100)
    """
    today = datetime.now().strftime('%Y-%m-%d')
    if verbose:
        print(f'\n[Phase A] {today}  直近{days}日 Auction={auction_only}')
        print('=' * 55)

    # --- STEP1: eBay 6バケット ---
    token = get_ebay_token()
    bucket_results = {}  # (cert, material) → [item]
    r1_raw_count = 0

    if verbose: print('[STEP1] eBay 6バケット取得...')
    for cert, material, query in ROUND1_BUCKETS:
        items = search_bucket(token, query, days=days, limit=limit_per_bucket,
                              auction_only=auction_only)
        bucket_results[(cert, material)] = items
        r1_raw_count += len(items)
        if verbose:
            print(f'  [{cert} × {material}] "{query}": {len(items)}件')

    if verbose: print(f'  ROUND1 生取得合計: {r1_raw_count}件')

    # ★ クロスバケット重複除去（同一itemIdが複数バケットに出現するケース除去）
    # 例: 金/白金バイメタル → NGC gold & NGC platinum の両バケットでヒット
    _seen_ids: set = set()
    for key in bucket_results:
        unique_bucket: list = []
        for it in bucket_results[key]:
            iid = it.get('itemId', '')
            if iid and iid not in _seen_ids:
                _seen_ids.add(iid)
                unique_bucket.append(it)
        bucket_results[key] = unique_bucket

    # --- STEP2: パース + 除外 ---
    if verbose: print('[STEP2] eBayアイテム属性パース + 除外...')
    parsed_items = []
    excluded_count = 0
    for (cert, material), items in bucket_results.items():
        for item in items:
            excl, reason = is_excluded_item(item)
            if excl:
                excluded_count += 1
                continue
            parsed = parse_ebay_title(item.get('title', ''))
            parsed['_item'] = item
            parsed['_bucket_cert'] = cert
            parsed['_bucket_material'] = material
            parsed_items.append(parsed)
    if verbose:
        print(f'  除外: {excluded_count}件 → 残り: {len(parsed_items)}件')

    # --- STEP3: Staging DB ---
    if verbose: print('[STEP3] Staging DB ロード...')
    db_coins = load_staging(verbose=verbose)

    # --- STEP4: ROUND1照合 ---
    if verbose: print('[STEP4] ROUND1照合 (全属性一致)...')
    candidates = []
    r1_hit_items = set()

    for ep in parsed_items:
        hits = match_round1(ep, db_coins)
        if hits:
            ref = max(hits, key=lambda d: d['sold_price_jpy'])
            item = ep['_item']
            usd  = float(item.get('price', {}).get('value', 0))
            bids = item.get('bidCount', 0)
            ref_jpy = ref['sold_price_jpy']

            if usd <= 0 or bids == 0:
                tier = 'WATCH'
                cost = {'total_jpy': 0, 'net_jpy': 0, 'profit_jpy': 0,
                        'roi': 0.0, 'bl_usd': calc_cost(1, ref_jpy)['bl_usd'], 'tier': 'WATCH'}
            else:
                cost = calc_cost(usd, ref_jpy)
                tier = cost['tier']

            candidates.append({
                'round': 1, 'variant': 'exact',
                'ep': ep, 'ref_db': ref, 'item': item,
                'usd': usd, 'bids': bids, 'ref_jpy': ref_jpy,
                **cost,
            })
            r1_hit_items.add(id(item))

    r1_hits = len(candidates)
    if verbose: print(f'  ROUND1ヒット: {r1_hits}件')

    # --- STEP5: ROUND2照合（ROUND1が0件のバケットのみ） ---
    if verbose: print('[STEP5] ROUND2照合 (1差許容)...')
    r1_hit_buckets = {c['ep']['_bucket_cert'] + c['ep']['_bucket_material']
                      for c in candidates if c['round'] == 1}

    r2_count = 0
    for ep in parsed_items:
        bk = ep['_bucket_cert'] + ep['_bucket_material']
        if bk in r1_hit_buckets:
            continue  # ROUND1でヒットのあったバケットはスキップ

        for variant in ('year_delta', 'grade_delta'):
            hits = match_round2(ep, db_coins, variant)
            if not hits:
                continue
            ref = max(hits, key=lambda d: d['sold_price_jpy'])
            item = ep['_item']
            usd  = float(item.get('price', {}).get('value', 0))
            bids = item.get('bidCount', 0)
            ref_jpy = ref['sold_price_jpy']

            if usd <= 0 or bids == 0:
                cost = {'total_jpy': 0, 'net_jpy': 0, 'profit_jpy': 0,
                        'roi': 0.0, 'bl_usd': calc_cost(1, ref_jpy)['bl_usd'], 'tier': 'WATCH'}
            else:
                cost = calc_cost(usd, ref_jpy)

            # 既にこのitemがcandidatesに入っていないか確認
            if any(c['item'] is item for c in candidates):
                continue

            candidates.append({
                'round': 2, 'variant': variant,
                'ep': ep, 'ref_db': ref, 'item': item,
                'usd': usd, 'bids': bids, 'ref_jpy': ref_jpy,
                **cost,
            })
            r2_count += 1

    if verbose:
        print(f'  ROUND2ヒット: {r2_count}件')
        pass_n = sum(1 for c in candidates if c['tier'] == 'PASS')
        hold_n = sum(1 for c in candidates if c['tier'] == 'HOLD')
        fail_n = sum(1 for c in candidates if c['tier'] == 'FAIL')
        watch_n = sum(1 for c in candidates if c['tier'] == 'WATCH')
        print(f'  候補合計: PASS={pass_n} / HOLD={hold_n} / FAIL={fail_n} / WATCH={watch_n}')

    # PASS→HOLD→WATCH→FAIL の順、ROI降順
    tier_order = {'PASS': 0, 'HOLD': 1, 'WATCH': 2, 'FAIL': 3}
    candidates.sort(key=lambda c: (tier_order.get(c['tier'], 9), -c.get('roi', 0)))

    # --- STEP3-B: CEO確認台帳 照会（重複禁止チェック） ---
    if verbose: print('[STEP3-B] CEO確認台帳 照会 (直近7日重複チェック)...')
    recent_submissions = load_recent_submissions(days=DEDUP_DAYS)
    if verbose:
        print(f'  直近{DEDUP_DAYS}日提出済み: {len(recent_submissions)}件')

    # --- STEP6-A: 第2段階 スコープ判定 ---
    if verbose: print('[STEP6-A] 第2段階: スコープ判定 (目標120〜180件)...')
    cand_ids = {id(c['item']) for c in candidates}
    scope_passed, scope_bkdn = scope_filter_stage2(parsed_items, cand_ids)
    if verbose:
        print(f'  スコープ通過: {scope_bkdn["passed"]}件 '
              f'(除外-候補: {scope_bkdn["excluded_cand"]}件 / '
              f'除外-スコープ外: {scope_bkdn["failed_scope"]}件)')

    # --- STEP6-B: 第3段階 スコアリング → Top20/50/100 ---
    if verbose: print('[STEP6-B] 第3段階: スコアリング → Top20/50/100...')
    all_scored, db_bkdn = score_and_tier_stage3(scope_passed, db_coins, count=100)
    if verbose:
        top20_n = min(20, len(all_scored))
        print(f'  スコアリング: {len(all_scored)}件 (Top20:{top20_n}件)')
        print(f'  DB照合 R1={db_bkdn["r1_match"]} / R2={db_bkdn["r2_match"]} / '
              f'なし={db_bkdn["no_match"]}')

    # --- STEP6-C: 重複除外フィルタ ---
    if verbose: print('[STEP6-C] 重複除外フィルタ (7日以内提出済みをブロック)...')
    dedup_passed, dedup_blocked, n_new, n_resub, n_blocked = dedup_filter(
        all_scored, recent_submissions
    )
    # Top100 スライス
    notable10 = dedup_passed[:100]
    if verbose:
        print(f'  NEW: {n_new}件 / 再提出許可: {n_resub}件 / ブロック: {n_blocked}件')
        print(f'  注目候補確定: {len(notable10)}件 (Top20:{min(20,len(notable10))}件)')
        if len(notable10) < 100:
            print(f'  ⚠️  Top100未達: {len(notable10)}件')
            print(f'     第1段階生取得: {r1_raw_count}件')
            print(f'     第2段階スコープ通過: {scope_bkdn["passed"]}件')
            print(f'     第3段階スコアリング: {len(all_scored)}件')
            print(f'     重複ブロック: {n_blocked}件 → 最終: {len(notable10)}件')

    stats = {
        'r1_raw':              r1_raw_count,
        'excluded':            excluded_count,
        'parsed':              len(parsed_items),
        'db_coins':            len(db_coins),
        'r1_hits':             r1_hits,
        'r2_hits':             r2_count,
        'stage2_passed':       scope_bkdn['passed'],
        'stage2_failed':       scope_bkdn['failed_scope'],
        'stage3_r1':           db_bkdn['r1_match'],
        'stage3_r2':           db_bkdn['r2_match'],
        'stage3_no_match':     db_bkdn['no_match'],
        'dedup_prev_seen':     len(recent_submissions),
        'dedup_n_new':         n_new,
        'dedup_n_resub':       n_resub,
        'dedup_n_blocked':     n_blocked,
        'notable_n':           len(notable10),
        'recent_submissions':  recent_submissions,  # generate_reports 側で save に使用
    }
    return candidates, notable10, stats


# ================================================================
# STEP 7. 注目候補10 選定ヘルパー
# ================================================================

def score_notable_item(ep: dict, item: dict, db_match: dict | None) -> int:
    """注目候補スコア計算（高いほど注目度高）"""
    score = 0
    bids  = item.get('bidCount', 0)
    grade = ep.get('grade_num', '')

    # 入札数（市場需要シグナル）
    score += bids * 15

    # グレード品位
    grade_pts = {
        'MS70': 35, 'PF70': 35, 'SP70': 35,
        'MS69': 25, 'PF69': 25, 'PR70': 25,
        'MS68': 18, 'PF68': 18,
        'MS67': 12, 'MS66':  9, 'MS65':  6, 'PF65':  6,
        'MS64':  3, 'MS63':  2,
    }
    score += grade_pts.get(grade, 0)

    # DB照合ありの場合は参照価格でボーナス
    if db_match:
        score += 20
        ref = db_match.get('sold_price_jpy', 0)
        if   ref >= 500_000: score += 20
        elif ref >= 300_000: score += 15
        elif ref >= 150_000: score += 10
        elif ref >=  80_000: score +=  5

    # 終了時刻（近いほど高優先）
    end_dt = item.get('itemEndDate', '')
    if end_dt:
        try:
            end  = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
            now  = datetime.now(timezone.utc)
            hrs  = (end - now).total_seconds() / 3600
            if   hrs < 24: score += 30
            elif hrs < 48: score += 18
            elif hrs < 72: score +=  8
        except Exception:
            pass

    return score


def make_notable_comment(ep: dict, item: dict, db_match: dict | None) -> str:
    """注目候補の自動コメント生成"""
    parts  = []
    bids   = item.get('bidCount', 0)
    grade  = ep.get('grade_num', '')
    cert   = ep.get('cert_company', '')
    denom  = ep.get('denomination', 'UNKNOWN')
    country= ep.get('country', 'UNKNOWN')

    # 入札状況
    if bids > 5:
        parts.append(f'🔥 入札{bids}件（競争激化）')
    elif bids > 0:
        parts.append(f'入札{bids}件あり（市場需要確認済）')
    else:
        parts.append('0入札（開幕前 or 閑散）')

    # グレード評価
    if grade in ('MS70', 'PF70', 'SP70'):
        parts.append(f'{cert} {grade} — 最高品位')
    elif grade in ('MS69', 'PF69', 'PR70'):
        parts.append(f'{cert} {grade} — 高品位・人気帯')
    elif grade in ('MS68', 'PF68'):
        parts.append(f'{cert} {grade} — 上位品位')
    elif grade:
        parts.append(f'{cert} {grade}')

    # 種別メモ
    if country != 'UNKNOWN' and denom != 'UNKNOWN':
        parts.append(f'{country} {denom}')
    elif country != 'UNKNOWN':
        parts.append(country)

    # DB参照
    if db_match:
        ref_jpy = db_match['sold_price_jpy']
        bl      = ref_jpy * (1 - YAHOO_FEE) * (1 - ROI_MIN) / USD_JPY
        usd     = float(item.get('price', {}).get('value', 0) or 0)
        parts.append(f'DB参照¥{ref_jpy:,} / 買い上限${bl:,.0f}')
        if usd > 0:
            status = '← 上限内' if usd <= bl else '← 現在超過'
            parts.append(f'現在${usd:,.2f} {status}')
    else:
        parts.append('DB照合なし（参照価格別途要調査）')

    # 終了時刻
    end_dt = item.get('itemEndDate', '')
    if end_dt:
        try:
            end  = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
            now  = datetime.now(timezone.utc)
            hrs  = (end - now).total_seconds() / 3600
            if   hrs <  0: parts.append('終了済み')
            elif hrs < 24: parts.append(f'⚠️ 本日終了（残{hrs:.0f}h）')
            elif hrs < 48: parts.append(f'明日終了（残{hrs:.0f}h）')
            else:          parts.append(f'残{hrs:.0f}h')
        except Exception:
            pass

    return ' / '.join(parts)


def is_notable_price_tier(ep: dict, db_match: dict | None) -> bool:
    """
    注目候補スコープ判定（入札候補より大幅に緩い）。

    DB照合あり  → ref_jpy ≥ PRICE_MIN で判定（厳格）
    DB照合なし  → material + denomination + grade で推定（緩め）

    ポリシー:
      - gold: 分数イーグル ($5/$10 Eagle = 1/10oz, 1/4oz以下) のみ除外。
              NGC/PCGS鑑定の金貨は基本¥10万以上帯 → それ以外は全て通す。
      - silver: HIGH_VALUE_SILVER_COMBOS の denomination × grade ルール OR
                denomination=UNKNOWN で MS64+ (Morgan/Peace等の古典米国銀貨)
      - platinum: 全通す

    Falseなら注目候補から除外（安価コインの穴埋め防止）。
    入札候補はこの関数を使わず、calc_cost() の tier=PASS/HOLD で判断する。
    """
    def _grade_num(g: str) -> int:
        try:
            return int(g[2:]) if g and len(g) >= 4 else 0
        except ValueError:
            return 0

    # DB照合あり → ref_jpy が基準
    if db_match:
        return db_match.get('sold_price_jpy', 0) >= PRICE_MIN

    # DB照合なし → material + denomination で推定
    material = ep.get('material', 'unknown')
    denom    = ep.get('denomination', 'UNKNOWN')
    grade    = ep.get('grade_num', '')
    gn       = _grade_num(grade)

    # ─────────────────────────────────────
    # GOLD
    # ─────────────────────────────────────
    if material == 'gold':
        # 除外: 分数イーグル (1/10oz = $5 Eagle, 1/4oz = $10 Eagle)
        # ※超小型金貨(1/25oz等)は既に$5 Eagle denomにマッピング済み
        if denom in ('$5 Eagle', '$10 Eagle'):
            return False

        # HIGH_VALUE_GOLD_DENOMS に入っている denominion → 原則スコープ内
        if denom in HIGH_VALUE_GOLD_DENOMS:
            # Sovereign / 20 Francs は境界帯 → grade 60+ で含める（注目候補は緩め）
            if denom in ('Sovereign', '20 Francs'):
                return gn >= 60
            # Half Eagle は境界帯 → grade 55+ (AU55+) で含める
            if denom == 'Half Eagle':
                return gn >= 55
            return True

        # denomination UNKNOWN の gold (古典金貨でDENOM_PATTERNSにない銘柄等)
        # NGC/PCGS鑑定の金貨はAU50以上あれば¥10万以上帯がほとんど
        if denom == 'UNKNOWN':
            return gn >= 55   # AU55+

        # その他の gold denomination (Panda 等) → HIGH_VALUE_GOLD_DENOMSに未登録
        # DB照合なし + 明示リスト外 → 除外（スコープ不明確）
        return False

    # ─────────────────────────────────────
    # SILVER
    # ─────────────────────────────────────
    elif material == 'silver':
        # HIGH_VALUE_SILVER_COMBOS の denomination × grade ルール
        if denom in HIGH_VALUE_SILVER_COMBOS:
            coin_year = ep.get('year')
            # Morgan Dollar / Peace Dollar の現代リバイバル版 (2021+) は廉価 → 除外
            # 原産: Morgan 1878-1921, Peace 1921-1935。2021+ は面白いが日本では¥2万未満
            if denom in ('Morgan Dollar', 'Peace Dollar'):
                if coin_year and coin_year >= 2021:
                    return False
            return gn >= HIGH_VALUE_SILVER_COMBOS[denom]

        # denomination UNKNOWN の silver: MS70/PF70のみ → 完全品位の未判定銀貨
        # Silver Eagle は '$50 Eagle' denomに解析されるため、ここには来ない
        # Eisenhower/Washington Quarter等の安価コインがPF69で通過するのを防ぐため閾値70
        if denom == 'UNKNOWN':
            return gn >= 70

        # それ以外の silver denomination ($50 Eagle等、HIGH_VALUE_SILVER_COMBOSに未登録) → 除外
        return False

    # ─────────────────────────────────────
    # PLATINUM
    # ─────────────────────────────────────
    elif material == 'platinum':
        return True   # NGC/PCGS鑑定platinum は全て高額帯

    return False


def make_purchase_condition(ep: dict, item: dict,
                            db_match: dict | None, match_type: str | None) -> str:
    """
    「どういう条件なら購入できるか」または「なぜ今は見送りか」を生成。
    DB比較コメント + 購入条件を1〜3行で返す。
    """
    usd    = float(item.get('price', {}).get('value', 0) or 0)
    bids   = item.get('bidCount', 0)
    grade  = ep.get('grade_num', '')
    cert   = ep.get('cert_company', '')
    e_year = ep.get('year')

    lines = []

    if db_match is None:
        lines.append('当社DBに類似コインなし → Yahoo相場が取れないため買い上限算出不可。'
                     '日本市場相場を別途調査すれば入札可能性あり。')
        if bids > 5:
            lines.append(f'入札{bids}件の競争があるため相場感の把握に価値あり（市場調査対象）。')
        return '\n'.join(lines)

    ref_jpy = db_match['sold_price_jpy']
    bl_usd  = ref_jpy * (1 - YAHOO_FEE) * (1 - ROI_MIN) / USD_JPY
    d_grade = db_match.get('grade_num', '')
    d_year  = db_match.get('year')
    d_title = db_match.get('lot_title', '')[:50]
    cert_no = db_match.get('cert_number', '') or ''

    # DB照合状況
    if match_type == 'R1_exact':
        lines.append(f'✅ DB完全一致（{cert} {grade} / 年号・denomination一致）。'
                     f'参照価格¥{ref_jpy:,}（Yahoo落札実績）。')
    elif match_type == 'R2_year':
        lines.append(f'⚠️ DB年号違い一致：eBay={e_year}年 / DB={d_year}年（同グレード {grade}）。'
                     f'DB参照¥{ref_jpy:,}は{d_year}年実績。年号差は価値差あり、参照価格は目安。')
    elif match_type == 'R2_grade':
        lines.append(f'⚠️ DBグレード違い一致：eBay={grade} / DB={d_grade}（同年 {e_year}年）。'
                     f'DB参照¥{ref_jpy:,}は{d_grade}実績。グレード差分の価値調整が必要。')

    # 購入条件
    # 買い上限: ROI≥15% かつ 利益≥¥20,000 の両方を満たす上限
    bl_roi    = ref_jpy * (1 - YAHOO_FEE) * (1 - ROI_MIN) / USD_JPY
    bl_profit = (ref_jpy * (1 - YAHOO_FEE) - PROFIT_MIN - FWD_JPY - DOM_JPY) / (USD_JPY * IMPORT_DUTY)
    bl_usd    = min(bl_roi, bl_profit)

    if usd <= 0 and bids == 0:
        lines.append(f'現在0入札。落札価格が${bl_usd:,.0f}以下なら ROI≥15%・利益≥¥{PROFIT_MIN:,}を同時達成。'
                     f'【買い上限: ${bl_usd:,.0f}】')
    elif usd <= 0:
        lines.append(f'現在価格不明（{bids}入札済み）。落札価格が${bl_usd:,.0f}以下なら購入検討可。'
                     f'【買い上限: ${bl_usd:,.0f} (ROI≥15%・利益≥¥{PROFIT_MIN:,}同時条件)】')
    elif usd <= bl_usd:
        margin = bl_usd - usd
        lines.append(f'✅ 現在${usd:,.0f} ≤ 買い上限${bl_usd:,.0f}（ROI≥15%・利益≥¥{PROFIT_MIN:,}条件）。'
                     f'残り余裕${margin:,.0f}。追加入札余地あり → 入札候補昇格検討を推奨。')
    else:
        gap = usd - bl_usd
        lines.append(f'❌ 現在${usd:,.0f}は買い上限${bl_usd:,.0f}を${gap:,.0f}超過。'
                     f'【条件: ROI≥15%・利益≥¥{PROFIT_MIN:,}の両立】'
                     f'価格が下がるか、より高い年号/グレードの参照価格が確保できれば購入可能。')

    if cert_no:
        lines.append(f'参照DB cert番号: {cert_no}（{d_title}）')

    return '\n'.join(lines)


# ================================================================
# STEP 7-B. 注目候補スコアリング（CEO設計 4軸 100点満点）
# ================================================================

def score_profit_axis(profit_jpy) -> int:
    """利益見込みスコア (0-40)。profit_jpy=None→算定保留(暫定5点)"""
    if profit_jpy is None:   return 5
    if profit_jpy >= 70_000: return 40
    if profit_jpy >= 50_000: return 34
    if profit_jpy >= 30_000: return 28
    if profit_jpy >= 20_000: return 22
    if profit_jpy > 0:       return 10
    return 0


def score_db_axis(match_type) -> int:
    """DB類似スコア (0-30): R1完全一致=30 / R2近傍=18 / なし=0"""
    if match_type == 'R1_exact':              return 30
    if match_type in ('R2_year', 'R2_grade'): return 18
    return 0


def score_bids_axis(bids: int) -> int:
    """入札数スコア (0-20)"""
    if bids >= 21: return 20
    if bids >= 11: return 16
    if bids >= 6:  return 12
    if bids >= 3:  return 8
    if bids >= 1:  return 5
    return 2


def score_yahoo_ref_axis(ref_jpy) -> int:
    """ヤフオク参照価格スコア (0-10)"""
    if ref_jpy is None:     return 0
    if ref_jpy >= 500_000:  return 10
    if ref_jpy >= 300_000:  return 8
    if ref_jpy >= 200_000:  return 6
    if ref_jpy >= 100_000:  return 4
    return 0


def notable_score_100(profit_jpy, match_type, bids: int, ref_jpy) -> int:
    """注目候補 総合スコア 100点満点 (CEO設計 4軸)"""
    return (score_profit_axis(profit_jpy)
            + score_db_axis(match_type)
            + score_bids_axis(bids)
            + score_yahoo_ref_axis(ref_jpy))


def profit_label(profit_jpy) -> str:
    """利益見込み固定語彙: 2万円以上可 / 薄利 / 2万円未満 / 赤字懸念 / 算定保留"""
    if profit_jpy is None:   return '算定保留'
    if profit_jpy >= 20_000: return '2万円以上可'
    if profit_jpy >= 10_000: return '薄利'
    if profit_jpy > 0:       return '2万円未満'
    return '赤字懸念'


def db_label(match_type) -> str:
    """DB類似 固定語彙: あり / 近傍のみ / なし"""
    if match_type == 'R1_exact':              return 'あり'
    if match_type in ('R2_year', 'R2_grade'): return '近傍のみ'
    return 'なし'


def make_compact_comment(ep: dict, item: dict,
                         db_match, match_type, profit_jpy) -> str:
    """40字前後コメント: なぜ注目か / DB状況 / 条件付きBUY"""
    bids  = item.get('bidCount', 0)
    cert  = ep.get('cert_company', '')
    grade = ep.get('grade_num', '')
    usd   = float(item.get('price', {}).get('value', 0) or 0)

    parts = []

    # なぜ注目か
    if bids >= 10:
        parts.append(f'入札{bids}件競争激化')
    elif bids >= 3:
        parts.append(f'入札{bids}件需要あり')
    elif bids > 0:
        parts.append(f'入札{bids}件')
    else:
        parts.append(f'{cert} {grade}市場観察')

    # DB状況
    if db_match:
        ref = db_match.get('sold_price_jpy', 0)
        bl  = ref * (1 - YAHOO_FEE) * (1 - ROI_MIN) / USD_JPY
        kind = 'DB一致' if match_type == 'R1_exact' else 'DB近傍'
        parts.append(f'{kind}¥{ref // 10000}万')
        if usd > 0:
            st = '上限内' if usd <= bl else '現在超過'
            parts.append(f'${usd:,.0f}/{st}')
    else:
        parts.append('DB照合なし・要相場調査')

    # 条件付きBUY
    if profit_jpy is not None and profit_jpy >= 20_000:
        parts.append('→入札候補化可')
    elif db_match and profit_jpy is not None and profit_jpy > 0:
        parts.append('→条件次第でBUY')

    raw = '。'.join(parts)
    return raw[:45] + '…' if len(raw) > 47 else raw


# ================================================================
# STEP 7-C. 第2段階: スコープ判定（DB照合なしでは落とさない）
# ================================================================

def scope_filter_stage2(parsed_items: list, excluded_item_ids: set) -> tuple:
    """
    第2段階: 当社スコープ判定（緩め設定、目標120〜180件）。

    ルール（Stage3より大幅に緩和）:
      - DB完全一致なしでは落とさない（DB_NO_MATCHは除外理由にしない）
      - Gold: $5 Eagle・$10 Eagle（超小型分数）のみ除外。他全通過
      - Silver: grade >= 60 OR HIGH_VALUE_SILVER_COMBOS に含まれる denomination × grade
                $50 Eagle(Silver Eagle) = grade >= 67 で通過（key date等）
                denomination UNKNOWN = grade >= 65
      - Platinum: 全通過
      - material UNKNOWN: 除外

    Returns:
        (passed: list[dict], breakdown: dict)
    """
    def _gn(grade: str) -> int:
        try:
            return int(grade[2:]) if grade and len(grade) >= 4 else 0
        except ValueError:
            return 0

    passed:  list = []
    n_cand  = 0
    n_scope = 0

    for ep in parsed_items:
        item = ep['_item']
        if id(item) in excluded_item_ids:
            n_cand += 1
            continue

        material = ep.get('material', 'unknown')
        denom    = ep.get('denomination', 'UNKNOWN')
        grade    = ep.get('grade_num', '')
        gn       = _gn(grade)

        ok = False

        if material == 'gold':
            # 超小型分数イーグルのみ除外
            if denom not in ('$5 Eagle', '$10 Eagle'):
                ok = True

        elif material == 'silver':
            # $50 Eagle (Silver Eagle): key date は高額 → grade 65+ で通過
            if denom == '$50 Eagle':
                ok = (gn >= 65)
            # 高額帯銀貨リスト: Stage3より4pt緩め
            elif denom in HIGH_VALUE_SILVER_COMBOS:
                threshold = HIGH_VALUE_SILVER_COMBOS[denom]
                # Morgan/Peace 2021以降は現代復刻版で安価
                if denom in ('Morgan Dollar', 'Peace Dollar'):
                    year = ep.get('year')
                    if year and year >= 2021:
                        ok = False
                    else:
                        ok = (gn >= max(58, threshold - 4))
                else:
                    ok = (gn >= max(58, threshold - 4))
            # denomination UNKNOWN: grade 63+ で通過
            elif denom == 'UNKNOWN':
                ok = (gn >= 63)
            # その他の silver denomination → grade 60+ で通過
            else:
                ok = (gn >= 60)

        elif material == 'platinum':
            ok = True   # platinum は全通過

        # material UNKNOWN は除外

        if ok:
            passed.append(ep)
        else:
            n_scope += 1

    breakdown = {
        'input':         len(parsed_items),
        'excluded_cand': n_cand,
        'failed_scope':  n_scope,
        'passed':        len(passed),
    }
    return passed, breakdown


# ================================================================
# STEP 7-D. 第3段階: DB照合 + スコアリング → Top20/50/100
# ================================================================

def score_and_tier_stage3(scope_passed: list, db_coins: list,
                          count: int = 100) -> tuple:
    """
    第3段階: 各候補に対しDB照合を実施し100点満点スコアで並べ替え。
    - DB照合なしでもスコアリング対象 (DB_NO_MATCH → score_db_axis=0)
    - 上位 count 件を返す

    Returns:
        (scored_top: list[tuple], db_breakdown: dict)
        scored_top 要素: (score, ep, item, db_match|None, match_type|None, profit_jpy|None)
    """
    scored: list = []
    db_breakdown = {'r1_match': 0, 'r2_match': 0, 'no_match': 0}

    for ep in scope_passed:
        item = ep['_item']

        # DB照合
        db_match   = None
        match_type = None
        r1 = match_round1(ep, db_coins)
        if r1:
            db_match   = max(r1, key=lambda d: d['sold_price_jpy'])
            match_type = 'R1_exact'
            db_breakdown['r1_match'] += 1
        else:
            r2y = match_round2(ep, db_coins, 'year_delta')
            r2g = match_round2(ep, db_coins, 'grade_delta')
            all_r2 = r2y + r2g
            if all_r2:
                db_match = max(all_r2, key=lambda d: d['sold_price_jpy'])
                match_type = 'R2_year' if (db_match in r2y) else 'R2_grade'
                db_breakdown['r2_match'] += 1
            else:
                db_breakdown['no_match'] += 1

        # 利益計算（4軸スコア用）
        bids    = item.get('bidCount', 0)
        usd     = float(item.get('price', {}).get('value', 0) or 0)
        ref_jpy = db_match['sold_price_jpy'] if db_match else None
        if db_match and usd > 0:
            profit_jpy = calc_cost(usd, ref_jpy)['profit_jpy']
        else:
            profit_jpy = None

        # 100点満点スコア（CEO設計 4軸）
        score = notable_score_100(profit_jpy, match_type, bids, ref_jpy)
        scored.append((score, ep, item, db_match, match_type, profit_jpy))

    scored.sort(key=lambda x: -x[0])
    return scored[:count], db_breakdown


# ================================================================
# STEP 7-E. 注目候補100 選定（後方互換ラッパー）
# ================================================================

def select_notable_n(parsed_items: list, candidates: list,
                     db_coins: list, count: int = 100) -> list:
    """
    入札候補以外の parsed_items からスコア順に上位 count 件を選ぶ。
    各要素: (score, ep, item, db_match or None, match_type str)
      match_type: None | 'R1_exact' | 'R2_year' | 'R2_grade'
    """
    cand_ids = {id(c['item']) for c in candidates}
    scored   = []

    for ep in parsed_items:
        item = ep['_item']
        if id(item) in cand_ids:
            continue

        # DB照合（注目候補向け・match_type付き）
        db_match   = None
        match_type = None
        r1 = match_round1(ep, db_coins)
        if r1:
            db_match   = max(r1, key=lambda d: d['sold_price_jpy'])
            match_type = 'R1_exact'
        else:
            r2y = match_round2(ep, db_coins, 'year_delta')
            r2g = match_round2(ep, db_coins, 'grade_delta')
            all_r2 = r2y + r2g
            if all_r2:
                db_match = max(all_r2, key=lambda d: d['sold_price_jpy'])
                match_type = 'R2_year' if (db_match in r2y) else 'R2_grade'

        # ★ 価格帯ガード: ¥10万未満推定コインは注目候補から除外
        if not is_notable_price_tier(ep, db_match):
            continue

        # 利益計算（4軸スコア用）
        bids    = item.get('bidCount', 0)
        usd     = float(item.get('price', {}).get('value', 0) or 0)
        ref_jpy = db_match['sold_price_jpy'] if db_match else None
        if db_match and usd > 0:
            profit_jpy = calc_cost(usd, ref_jpy)['profit_jpy']
        else:
            profit_jpy = None  # 価格未確定 or DB照合なし

        # 100点満点スコア（CEO設計 4軸）
        score = notable_score_100(profit_jpy, match_type, bids, ref_jpy)
        scored.append((score, ep, item, db_match, match_type, profit_jpy))

    scored.sort(key=lambda x: -x[0])
    return scored[:count]


# ================================================================
# STEP 8. Phase B: レポート生成（A/B/C/D 4提出物構造）
# ================================================================

def generate_reports(candidates: list, notable10: list,
                     stats: dict, days: int = 7) -> tuple:
    """
    4提出物を生成:
      A: 日次サマリ
      B: 入札候補リスト (PASS/HOLD, 0-5件)
      C: 注目候補10 (毎日必ず10件)
      D: WATCH一覧 (0入札・価格未確定)
    """
    today    = datetime.now().strftime('%Y-%m-%d')
    today_nd = today.replace('-', '')

    bid_list   = [c for c in candidates if c['tier'] in ('PASS', 'HOLD')]
    watch_list = [c for c in candidates if c['tier'] == 'WATCH']
    pass_list  = [c for c in candidates if c['tier'] == 'PASS']
    hold_list  = [c for c in candidates if c['tier'] == 'HOLD']

    # ─────────────────────────────────────────
    # 提出物A: 日次サマリ
    # ─────────────────────────────────────────
    path_a = OUTPUT_DIR / f'{today_nd}_A_summary.md'
    with open(path_a, 'w', encoding='utf-8') as f:
        f.write(f'# 提出物A: 日次スキャンサマリ\n')
        f.write(f'**日付**: {today}  |  直近{days}日以内の新規出品\n\n')

        f.write(f'## スキャン件数\n\n')
        f.write(f'| 項目 | 値 |\n|------|----|\n')
        f.write(f'| 【第1段階】6バケット生取得 | {stats["r1_raw"]}件 |\n')
        f.write(f'| 　└ 除外後（スキャン対象） | {stats["parsed"]}件 |\n')
        f.write(f'| 【第2段階】スコープ通過 | {stats.get("stage2_passed", "—")}件 |\n')
        f.write(f'| 　└ スコープ外除外 | {stats.get("stage2_failed", "—")}件 |\n')
        f.write(f'| 【第3段階】DB照合 R1完全一致 | {stats.get("stage3_r1", "—")}件 |\n')
        f.write(f'| 　└ DB照合 R2近傍 | {stats.get("stage3_r2", "—")}件 |\n')
        f.write(f'| 　└ DB照合なし | {stats.get("stage3_no_match", "—")}件 |\n')
        f.write(f'| Staging参照件数(DB) | {stats["db_coins"]}件 |\n')
        f.write(f'| 入札候補選定 ROUND1ヒット | {stats["r1_hits"]}件 |\n')
        f.write(f'| 入札候補選定 ROUND2ヒット | {stats["r2_hits"]}件 |\n')
        f.write(f'| 【重複除外】直近{DEDUP_DAYS}日提出済み | {stats.get("dedup_prev_seen","—")}件 |\n')
        f.write(f'| 　└ 今回NEW | {stats.get("dedup_n_new","—")}件 |\n')
        f.write(f'| 　└ 再提出許可 | {stats.get("dedup_n_resub","—")}件 |\n')
        f.write(f'| 　└ 重複ブロック | {stats.get("dedup_n_blocked","—")}件 |\n\n')

        # Top100未達の場合は内訳を表示
        if stats.get('notable_n', 0) < 100:
            f.write(f'## ⚠️ Top100未達 — 段階別内訳\n\n')
            f.write(f'| 段階 | 件数 | 備考 |\n|------|------|------|\n')
            f.write(f'| 第1段階 生取得 | {stats["r1_raw"]}件 | 6バケット合計 |\n')
            f.write(f'| 第1段階 除外後 | {stats["parsed"]}件 | EXCLUDE_KW / 発送元 / non-NGC/PCGS |\n')
            f.write(f'| 第2段階 スコープ通過 | {stats.get("stage2_passed","—")}件 | 価格帯ガード（gold/silver/platinum別基準） |\n')
            f.write(f'| 第3段階 注目候補 | {stats.get("notable_n","—")}件 | Top100={min(100, stats.get("notable_n",0))}件達成 |\n\n')
            if stats.get('stage2_passed', 0) < 120:
                f.write('> **原因推定**: 第2段階スコープ通過が120件未満。limit_per_bucketの増加またはスコープ基準の緩和を検討。\n\n')
            elif stats.get('notable_n', 0) < 100:
                f.write('> **原因推定**: 第2段階は十分だが第3段階で絞り込まれた。スコアリング基準の見直しを検討。\n\n')

        f.write(f'## 成果物件数\n\n')
        f.write(f'| 区分 | 件数 | 目標 |\n|------|------|------|\n')
        _t20  = min(20, len(notable10))
        _t50  = min(30, max(0, len(notable10) - 20))
        _t100 = max(0, len(notable10) - 50)
        _blocked = stats.get('dedup_n_blocked', 0)
        _resub   = stats.get('dedup_n_resub', 0)
        f.write(f'| 🔥 **入札候補** (PASS ROI≥{ROI_MIN*100:.0f}%) | **{len(pass_list)}件** | 1〜5件 |\n')
        f.write(f'| ⚠️ HOLD (ROI不足・要判断) | {len(hold_list)}件 | — |\n')
        f.write(f'| 🔥 **注目候補 Top20** | **{_t20}件** | 毎日20件必須 |\n')
        f.write(f'| ⚠️ 注目候補 Top50 | {_t50}件 | 時間があれば確認 |\n')
        f.write(f'| 👀 注目候補 Top100 | {_t100}件 | 市場監視母集団 |\n')
        f.write(f'| 🚫 重複ブロック | {_blocked}件 | CEO提出済みブロック |\n')
        f.write(f'| 🔄 再提出許可 | {_resub}件 | 重要変化あり再提出 |\n')
        f.write(f'| 👀 WATCH (0入札・価格未確定) | {len(watch_list)}件 | 20件目安 |\n\n')

        f.write(f'## 本日の結論\n\n')
        if bid_list:
            f.write(f'✅ **入札候補 {len(bid_list)}件あり** → 提出物B参照\n')
        else:
            f.write(f'❌ **即入札候補 0件**\n')

        _t20c = min(20, len(notable10))
        _t50c = min(30, max(0, len(notable10) - 20))
        _t100c = max(0, len(notable10) - 50)
        f.write(f'👁 注目候補 {len(notable10)}件 '
                f'(Top20:{_t20c} / Top50追加:{_t50c} / Top100追加:{_t100c}) → 提出物C参照\n')
        f.write(f'👀 WATCH {len(watch_list)}件 → 提出物D参照\n\n')

        if not bid_list:
            f.write('> **NO BUY（即入札）** — ただし注目候補・WATCHの継続監視を推奨\n')

    # ─────────────────────────────────────────
    # 提出物B: 入札候補リスト (PASS/HOLD)
    # ─────────────────────────────────────────
    path_b = OUTPUT_DIR / f'{today_nd}_B_bid_candidates.md'
    with open(path_b, 'w', encoding='utf-8') as f:
        f.write(f'# 提出物B: 入札候補リスト\n')
        f.write(f'**日付**: {today}  |  PASS: {len(pass_list)}  HOLD: {len(hold_list)}\n\n')

        if not bid_list:
            f.write('> 本日の入札候補は0件です。注目候補（提出物C）・WATCH（提出物D）を参照してください。\n')
        else:
            tier_emoji = {'PASS': '🔥', 'HOLD': '⚠️'}
            for idx, c in enumerate(bid_list, 1):
                item    = c['item']
                ep      = c['ep']
                ref_db  = c['ref_db']
                usd     = c['usd']
                bids    = c['bids']
                tier    = c['tier']
                roi     = c['roi']
                profit  = c['profit_jpy']
                total   = c['total_jpy']
                bl      = c['bl_usd']
                ref_jpy = c['ref_jpy']
                rnd     = f'ROUND{c["round"]}({c["variant"]})'
                url     = item.get('itemWebUrl', item.get('itemHref', ''))
                end_dt  = (item.get('itemEndDate', '')[:16]
                           if item.get('itemEndDate') else '?')
                loc     = item.get('itemLocation', {}).get('country', '?')
                opts    = ','.join(item.get('buyingOptions', []))
                em      = tier_emoji.get(tier, '')
                cert_no = ref_db.get('cert_number', '') or '—'

                f.write(f'## 入札候補 {idx}  [{em} {tier}]  [{rnd}]\n\n')
                f.write(f'| 項目 | 値 |\n|------|----|\n')
                f.write(f'| タイトル | {item.get("title","")[:75]} |\n')
                f.write(f'| URL | {url[:80]} |\n')
                f.write(f'| 現在価格 | ${usd:,.2f} |\n')
                f.write(f'| 入札数 | {bids} |\n')
                f.write(f'| 形式 | {opts} |\n')
                f.write(f'| 終了日時 | {end_dt} |\n')
                f.write(f'| 発送元 | {loc} |\n')
                f.write(f'| DB参照価格(Yahoo落札) | ¥{ref_jpy:,} |\n')
                f.write(f'| 総費用 | ¥{total:,} |\n')
                f.write(f'| 買い上限 | ${bl:,.0f} |\n')
                f.write(f'| 想定利益 | ¥{profit:,} |\n')
                f.write(f'| ROI | {roi*100:.1f}% |\n')
                f.write(f'| eBay鑑定会社(解析) | {ep["cert_company"]} |\n')
                f.write(f'| eBayグレード(解析) | {ep["grade_num"]} |\n')
                f.write(f'| eBay国(解析) | {ep["country"]} |\n')
                f.write(f'| eBay denomination | {ep["denomination"]} |\n')
                f.write(f'| DB参照グレード | {ref_db.get("grade_num","")} |\n')
                f.write(f'| DB参照年号 | {ref_db.get("year","")} |\n')
                f.write(f'| cert番号(照合用) | {cert_no} |\n\n')
                f.write(f'**主リスク**: \n\n')
                f.write(f'**CAPコメント**: \n\n---\n\n')

    # ─────────────────────────────────────────
    # 提出物C: 注目候補 Top20/50/100 (3段階)
    # ─────────────────────────────────────────
    path_c = OUTPUT_DIR / f'{today_nd}_C_notable.md'
    n_notable = len(notable10)
    top20  = notable10[:20]
    top50  = notable10[20:50]
    top100 = notable10[50:100]

    # 統計（タプルは (score, ep, item, db_match, match_type, profit_jpy)）
    def _e(entry, idx, default=None):
        return entry[idx] if len(entry) > idx else default

    prof_ok = sum(1 for e in notable10
                  if _e(e, 5) is not None and _e(e, 5) >= 20_000)
    db_ok   = sum(1 for e in notable10 if _e(e, 3) is not None)
    bids_0  = sum(1 for e in notable10 if e[2].get('bidCount', 0) == 0)
    pending = sum(1 for e in notable10 if _e(e, 5) is None)

    TABLE_HEADER = (
        '| Rank | コイン | cert+grade | 入札 | ヤフ参照 | '
        '利益見込み | DB類似 | 管理番号 | コメント | Score |\n'
        '|------|--------|-----------|------|---------|'
        '----------|--------|---------|---------|-------|\n'
    )

    def _row(rank_global: int, entry) -> str:
        score      = entry[0]
        ep         = entry[1]
        item       = entry[2]
        db_match   = _e(entry, 3)
        match_type = _e(entry, 4)
        profit_jpy = _e(entry, 5)

        title = item.get('title', '')
        url   = item.get('itemWebUrl', item.get('itemHref', ''))
        bids  = item.get('bidCount', 0)
        cert  = ep.get('cert_company', '')
        grade = ep.get('grade_num', '')
        short = title[:27] + '…' if len(title) > 29 else title

        ref_jpy  = db_match['sold_price_jpy'] if db_match else None
        ref_str  = f'¥{ref_jpy:,}' if ref_jpy else '—'
        mgmt_no  = (db_match.get('cert_number', '') or '—') if db_match else '—'
        plabel   = profit_label(profit_jpy)
        dlabel   = db_label(match_type)
        comment  = make_compact_comment(ep, item, db_match, match_type, profit_jpy)

        return (f'| {rank_global} | [{short}]({url}) '
                f'| {cert} {grade} | {bids} | {ref_str} '
                f'| {plabel} | {dlabel} | {mgmt_no} | {comment} | {score} |\n')

    def _url_list(entries, start: int) -> str:
        lines = []
        for i, entry in enumerate(entries, start):
            ep    = entry[1]
            item  = entry[2]
            url   = item.get('itemWebUrl', item.get('itemHref', ''))
            title = item.get('title', '')[:60]
            cert  = ep.get('cert_company', '')
            grade = ep.get('grade_num', '')
            bids  = item.get('bidCount', 0)
            lines.append(f'{i}. [{title}]({url})  `{cert} {grade}`  入札{bids}件')
        return '\n'.join(lines) + '\n'

    with open(path_c, 'w', encoding='utf-8') as f:
        f.write(f'# 提出物C: 注目候補 Top20/50/100\n')
        f.write(f'**日付**: {today}  |  朝9時更新  |  総スコア100点満点\n\n')

        # 1ページ目サマリ（CEO 5分レビュー用）
        f.write('---\n\n')
        f.write('## ① 今日の要点\n\n')
        f.write(f'- 注目候補総数: **{n_notable}件** '
                f'(Top20: {len(top20)}件 / Top50追加: {len(top50)}件 / Top100追加: {len(top100)}件)\n')
        f.write(f'- 利益2万円以上可: **{prof_ok}件**\n')
        f.write(f'- DB類似あり/近傍: **{db_ok}件**\n')
        f.write(f'- 0入札(WATCH層): {bids_0}件\n')
        f.write(f'- 算定保留(DB照合なし等): {pending}件\n\n')

        f.write('## ② 優先順位サマリ\n\n')
        f.write('| 区分 | 件数 | 用途 |\n|------|------|------|\n')
        f.write(f'| 🔥 Top 20 | {len(top20)}件 | 今日CEOが必ず見る候補 |\n')
        f.write(f'| ⚠️ Top 50 | {len(top50)}件 | 時間があれば確認 |\n')
        f.write(f'| 👀 Top 100 | {len(top100)}件 | 市場全体の監視母集団 |\n\n')
        f.write('---\n\n')

        # ─── Top 20 ───
        f.write('## 🔥 Top 20 — 今日いちばん見るべき候補\n\n')
        if top20:
            f.write(TABLE_HEADER)
            for i, entry in enumerate(top20, 1):
                f.write(_row(i, entry))
            f.write('\n')
            f.write('### URL一覧 (Top 20)\n\n')
            f.write(_url_list(top20, 1))
        else:
            f.write('> Top20の抽出に失敗しました。\n')
        f.write('\n---\n\n')

        # ─── Top 50 ───
        f.write('## ⚠️ Top 50 — 時間があれば確認する候補\n\n')
        if top50:
            f.write(TABLE_HEADER)
            for i, entry in enumerate(top50, 21):
                f.write(_row(i, entry))
            f.write('\n')
            f.write('### URL一覧 (Top 50)\n\n')
            f.write(_url_list(top50, 21))
        else:
            f.write('> Top50(21-50位)の候補が不足しています。\n')
        f.write('\n---\n\n')

        # ─── Top 100 ───
        f.write('## 👀 Top 100 — 市場全体の監視母集団\n\n')
        if top100:
            f.write(TABLE_HEADER)
            for i, entry in enumerate(top100, 51):
                f.write(_row(i, entry))
            f.write('\n')
            f.write('### URL一覧 (Top 100)\n\n')
            f.write(_url_list(top100, 51))
        else:
            f.write('> Top100(51-100位)の候補が不足しています。\n')
        f.write('\n---\n\n')

        # CEO結論
        f.write('## CEO向け結論\n\n')
        if pass_list:
            f.write(f'✅ **入札候補 {len(pass_list)}件あり** → 提出物B参照\n\n')
        else:
            f.write('❌ 即入札候補: 0件\n\n')
        f.write(f'👁 注目候補: Top20={len(top20)}件 / '
                f'Top50(追加)={len(top50)}件 / Top100(追加)={len(top100)}件\n')
        if prof_ok > 0:
            f.write(f'💰 利益2万円以上可: {prof_ok}件\n')
        f.write('\n> **NO BUY（即入札）** — 注目候補・市場観察継続推奨\n')

    # ─────────────────────────────────────────
    # 提出物D: WATCH一覧
    # ─────────────────────────────────────────
    path_d = OUTPUT_DIR / f'{today_nd}_D_watch.md'
    with open(path_d, 'w', encoding='utf-8') as f:
        f.write(f'# 提出物D: WATCH一覧\n')
        f.write(f'**日付**: {today}  |  WATCH: {len(watch_list)}件\n\n')
        f.write(f'> 0入札・価格未確定・cert未確認など。次回に入札候補/注目候補へ昇格候補。\n\n')

        if not watch_list:
            f.write('本日のWATCH候補は0件。\n')
        else:
            f.write('| # | タイトル | 形式 | 終了 | 入札 | cert | grade | DB参照 | 買い上限 | 備考 |\n')
            f.write('|---|---------|------|------|------|------|-------|--------|----------|------|\n')
            for idx, c in enumerate(watch_list, 1):
                item    = c['item']
                ep      = c['ep']
                ref_db  = c['ref_db']
                title60 = item.get('title', '')[:55]
                end_dt  = (item.get('itemEndDate', '')[:10]
                           if item.get('itemEndDate') else '?')
                opts    = '+'.join(item.get('buyingOptions', []))
                bids    = c['bids']
                cert    = ep.get('cert_company', '?')
                grade   = ep.get('grade_num', '?')
                ref_jpy = c['ref_jpy']
                bl      = c['bl_usd']
                url     = item.get('itemWebUrl', item.get('itemHref', ''))
                rnd     = f'R{c["round"]}({c["variant"][:4]})'

                f.write(f'| {idx} | [{title60}]({url[:70]}) | {opts} | {end_dt} | {bids} '
                        f'| {cert} | {grade} | ¥{ref_jpy:,} | ${bl:,.0f} | {rnd} |\n')

            f.write('\n')
            f.write('## 次回昇格候補\n\n')
            # 昇格候補 = 入札ありに変わったら即ROUND評価対象
            r2_watch = [c for c in watch_list if c['round'] == 2]
            r1_watch = [c for c in watch_list if c['round'] == 1]
            if r1_watch:
                f.write(f'**ROUND1 WATCH ({len(r1_watch)}件)** — 入札開始次第 PASS/HOLD評価可能\n\n')
                for c in r1_watch[:5]:
                    f.write(f'- {c["item"].get("title","")[:60]}\n')
                f.write('\n')
            if r2_watch:
                f.write(f'**ROUND2 WATCH ({len(r2_watch)}件)** — 年号/グレード差あり、参照価格要精査\n\n')
                for c in r2_watch[:5]:
                    f.write(f'- {c["item"].get("title","")[:60]}\n')
                f.write('\n')

    # ─────────────────────────────────────────
    # CEO確認台帳 upsert（重複管理ログ保存）
    # ─────────────────────────────────────────
    recent_submissions = stats.get('recent_submissions', {})
    saved_n = save_ceo_review_entries(
        notable_items=notable10,
        candidates=candidates,
        scan_date=today,
        recent_submissions=recent_submissions,
        verbose=True,
    )

    print(f'\n[Phase B] レポート出力 (4提出物):')
    print(f'  A: {path_a.name}')
    print(f'  B: {path_b.name}')
    print(f'  C: {path_c.name}  (注目候補{n_notable}件: Top20={len(top20)} / Top50追加={len(top50)} / Top100追加={len(top100)})')
    print(f'  D: {path_d.name}')
    if saved_n:
        print(f'  📋 ceo_review_log: {saved_n}件 保存')
    return path_a, path_b, path_c, path_d


# ================================================================
# MAIN
# ================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days',        type=int,  default=7)
    ap.add_argument('--limit',       type=int,  default=200)
    ap.add_argument('--no-auction',  action='store_true')
    ap.add_argument('--quiet',       action='store_true')
    args = ap.parse_args()

    verbose = not args.quiet
    candidates, notable10, stats = run_phase_a(
        days=args.days,
        limit_per_bucket=args.limit,
        auction_only=not args.no_auction,
        verbose=verbose,
    )
    if verbose:
        bid_n   = sum(1 for c in candidates if c['tier'] in ('PASS','HOLD'))
        watch_n = sum(1 for c in candidates if c['tier'] == 'WATCH')
        print(f'  入札候補: {bid_n}件 / 注目候補: {len(notable10)}件 / WATCH: {watch_n}件')

    generate_reports(candidates, notable10, stats, days=args.days)

    with open(BASE_DIR / 'docs' / 'dev_progress.log', 'a', encoding='utf-8') as f:
        today = datetime.now().strftime('%Y-%m-%d')
        f.write(f'{today} daily_scan 完了: '
                f'PASS={sum(1 for c in candidates if c["tier"]=="PASS")} '
                f'HOLD={sum(1 for c in candidates if c["tier"]=="HOLD")} '
                f'WATCH={sum(1 for c in candidates if c["tier"]=="WATCH")} '
                f'注目候補={len(notable10)}\n')


if __name__ == '__main__':
    main()
