"""eBay vs Yahoo 価格差自動探索スクリプト

Supabaseのmarket_transactions（Yahoo落札データ）から高額コインを特定し、
eBayのBuy It Now価格と比較して仕入れ候補を自動探索する。

使い方:
    python run.py explore                  # 本番実行
    python run.py explore --top 20         # 上位20件のみ
    python run.py explore --dry-run        # Supabase読み込みのみ（eBay検索しない）
    python run.py explore --min-price 50000  # 最低Yahoo価格フィルタ

技術仕様:
    - Yahoo側: Supabase market_transactions (source='yahoo', 直近3か月)
    - eBay側: Buy It Now検索結果をHTML解析
    - 利益計算: Yahoo中央値 - eBay価格 - 送料3000円 - Yahoo手数料8.8%
    - レート制限: eBay検索間隔30-60秒ランダム
"""

import json
import random
import re
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client

# ============================================================
# 設定
# ============================================================

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"
YAHOO_FEE_RATE = 0.10  # ヤフオク手数料 10%
MIN_YAHOO_PRICE = 10000  # 最低Yahoo落札価格
TOP_N = 50  # 探索する上位N件
SEARCH_INTERVAL = (30, 60)  # eBay検索間隔（秒）

# 固定コスト（JPY）
SHIPPING_DOMESTIC_JPY = 750      # 国内送料
SHIPPING_US_FORWARD_JPY = 2000   # US倉庫→日本 転送
IMPORT_TAX_RATE = 0.10           # 輸入消費税（商品価格のみに適用、送料にはかからない）


def _get_fx_rate(currency: str = "usd_jpy") -> int:
    """Supabaseから今日の計算用為替レートを取得"""
    try:
        from scripts.fetch_daily_rates import get_today_rate
        rate = get_today_rate(currency)
        if rate:
            return rate
    except Exception:
        pass
    # フォールバック: 手動取得
    print("  [WARN] Supabaseからレート取得失敗。フォールバック値を使用。")
    fallbacks = {"usd_jpy": 161, "gbp_jpy": 214, "eur_jpy": 186}
    return fallbacks.get(currency, 161)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DATA_DIR = PROJECT_ROOT / "data"
RESULTS_FILE = DATA_DIR / "auto_explorer_results.json"

# 国名→英語マッピング（eBay検索用）
COUNTRY_EN_MAP = {
    "アメリカ": "USA",
    "イギリス": "Great Britain",
    "ドイツ": "Germany",
    "フランス": "France",
    "日本": "Japan",
    "カナダ": "Canada",
    "オーストラリア": "Australia",
    "中国": "China",
    "メキシコ": "Mexico",
    "スイス": "Switzerland",
    "イタリア": "Italy",
    "スペイン": "Spain",
    "オーストリア": "Austria",
    "ロシア": "Russia",
    "南アフリカ": "South Africa",
    "ニュージーランド": "New Zealand",
    "インド": "India",
    "オランダ": "Netherlands",
    "ポーランド": "Poland",
    "スウェーデン": "Sweden",
    "ノルウェー": "Norway",
    "デンマーク": "Denmark",
    "ギリシャ": "Greece",
    "トルコ": "Turkey",
    "エジプト": "Egypt",
    "ペルー": "Peru",
    "チリ": "Chile",
    "アルゼンチン": "Argentina",
    "ブラジル": "Brazil",
    "香港": "Hong Kong",
    "台湾": "Taiwan",
    "韓国": "Korea",
    "フィンランド": "Finland",
    "ハンガリー": "Hungary",
    "チェコ": "Czech",
    "ベルギー": "Belgium",
    "ポルトガル": "Portugal",
}


# ============================================================
# Yahoo データ取得（Supabase）
# ============================================================

def fetch_yahoo_high_value(min_price: int = MIN_YAHOO_PRICE,
                           months: int = 3) -> list[dict]:
    """直近N か月のYahoo高額取引をSupabaseから取得"""
    client = get_client()
    cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    print(f"  Supabase照会: source=yahoo, price>={min_price:,}, sold_date>={cutoff}")

    # Supabaseはページネーションが必要（最大1000件/リクエスト）
    all_records = []
    page_size = 1000
    offset = 0

    while True:
        resp = (client.table("market_transactions")
                .select("title, price_jpy, sold_date, grader, grade, country, year, series")
                .eq("source", "yahoo")
                .gte("sold_date", cutoff)
                .gte("price_jpy", min_price)
                .order("sold_date", desc=True)
                .range(offset, offset + page_size - 1)
                .execute())

        batch = resp.data
        if not batch:
            break

        all_records.extend(batch)
        print(f"    取得: {len(all_records):,}件...")

        if len(batch) < page_size:
            break
        offset += page_size

    print(f"  Yahoo高額取引: {len(all_records):,}件")
    return all_records


def group_by_coin_type(records: list[dict]) -> dict[str, list[dict]]:
    """(grader, grade, country, year) でグループ化"""
    groups = {}
    for r in records:
        grader = r.get("grader") or ""
        grade = r.get("grade") or ""
        country = r.get("country") or ""
        year = r.get("year") or ""

        # 最低限 grader + grade がないとスキップ
        if not grader or not grade:
            continue

        key = f"{grader}|{grade}|{country}|{year}"
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    return groups


def rank_coin_types(groups: dict[str, list[dict]],
                    top_n: int = TOP_N) -> list[dict]:
    """Yahoo中央値が高い順にソートして上位N件を返す"""
    ranked = []
    for key, records in groups.items():
        parts = key.split("|")
        prices = [r["price_jpy"] for r in records if r.get("price_jpy")]
        if not prices:
            continue

        median_price = int(statistics.median(prices))
        titles = [r.get("title", "") for r in records[:5]]  # サンプルタイトル

        # series情報を集約
        series_set = set()
        for r in records:
            s = r.get("series")
            if s:
                series_set.add(s)

        ranked.append({
            "key": key,
            "grader": parts[0],
            "grade": parts[1],
            "country": parts[2],
            "year": parts[3],
            "series": list(series_set)[:3],  # 上位3シリーズ
            "yahoo_median_jpy": median_price,
            "yahoo_count": len(records),
            "yahoo_titles": titles,
        })

    ranked.sort(key=lambda x: x["yahoo_median_jpy"], reverse=True)
    return ranked[:top_n]


# ============================================================
# eBay 検索クエリ生成
# ============================================================

# 日本語シリーズ名→英語変換
SERIES_EN_MAP = {
    "モルガンダラー": "Morgan Dollar",
    "ピースダラー": "Peace Dollar",
    "ウォーキングリバティ": "Walking Liberty",
    "ゴシッククラウン": "Gothic Crown",
    "ソブリン": "Sovereign",
    "ブリタニア": "Britannia",
    "パンダ": "Panda",
    "メイプルリーフ": "Maple Leaf",
    "イーグル": "Eagle",
    "カンガルー": "Kangaroo",
    "フィルハーモニー": "Philharmonic",
    "クルーガーランド": "Krugerrand",
    "リベルタード": "Libertad",
    "コアラ": "Koala",
    "クッカバラ": "Kookaburra",
}

# ヤフオクタイトルから額面を英語化するパターン
DENOMINATION_EN_PATTERNS = [
    (re.compile(r'(\d+)\s*ポンド'), r'\1 Pound'),
    (re.compile(r'(\d+)\s*ドル'), r'\1 Dollar'),
    (re.compile(r'(\d+)\s*ソル'), r'\1 Soles'),
    (re.compile(r'(\d+)\s*リラ'), r'\1 Lire'),
    (re.compile(r'(\d+)\s*マルク'), r'\1 Mark'),
    (re.compile(r'(\d+)\s*フラン'), r'\1 Franc'),
    (re.compile(r'(\d+)\s*ルーブル'), r'\1 Rouble'),
    (re.compile(r'(\d+)\s*クローネ'), r'\1 Krone'),
    (re.compile(r'(\d+)\s*元'), r'\1 Yuan'),
    (re.compile(r'(\d+)\s*ペソ'), r'\1 Peso'),
]


def _extract_english_keywords(titles: list[str]) -> list[str]:
    """ヤフオクタイトル群から検索に有用な英語キーワードを抽出"""
    keywords = []

    for title in titles:
        # タイトル内の英語単語を抽出（3文字以上）
        eng_words = re.findall(r'[A-Za-z]{3,}', title)
        for w in eng_words:
            w_upper = w.upper()
            # NGC/PCGS/グレードは除外（別途追加するため）
            if w_upper in ("NGC", "PCGS", "COIN", "THE", "AND", "FOR",
                           "ULTRA", "CAMEO", "DCAM", "DEEP", "PROOF",
                           "MINT", "STATE", "UNCIRCULATED", "DETAILS",
                           "COLL", "TOP", "POP"):
                continue
            if re.match(r'^(MS|PF|PR|AU|XF|VF|SP)\d', w_upper):
                continue
            keywords.append(w)

        # 日本語額面を英語化
        for pat, repl in DENOMINATION_EN_PATTERNS:
            m = pat.search(title)
            if m:
                denom_en = pat.sub(repl, m.group(0))
                keywords.append(denom_en)

        # 金貨/銀貨/プラチナ を Gold/Silver/Platinum に
        if "金貨" in title:
            keywords.append("Gold")
        elif "銀貨" in title:
            keywords.append("Silver")
        elif "プラチナ" in title:
            keywords.append("Platinum")

    # 重複除去（出現順保持）
    seen = set()
    unique = []
    for kw in keywords:
        if kw.lower() not in seen:
            seen.add(kw.lower())
            unique.append(kw)

    return unique


def generate_ebay_query(coin: dict) -> str:
    """コイン情報 + ヤフオクタイトルからeBay検索クエリを生成"""
    parts = []

    # 国名（英語化）
    country = coin.get("country", "")
    country_en = COUNTRY_EN_MAP.get(country, country)
    if country_en:
        parts.append(country_en)

    # 年号
    year = coin.get("year", "")
    if year:
        parts.append(str(year))

    # シリーズ名（日本語→英語変換）
    series_list = coin.get("series", [])
    for s in series_list:
        if not s:
            continue
        if re.match(r'^[A-Za-z\s]+$', s):
            parts.append(s)
            break
        en = SERIES_EN_MAP.get(s)
        if en:
            parts.append(en)
            break

    # ヤフオクタイトルから英語キーワード抽出
    yahoo_titles = coin.get("yahoo_titles", [])
    title_keywords = _extract_english_keywords(yahoo_titles)
    # 額面・素材など重要なキーワードを追加（最大3個）
    added = 0
    for kw in title_keywords:
        if kw.lower() not in " ".join(parts).lower():
            parts.append(kw)
            added += 1
            if added >= 3:
                break

    # 鑑定機関 + グレード
    grader = coin.get("grader", "")
    grade = coin.get("grade", "")
    if grader:
        parts.append(grader)
    if grade:
        parts.append(grade)

    # キーワード補足
    parts.append("coin")

    query = " ".join(parts)
    return query


# ============================================================
# eBay 検索・解析
# ============================================================

def _start_browser():
    """Playwright ブラウザを起動（eBay検索専用）"""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
        ],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        timezone_id="America/New_York",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = context.new_page()
    return pw, browser, page


def _stop_browser(pw, browser):
    """ブラウザを閉じる"""
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass


def search_ebay(query: str, page, max_items: int = 5) -> list[dict]:
    """Playwright でeBay Buy It Now検索 → 商品ページから価格取得

    2ステップ方式:
      1. 検索結果ページから商品URLリストを取得
      2. 各商品ページにアクセスしてUS$価格を取得

    Args:
        query: 検索クエリ
        page: Playwright Page オブジェクト
        max_items: 価格取得する最大件数（ページ遷移コスト考慮）
    """
    search_url = (f"{EBAY_SEARCH_URL}?_nkw={query.replace(' ', '+')}"
                  "&LH_BIN=1&_sop=15&LH_PrefLoc=1")

    # Step 1: 検索結果から商品URLを取得
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"    BROWSER ERROR: {e}")
        return []

    try:
        item_urls = page.evaluate(r"""() => {
            const links = document.querySelectorAll('a[href*="/itm/"]');
            const urls = new Set();
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const match = href.match(/ebay\.com\/itm\/(\d+)/);
                if (match) {
                    urls.add('https://www.ebay.com/itm/' + match[1]);
                }
            }
            return [...urls];
        }""")
    except Exception as e:
        print(f"    URL抽出ERROR: {e}")
        return []

    if not item_urls:
        print(f"    検索結果: 0件")
        return []

    print(f"    検索結果: {len(item_urls)}件 → 上位{min(max_items, len(item_urls))}件の価格取得中...")

    # Step 2: 各商品ページから価格・タイトルを取得
    items = []
    for item_url in item_urls[:max_items]:
        try:
            page.goto(item_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)

            info = page.evaluate(r"""() => {
                const body = document.body.innerText;
                const titleEl = document.querySelector('h1');
                // ページタイトル（英語）をフォールバックに使う
                const pageTitle = document.title.replace(' | eBay', '').trim();
                const title = titleEl ? titleEl.textContent.trim() : pageTitle;

                // P1修正: 複数の価格取得戦略（構造化セレクタ→正規表現）
                let priceUsd = null;

                // 戦略1: 構造化セレクタ（eBay 2024/2025 DOM）
                const priceSelectors = [
                    '.x-price-primary .ux-textspans',
                    '.x-price-primary [itemprop="price"]',
                    '.x-bin-price__content .ux-textspans',
                    '[data-testid="x-bin-price"] .ux-textspans',
                    '#prcIsum',
                    '.notranslate[itemprop="price"]',
                ];
                for (const sel of priceSelectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const content = el.getAttribute('content') || el.textContent || '';
                        const m = content.match(/([\d,]+\.?\d*)/);
                        if (m) {
                            const val = parseFloat(m[1].replace(/,/g, ''));
                            if (val > 0) { priceUsd = val; break; }
                        }
                    }
                }

                // 戦略2: body テキスト正規表現（US $xxx または $xxx）
                if (!priceUsd) {
                    const m = body.match(/US\s*\$([\d,]+\.?\d*)/)
                           || body.match(/\$\s*([\d,]+\.?\d*)/);
                    if (m) {
                        const val = parseFloat(m[1].replace(/,/g, ''));
                        if (val > 0) priceUsd = val;
                    }
                }

                return {title, pageTitle, priceUsd};
            }""")

            title = info.get("pageTitle") or info.get("title", "")
            price_usd = info.get("priceUsd")

            if price_usd and price_usd > 0:
                price_jpy = int(price_usd * USD_TO_JPY)
                price_raw = f"${price_usd:.2f}"
            else:
                price_jpy = 0
                price_raw = "N/A"

            items.append({
                "title": title,
                "price_jpy": price_jpy,
                "price_raw": price_raw,
                "url": item_url,
            })

        except Exception as e:
            print(f"    商品ページERROR ({item_url}): {e}")
            continue

    return items


def _parse_ebay_price(price_text: str) -> int | None:
    """eBay価格テキストをJPYに変換

    対応フォーマット:
        "$123.45"        → USD → JPY
        "JPY 12,345"     → そのまま
        "US $123.45"     → USD → JPY
        "GBP 80.00"      → GBP → JPY (概算)
        "$123.45 to $200" → 最初の価格を使用
    """
    if not price_text:
        return None

    # "to" がある場合は最初の価格を使用
    price_text = price_text.split(" to ")[0].strip()

    # JPY表記
    if "JPY" in price_text.upper():
        nums = re.findall(r'[\d,]+', price_text)
        if nums:
            return int(nums[0].replace(",", ""))

    # USD表記 ($, US $)
    usd_match = re.search(r'(?:US\s*)?\$\s*([\d,]+\.?\d*)', price_text)
    if usd_match:
        usd = float(usd_match.group(1).replace(",", ""))
        return int(usd * USD_TO_JPY)

    # GBP
    if "GBP" in price_text.upper() or "\u00a3" in price_text:
        nums = re.findall(r'[\d,]+\.?\d*', price_text)
        if nums:
            gbp = float(nums[0].replace(",", ""))
            return int(gbp * USD_TO_JPY * 1.27)  # GBP→USD概算

    # EUR
    if "EUR" in price_text.upper() or "\u20ac" in price_text:
        nums = re.findall(r'[\d,]+\.?\d*', price_text)
        if nums:
            eur = float(nums[0].replace(",", ""))
            return int(eur * USD_TO_JPY * 1.08)  # EUR→USD概算

    # 数値のみ（USDと仮定）
    nums = re.findall(r'[\d,]+\.?\d*', price_text)
    if nums:
        val = float(nums[0].replace(",", ""))
        if val > 1000:
            # 1000超えならJPYの可能性
            return int(val)
        return int(val * USD_TO_JPY)

    return None


# ============================================================
# 利益計算
# ============================================================

def calculate_profit(yahoo_median: int, ebay_price_usd: float,
                     ebay_shipping_usd: float = 0,
                     origin: str = "US") -> dict:
    """仕入れ利益を計算（確定版）

    原価 = (eBay商品価格USD × 為替 × 1.1) + (eBay送料USD × 為替) + 転送 + 国内送料
    ※消費税10%は商品価格のみ。送料にはかからない。
    手取り = ヤフオク落札価格 × 0.9（手数料10%）
    利益 = 手取り − 原価

    Args:
        yahoo_median: ヤフオク中央値（JPY）
        ebay_price_usd: eBay商品価格（USD）
        ebay_shipping_usd: eBay送料（USD）。Free Shipping=0。
        origin: 発送元 "US" / "UK" / "EU" / "OTHER"
    """
    fx_key = {"US": "usd_jpy", "UK": "gbp_jpy", "EU": "eur_jpy"}.get(origin, "usd_jpy")
    fx_rate = _get_fx_rate(fx_key)

    # 原価計算
    item_jpy = ebay_price_usd * fx_rate
    item_tax = item_jpy * IMPORT_TAX_RATE  # 商品のみに消費税
    shipping_ebay_jpy = ebay_shipping_usd * fx_rate  # 送料に消費税なし

    if origin == "US":
        forward_jpy = SHIPPING_US_FORWARD_JPY
    else:
        forward_jpy = 0  # US以外は直送（送料はebay_shipping_usdに含まれる想定）

    total_cost = int(item_jpy + item_tax + shipping_ebay_jpy + forward_jpy + SHIPPING_DOMESTIC_JPY)

    # 販売側
    yahoo_net = int(yahoo_median * (1 - YAHOO_FEE_RATE))
    profit = yahoo_net - total_cost
    profit_pct = round(profit / total_cost * 100, 1) if total_cost > 0 else 0

    return {
        "profit_jpy": profit,
        "profit_pct": profit_pct,
        "yahoo_net_jpy": yahoo_net,
        "total_cost_jpy": total_cost,
        "fx_rate": fx_rate,
        "origin": origin,
    }


# ============================================================
# 結果保存
# ============================================================

def save_results(results: list[dict], filepath: Path = RESULTS_FILE):
    """結果をJSONに保存（.tmp経由のatomic write）"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = filepath.with_suffix(".tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    tmp_path.replace(filepath)


def load_existing_results(filepath: Path = RESULTS_FILE) -> list[dict]:
    """既存結果を読み込み（中断再開用）"""
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


# ============================================================
# レポート表示
# ============================================================

def print_summary(results: list[dict]):
    """探索結果サマリーを表示"""
    if not results:
        print("  探索結果: 0件")
        return

    # 利益順にソート
    profitable = [r for r in results if r.get("profit_jpy", 0) > 0]
    profitable.sort(key=lambda x: x["profit_jpy"], reverse=True)

    print()
    print(f"{'=' * 70}")
    print(f"  eBay vs Yahoo 価格差レポート")
    print(f"{'=' * 70}")
    print(f"  探索コイン種: {len(results)}件")
    print(f"  利益候補:     {len(profitable)}件")
    print()

    if profitable:
        print(f"  {'コイン種別':<30} {'Yahoo中央':>10} {'eBay最安':>10} {'利益':>10} {'利益率':>8}")
        print(f"  {'-' * 68}")
        for r in profitable[:20]:
            coin_type = r.get("coin_type", "")[:28]
            print(f"  {coin_type:<30} "
                  f"Y{r.get('yahoo_median_jpy', 0):>9,} "
                  f"Y{r.get('ebay_price_jpy', 0):>9,} "
                  f"Y{r.get('profit_jpy', 0):>9,} "
                  f"{r.get('profit_pct', 0):>6.1f}%")

    print(f"{'=' * 70}")


# ============================================================
# メイン探索ループ
# ============================================================

def explore(top_n: int = TOP_N, min_price: int = MIN_YAHOO_PRICE,
            dry_run: bool = False) -> list[dict]:
    """メイン探索処理"""
    start_time = datetime.now()

    print(f"{'=' * 60}")
    print(f"  eBay vs Yahoo 自動価格差探索")
    print(f"{'=' * 60}")
    print(f"  実行日時:   {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  最低価格:   Y{min_price:,}")
    print(f"  探索上限:   {top_n}件")
    print(f"  モード:     {'DRY-RUN' if dry_run else '本番'}")
    print(f"  為替レート: 1 USD = {USD_TO_JPY} JPY")
    print()

    # Step 1: Yahoo高額取引を取得
    print("[Step 1] Yahoo高額取引データ取得...")
    yahoo_records = fetch_yahoo_high_value(min_price=min_price)
    if not yahoo_records:
        print("  Yahoo高額取引なし。終了。")
        return []

    # Step 2: コイン種別にグループ化
    print()
    print("[Step 2] コイン種別グループ化...")
    groups = group_by_coin_type(yahoo_records)
    print(f"  コイン種別: {len(groups)}種")

    # Step 3: ランキング
    ranked = rank_coin_types(groups, top_n=top_n)
    print(f"  探索対象:   {len(ranked)}件")

    if not ranked:
        print("  探索対象なし。終了。")
        return []

    print()
    print("  上位5件プレビュー:")
    for i, coin in enumerate(ranked[:5], 1):
        print(f"    {i}. {coin['key']} | Yahoo中央値: Y{coin['yahoo_median_jpy']:,} | 件数: {coin['yahoo_count']}")

    if dry_run:
        print()
        print("  [DRY-RUN] eBay検索はスキップ。")
        return []

    # --urls-only モード: eBay検索URLリストを高速出力
    urls_only = "--urls-only" in sys.argv
    if urls_only:
        print()
        print("[URLs-Only] eBay検索URLリスト出力:")
        print()
        url_list = []
        for i, coin in enumerate(ranked, 1):
            query = generate_ebay_query(coin)
            ebay_url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}&LH_BIN=1&_sop=15"
            yahoo_med = coin["yahoo_median_jpy"]
            yahoo_cnt = coin["yahoo_count"]
            print(f"  #{i:>3} | Y{yahoo_med:>9,} ({yahoo_cnt}件) | {coin['key']}")
            print(f"        {ebay_url}")
            url_list.append({
                "rank": i,
                "coin_type": coin["key"],
                "yahoo_median_jpy": yahoo_med,
                "yahoo_count": yahoo_cnt,
                "ebay_search_url": ebay_url,
                "ebay_query": query,
                "yahoo_titles": coin.get("yahoo_titles", [])[:3],
            })

        # URLリストをJSONで保存
        url_path = RESULTS_FILE.parent / "ebay_search_urls.json"
        with open(url_path, "w", encoding="utf-8") as f:
            json.dump(url_list, f, ensure_ascii=False, indent=2)
        print()
        print(f"  保存先: {url_path}")
        print(f"  合計: {len(url_list)}件のeBay検索URL")
        return url_list

    # Step 4: eBay検索ループ（Playwright ブラウザ使用）
    print()
    print(f"[Step 3] eBay Buy It Now 検索開始 ({len(ranked)}件)...")
    print(f"  検索間隔: {SEARCH_INTERVAL[0]}-{SEARCH_INTERVAL[1]}秒")
    print(f"  モード: Playwright (headless browser)")
    print()

    pw, browser, page = _start_browser()
    print("  ブラウザ起動完了")

    # 既存結果を読み込み（中断再開）
    existing_results = load_existing_results()
    existing_keys = {r.get("coin_type") for r in existing_results}

    results = list(existing_results)
    searched = 0
    skipped = 0
    errors = 0

    for idx, coin in enumerate(ranked, 1):
        coin_type = coin["key"]

        # 既に探索済みならスキップ
        if coin_type in existing_keys:
            skipped += 1
            continue

        query = generate_ebay_query(coin)

        # 進捗表示（10件ごと）
        if idx % 10 == 0 or idx == 1:
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"  [{idx}/{len(ranked)}] 経過: {elapsed:.0f}秒 | "
                  f"検索済: {searched} | スキップ: {skipped} | エラー: {errors}")

        print(f"  [{idx}/{len(ranked)}] {coin_type}")
        print(f"    Query: {query}")

        # eBay検索
        try:
            ebay_items = search_ebay(query, page)
        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1
            time.sleep(random.uniform(*SEARCH_INTERVAL))
            continue

        searched += 1

        if not ebay_items:
            print(f"    結果: 0件 (スキップ)")
            time.sleep(random.uniform(*SEARCH_INTERVAL))
            continue

        # 最安値を取得
        ebay_items.sort(key=lambda x: x["price_jpy"])
        cheapest = ebay_items[0]
        ebay_price = cheapest["price_jpy"]

        # 利益計算
        profit_info = calculate_profit(coin["yahoo_median_jpy"], ebay_price)

        result = {
            "coin_type": coin_type,
            "ebay_query": query,
            "ebay_price_jpy": ebay_price,
            "yahoo_median_jpy": coin["yahoo_median_jpy"],
            "yahoo_count": coin["yahoo_count"],
            "profit_jpy": profit_info["profit_jpy"],
            "profit_pct": profit_info["profit_pct"],
            "ebay_titles": [item["title"] for item in ebay_items[:5]],
            "ebay_prices": [item["price_jpy"] for item in ebay_items[:5]],
            "yahoo_titles": coin["yahoo_titles"],
            "timestamp": datetime.now().isoformat(),
        }

        results.append(result)

        # 利益あり/なし表示
        if profit_info["profit_jpy"] > 0:
            print(f"    >>> 利益候補! eBay Y{ebay_price:,} → Yahoo Y{coin['yahoo_median_jpy']:,} "
                  f"= 利益 Y{profit_info['profit_jpy']:,} ({profit_info['profit_pct']:.1f}%)")
        else:
            print(f"    eBay Y{ebay_price:,} vs Yahoo Y{coin['yahoo_median_jpy']:,} "
                  f"= {profit_info['profit_jpy']:,} (赤字)")

        # 進捗保存（5件ごと）
        if searched % 5 == 0:
            save_results(results)
            print(f"    [保存済: {len(results)}件]")

        # レート制限対策
        delay = random.uniform(*SEARCH_INTERVAL)
        print(f"    待機: {delay:.0f}秒...")
        time.sleep(delay)

    # 最終保存 & ブラウザ終了
    save_results(results)
    _stop_browser(pw, browser)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    print()
    print(f"[完了] 所要時間: {duration:.0f}秒 | 検索: {searched}件 | スキップ: {skipped}件 | エラー: {errors}件")
    print(f"  結果保存先: {RESULTS_FILE}")

    # サマリー表示
    print_summary(results)

    return results


# ============================================================
# CLI
# ============================================================

def main():
    args = sys.argv[1:]
    args = [a for a in args if a not in ("explore",)]

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    top_n = TOP_N
    min_price = MIN_YAHOO_PRICE

    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])
            i += 2
        elif args[i] == "--min-price" and i + 1 < len(args):
            min_price = int(args[i + 1])
            i += 2
        else:
            i += 1

    explore(top_n=top_n, min_price=min_price, dry_run=dry_run)


if __name__ == "__main__":
    main()
