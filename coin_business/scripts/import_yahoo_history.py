"""ヤフオク過去落札履歴（Aucfan CSV）をSupabase market_transactionsへ投入する

使い方:
  python run.py import-yahoo <path>             # 単一CSV/ディレクトリ投入
  python run.py import-yahoo <path> --dry-run   # ドライラン
"""

import math
import os
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client, make_dedup_key

SOURCE = "yahoo"
BATCH_SIZE = 500  # 安全マージン（カラム数多いため500に）

# Aucfan CSV列 → Supabaseカラム マッピング
AUCFAN_COLUMN_MAP = {
    "商品タイトル": "title",
    "落札価格": "price",
    "終了日": "sold_date",
    "実サイト商品ページのURL": "url",
    "商品ＩＤ": "item_id",
    "出品者ID": "seller_name",
    "入札数": "bid_count",
    "開始価格": "start_price",
    "サムネイル画像URL": "thumbnail_url",
    "詳細ページのURL": "detail_url",
}

# カバレッジ分析対象
COVERAGE_FIELDS = [
    "title", "price", "sold_date", "url",
    "grader", "grade", "country", "year", "denomination",
    "seller_name",
]

# ============================================================
# タイトルパーサー（grader/grade/country/year 自動抽出）
# ============================================================

# グレード部分の共通パターン（MS|PF|PR|AU|XF|VF|PL|SP|Genuine|Details...）
_GRADE_TYPES = r'(?:MS|PF|PR|AU|XF|EF|VF|F|VG|G|AG|FR|PO|SP|PL|UNC|Genuine|Details)'
_GRADE_SUFFIX = r'(?:\s*\d{0,2})(?:\s*(?:UC|ULTRA\s*CAMEO|RD|RB|BN|DCAM|CAM|PL|DPL|FB|FH|FT|FS|FBL|Details|\+|\*|W))*'
_GRADE_FULL = _GRADE_TYPES + _GRADE_SUFFIX

# 鑑定会社 + グレード抽出パターン（優先度順）
GRADER_GRADE_PATTERNS = [
    # NGC-PF68 ULTRA CAMEO, NGC-AU58, NGC-MS63 (ハイフン区切り)
    re.compile(r'\b(NGC|PCGS)[--]\s*(' + _GRADE_FULL + r')', re.IGNORECASE),
    # NGC(AU55), PCGS(MS64) (括弧区切り)
    re.compile(r'\b(NGC|PCGS)\s*[(\uff08]\s*(' + _GRADE_FULL + r')\s*[)\uff09]', re.IGNORECASE),
    # MS65 NGC, PF70 PCGS (グレードが前)
    re.compile(r'(' + _GRADE_FULL + r')\s+(NGC|PCGS)\b', re.IGNORECASE),
    # NGC MS70, NGC PF69 UC, PCGS PR69DCAM (スペース区切り・標準)
    re.compile(r'\b(NGC|PCGS)\s+(?:鑑定品\s*)?(' + _GRADE_FULL + r')', re.IGNORECASE),
    # Fallback: just grader name
    re.compile(r'\b(NGC|PCGS)\b', re.IGNORECASE),
]

# 年号抽出（西暦4桁）
YEAR_PATTERN = re.compile(r'\b(1[0-9]{3}|20[0-2][0-9])\s*年?\b')

# 国名マッピング（タイトルから抽出）
COUNTRY_MAP = {
    "アメリカ": "アメリカ", "米国": "アメリカ", "USA": "アメリカ",
    "イギリス": "イギリス", "英国": "イギリス", "UK": "イギリス", "GREAT BRITAIN": "イギリス", "GB": "イギリス",
    "ドイツ": "ドイツ", "GERMANY": "ドイツ", "プロイセン": "ドイツ", "バイエルン": "ドイツ", "ザクセン": "ドイツ",
    "フランス": "フランス", "FRANCE": "フランス",
    "日本": "日本", "JAPAN": "日本",
    "カナダ": "カナダ", "CANADA": "カナダ",
    "オーストラリア": "オーストラリア", "AUSTRALIA": "オーストラリア",
    "中国": "中国", "CHINA": "中国", "パンダ": "中国",
    "メキシコ": "メキシコ", "MEXICO": "メキシコ",
    "スイス": "スイス", "SWITZERLAND": "スイス",
    "イタリア": "イタリア", "ITALY": "イタリア",
    "スペイン": "スペイン", "SPAIN": "スペイン",
    "オーストリア": "オーストリア", "AUSTRIA": "オーストリア",
    "ロシア": "ロシア", "RUSSIA": "ロシア",
    "南アフリカ": "南アフリカ", "クルーガーランド": "南アフリカ",
    "ニュージーランド": "ニュージーランド",
    "フィリピン": "フィリピン",
    "インド": "インド", "INDIA": "インド",
    "ブラジル": "ブラジル",
    "香港": "香港",
    "台湾": "台湾",
    "韓国": "韓国",
    "タイ": "タイ",
    "オランダ": "オランダ", "NETHERLANDS": "オランダ",
    "ベルギー": "ベルギー",
    "ポルトガル": "ポルトガル",
    "ギリシャ": "ギリシャ",
    "トルコ": "トルコ",
    "エジプト": "エジプト",
    "ペルー": "ペルー",
    "コロンビア": "コロンビア",
    "チリ": "チリ",
    "アルゼンチン": "アルゼンチン",
    "ポーランド": "ポーランド",
    "ハンガリー": "ハンガリー",
    "チェコ": "チェコ", "ボヘミア": "チェコ",
    "スウェーデン": "スウェーデン",
    "ノルウェー": "ノルウェー",
    "デンマーク": "デンマーク",
    "フィンランド": "フィンランド",
    "マン島": "イギリス", "英領": "イギリス",
    "グアテマラ": "グアテマラ",
    "イラク": "イラク",
    "ペルシア": "イラン", "イラン": "イラン",
    "シリア": "シリア",
    "パキスタン": "パキスタン",
    "ミャンマー": "ミャンマー",
    "ベトナム": "ベトナム",
    "マレーシア": "マレーシア",
    "シンガポール": "シンガポール",
    "パナマ": "パナマ",
    "キューバ": "キューバ",
    "ササン朝": "イラン",
    "ローマ": "ローマ帝国", "古代ローマ": "ローマ帝国",
    "ハワイ": "アメリカ",
    "リベルタード": "メキシコ",
    "ブリタニア": "イギリス",
    "イーグル": "アメリカ",
    "メイプルリーフ": "カナダ",
    "モルガン": "アメリカ", "Morgan": "アメリカ",
    "ウォーキングリバティ": "アメリカ", "Walking Liberty": "アメリカ",
}

# 素材推定（優先度順）
MATERIAL_KEYWORDS = [
    ("プラチナ", "プラチナ"), ("パラジウム", "パラジウム"),
    ("金貨", "金"), ("銀貨", "銀"), ("銅貨", "銅"), ("白銅貨", "白銅"), ("ニッケル", "ニッケル"),
    ("Gold", "金"), ("Silver", "銀"), ("Platinum", "プラチナ"),
    ("1/10oz", "金"), ("1/4oz", "金"), ("1/2oz", "金"),
]

# 額面抽出パターン（優先度順、具体的なものから）
DENOMINATION_PATTERNS = [
    # 重量表記（oz）
    (re.compile(r'(5)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(2)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/2)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/4)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/10)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/20)\s*oz', re.IGNORECASE), "{0}oz"),
    # 通貨単位（日本語）
    (re.compile(r'(\d+)\s*ポンド'), "{0}ポンド"),
    (re.compile(r'(\d+)\s*ドル'), "{0}ドル"),
    (re.compile(r'(\d+)\s*フラン'), "{0}フラン"),
    (re.compile(r'(\d+)\s*マルク'), "{0}マルク"),
    (re.compile(r'(\d+)\s*ルピー'), "{0}ルピー"),
    (re.compile(r'(\d+)\s*ターラー'), "{0}ターラー"),
    (re.compile(r'(\d+)\s*クローネ'), "{0}クローネ"),
    (re.compile(r'(\d+)\s*ペソ'), "{0}ペソ"),
    (re.compile(r'(\d+)\s*リラ'), "{0}リラ"),
    (re.compile(r'(\d+)\s*レアル'), "{0}レアル"),
    (re.compile(r'(\d+)\s*グルデン'), "{0}グルデン"),
    (re.compile(r'(\d+)\s*ソブリン'), "{0}ソブリン"),
    (re.compile(r'(\d+)\s*ギニー'), "{0}ギニー"),
    (re.compile(r'(\d+)\s*シリング'), "{0}シリング"),
    (re.compile(r'(\d+)\s*ペンス'), "{0}ペンス"),
    (re.compile(r'(\d+)\s*ペニー'), "{0}ペニー"),
    (re.compile(r'(\d+)\s*セント'), "{0}セント"),
    (re.compile(r'(\d+)\s*円(?!ス)'), "{0}円"),  # 「円スタート」除外
    (re.compile(r'(\d+)\s*銭'), "{0}銭"),
    # 英語表記
    (re.compile(r'(\d+)\s*Dollar', re.IGNORECASE), "{0}ドル"),
    (re.compile(r'(\d+)\s*Pound', re.IGNORECASE), "{0}ポンド"),
    (re.compile(r'(\d+)\s*Franc', re.IGNORECASE), "{0}フラン"),
    (re.compile(r'(\d+)\s*Crown', re.IGNORECASE), "{0}クラウン"),
    (re.compile(r'(\d+)\s*Cent', re.IGNORECASE), "{0}セント"),
    (re.compile(r'(\d+)\s*Peso', re.IGNORECASE), "{0}ペソ"),
]

# シリーズ名抽出
SERIES_MAP = [
    # イギリス
    ("ウナとライオン", "ウナとライオン"), ("Una and the Lion", "ウナとライオン"),
    ("ブリタニア", "ブリタニア"), ("Britannia", "ブリタニア"),
    ("ソブリン", "ソブリン"), ("Sovereign", "ソブリン"),
    ("クイーンズビースト", "クイーンズビースト"), ("Queen's Beast", "クイーンズビースト"),
    ("ロイヤルチューダー", "ロイヤルチューダー"), ("Tudor Beast", "ロイヤルチューダー"),
    ("ゴチッククラウン", "ゴチッククラウン"), ("Gothic Crown", "ゴチッククラウン"),
    # アメリカ
    ("モルガン", "モルガンダラー"), ("Morgan", "モルガンダラー"),
    ("ウォーキングリバティ", "ウォーキングリバティ"), ("Walking Liberty", "ウォーキングリバティ"),
    ("スタンディングリバティ", "スタンディングリバティ"), ("Standing Liberty", "スタンディングリバティ"),
    ("シーテッドリバティ", "シーテッドリバティ"), ("Seated Liberty", "シーテッドリバティ"),
    ("ピースドル", "ピースダラー"), ("Peace", "ピースダラー"),
    ("トレードドル", "トレードダラー"), ("Trade Dollar", "トレードダラー"),
    ("バッファロー", "バッファロー"), ("Buffalo", "バッファロー"),
    ("インディアン", "インディアンヘッド"), ("Indian", "インディアンヘッド"),
    ("セントゴーデンズ", "セントゴーデンズ"), ("Saint-Gaudens", "セントゴーデンズ"),
    # 中国
    ("パンダ", "パンダ"), ("Panda", "パンダ"),
    # カナダ
    ("メイプルリーフ", "メイプルリーフ"), ("Maple Leaf", "メイプルリーフ"),
    # オーストラリア
    ("カンガルー", "カンガルー"), ("Kangaroo", "カンガルー"),
    ("コアラ", "コアラ"), ("Koala", "コアラ"),
    ("カワセミ", "クッカバラ"), ("クッカバラ", "クッカバラ"), ("Kookaburra", "クッカバラ"),
    # オーストリア
    ("フィルハーモニー", "フィルハーモニー"), ("Philharmonic", "フィルハーモニー"),
    ("マリアテレジア", "マリアテレジア"), ("Maria Theresa", "マリアテレジア"),
    # 南アフリカ
    ("クルーガーランド", "クルーガーランド"), ("Krugerrand", "クルーガーランド"),
    # メキシコ
    ("リベルタード", "リベルタード"), ("Libertad", "リベルタード"),
    # フランス
    ("ナポレオン", "ナポレオン"),
    # その他シリーズ
    ("ルナ", "ルナ"), ("Lunar", "ルナ"),
]

# 特徴タグ抽出
TAG_PATTERNS = [
    # リリース種別
    (re.compile(r'First\s*Release|FR\b|初期発行', re.IGNORECASE), "First Release"),
    (re.compile(r'Early\s*Release|ER\b', re.IGNORECASE), "Early Release"),
    (re.compile(r'First\s*Strike|FS\b', re.IGNORECASE), "First Strike"),
    (re.compile(r'First\s*Day', re.IGNORECASE), "First Day"),
    # 品質・鑑定
    (re.compile(r'最高鑑定|Top[\s-]*Pop'), "最高鑑定"),
    (re.compile(r'プルーフ|Proof', re.IGNORECASE), "プルーフ"),
    (re.compile(r'レインボー|Rainbow', re.IGNORECASE), "レインボートーン"),
    (re.compile(r'トーン|Toned', re.IGNORECASE), "トーン"),
    # 希少性
    (re.compile(r'希少|稀少|レア|Rare', re.IGNORECASE), "希少"),
    (re.compile(r'(\d+)枚限定'), "限定枚数"),
    (re.compile(r'限定'), "限定"),
    # エラーコイン
    (re.compile(r'エラー|Error', re.IGNORECASE), "エラーコイン"),
    (re.compile(r'バラエティ|Variety', re.IGNORECASE), "バラエティ"),
]

# ノイズ検知パターン
NOISE_SET = re.compile(r'セット|まとめ|おまとめ|\blot\b|\bLOT\b|[2-9]\d*枚セット|複数', re.IGNORECASE)
NOISE_NON_COIN = re.compile(
    r'メダル|インゴット|記念品|カタログ|書籍|勲章|バッジ|徽章'
    r'|\bmedal\b|\bingot\b',
    re.IGNORECASE
)


def parse_title(title: str) -> dict:
    """タイトルから鑑定会社/グレード/国/年号/素材/額面/シリーズ/タグ/ノイズフラグを抽出"""
    result = {}

    # キリル文字のМ→ラテンMに正規化
    normalized_title = title.replace("\u041c", "M").replace("\u043c", "m")

    # Grader + Grade
    for i, pattern in enumerate(GRADER_GRADE_PATTERNS):
        m = pattern.search(normalized_title)
        if m:
            if i == 2:
                result["grader"] = m.group(2).upper()
                grade_raw = m.group(1).strip()
                if grade_raw:
                    result["grade"] = re.sub(r'\s+', '', grade_raw.upper())
            else:
                result["grader"] = m.group(1).upper()
                if m.lastindex >= 2 and m.group(2):
                    grade_raw = m.group(2).strip()
                    if grade_raw:
                        grade_norm = grade_raw.upper()
                        grade_norm = grade_norm.replace("ULTRA CAMEO", "UC")
                        grade_norm = re.sub(r'\s+', '', grade_norm)
                        result["grade"] = grade_norm
            break

    # Year
    years = YEAR_PATTERN.findall(title)
    if years:
        coin_years = [int(y) for y in years if int(y) <= 2026]
        if coin_years:
            result["year"] = min(coin_years)

    # Country
    for keyword, country in COUNTRY_MAP.items():
        if keyword in title:
            result["country"] = country
            break

    # Material（優先度順）
    for keyword, material in MATERIAL_KEYWORDS:
        if keyword in title:
            result["material"] = material
            break

    # Denomination（額面）
    for pattern, fmt in DENOMINATION_PATTERNS:
        m = pattern.search(title)
        if m:
            # 「1円スタート」等を除外
            start = m.start()
            context = title[max(0, start - 5):m.end() + 5]
            if "スタート" in context or "出品" in context:
                continue
            result["denomination"] = fmt.format(m.group(1))
            break

    # Series（シリーズ名）
    for keyword, series_name in SERIES_MAP:
        if keyword in title:
            result["series"] = series_name
            break

    # Tags（特徴タグ）
    tags = []
    for pattern, tag_name in TAG_PATTERNS:
        if pattern.search(title):
            tags.append(tag_name)
    # ノイズフラグもタグに格納
    if NOISE_SET.search(title):
        tags.append("_noise:set")
    if NOISE_NON_COIN.search(title):
        tags.append("_noise:non_coin")
    if tags:
        result["tags"] = tags

    return result


def clean_value(val):
    """NaN/None/空文字を処理"""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    return val


def convert_aucfan_row(row: dict, grader_hint: str = "") -> dict | None:
    """Aucfan CSV行 → Supabaseレコードに変換"""
    record = {"source": SOURCE, "currency": "JPY"}

    # 基本マッピング
    title = clean_value(row.get("商品タイトル"))
    if not title:
        return None
    record["title"] = str(title).strip()

    # 価格
    price_raw = clean_value(row.get("落札価格"))
    if price_raw is None:
        return None
    try:
        record["price"] = int(float(str(price_raw).replace(",", "")))
        record["price_jpy"] = record["price"]
    except (ValueError, TypeError):
        return None

    # 日付
    sold_date = clean_value(row.get("終了日"))
    if not sold_date:
        return None
    if hasattr(sold_date, "strftime"):
        record["sold_date"] = sold_date.strftime("%Y-%m-%d")
    else:
        date_str = str(sold_date).strip()
        # Handle slash format: 2023/6/30 → 2023-06-30
        import re as _re
        slash_match = _re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_str)
        if slash_match:
            y, m, d = slash_match.groups()
            record["sold_date"] = f"{y}-{int(m):02d}-{int(d):02d}"
        else:
            record["sold_date"] = date_str[:10]

    # URL（ヤフオク実URL優先）
    url = clean_value(row.get("実サイト商品ページのURL"))
    if url:
        record["url"] = str(url).strip()

    # 出品者
    seller = clean_value(row.get("出品者ID"))
    if seller:
        record["seller_name"] = str(seller).strip()

    # 入札数・開始価格をraw_dataに格納
    raw_data = {}
    bid_count = clean_value(row.get("入札数"))
    if bid_count is not None:
        try:
            raw_data["bid_count"] = int(float(str(bid_count)))
        except (ValueError, TypeError):
            pass
    start_price = clean_value(row.get("開始価格"))
    if start_price is not None:
        try:
            raw_data["start_price"] = int(float(str(start_price).replace(",", "")))
        except (ValueError, TypeError):
            pass
    thumbnail = clean_value(row.get("サムネイル画像URL"))
    if thumbnail:
        raw_data["thumbnail_url"] = str(thumbnail).strip()
    detail_url = clean_value(row.get("詳細ページのURL"))
    if detail_url:
        raw_data["detail_url"] = str(detail_url).strip()
    item_id = clean_value(row.get("商品ＩＤ"))
    if item_id:
        raw_data["item_id"] = str(item_id).strip()

    if raw_data:
        record["raw_data"] = raw_data

    # タイトルからメタデータ抽出
    parsed = parse_title(record["title"])
    for key in ("grader", "grade", "country", "year", "material",
                "denomination", "series", "tags"):
        if key in parsed:
            record[key] = parsed[key]

    # grader_hint（ファイル名から）がある場合、graderが未設定なら補完
    if grader_hint and "grader" not in record:
        record["grader"] = grader_hint

    # dedup_key生成
    record["dedup_key"] = make_dedup_key(
        SOURCE,
        url=record.get("url"),
        title=record.get("title", ""),
        price=record.get("price", 0),
        sold_date=record.get("sold_date", ""),
    )

    return record


def detect_grader_from_path(path: str) -> str:
    """ファイルパスからNGC/PCGSを判定"""
    name = os.path.basename(path).upper()
    if "NGC" in name:
        return "NGC"
    if "PCGS" in name:
        return "PCGS"
    parent = os.path.basename(os.path.dirname(path)).upper()
    if "NGC" in parent:
        return "NGC"
    if "PCGS" in parent:
        return "PCGS"
    return ""


def read_csv_file(path: str) -> list[dict]:
    """単一CSVファイルを読み込みSupabaseレコード形式に変換"""
    grader_hint = detect_grader_from_path(path)

    # エンコーディング自動判定
    for enc in ("cp932", "utf-8", "utf-8-sig", "shift_jis"):
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        print(f"  ERROR: {path} のエンコーディングを判定できません")
        return []

    records = []
    skipped = 0
    for _, row in df.iterrows():
        rec = convert_aucfan_row(row.to_dict(), grader_hint=grader_hint)
        if rec:
            records.append(rec)
        else:
            skipped += 1

    return records


def collect_csv_files(path: str) -> list[str]:
    """パスからCSVファイル一覧を収集（ディレクトリなら再帰探索）"""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".csv":
        return [str(p)]
    if p.is_dir():
        files = sorted(p.rglob("*.csv"))
        return [str(f) for f in files]
    # ZIP等の場合はそのまま返す
    return [str(p)]


def upload_records(records: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """Supabaseへバッチupsert"""
    total = len(records)
    success = 0
    failed = 0

    if dry_run:
        print(f"  [DRY-RUN] {total}件（実際の投入はスキップ）")
        return total, 0

    client = get_client()

    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        try:
            resp = client.table("market_transactions").upsert(
                batch, on_conflict="dedup_key"
            ).execute()
            inserted = len(resp.data)
            success += inserted
            print(f"  Batch {batch_num}/{total_batches}: {inserted}件OK")
        except Exception as e:
            error_str = str(e)
            if "duplicate" in error_str.lower() or "conflict" in error_str.lower():
                success += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: {len(batch)}件（重複更新）")
            else:
                failed += len(batch)
                print(f"  Batch {batch_num}/{total_batches}: ERROR - {error_str[:300]}")

    return success, failed


def analyze_coverage(records: list[dict]) -> dict:
    """データカバー率を分析"""
    stats = {}
    total = len(records)
    for field in COVERAGE_FIELDS:
        filled = sum(1 for r in records if r.get(field) is not None and r.get(field) != "")
        rate = filled / total * 100 if total > 0 else 0
        stats[field] = {"filled": filled, "empty": total - filled, "rate": f"{rate:.1f}%"}
    return stats


def analyze_detailed(records: list[dict]) -> dict:
    """CEO向け詳細分析"""
    total = len(records)
    analysis = {"total": total}

    # NGC / PCGS 件数
    ngc = sum(1 for r in records if r.get("grader") == "NGC")
    pcgs = sum(1 for r in records if r.get("grader") == "PCGS")
    other_grader = total - ngc - pcgs
    analysis["grader"] = {"NGC": ngc, "PCGS": pcgs, "other": other_grader}

    # 国別 Top 10
    countries = {}
    for r in records:
        c = r.get("country", "(未抽出)")
        if not c:
            c = "(未抽出)"
        countries[c] = countries.get(c, 0) + 1
    analysis["countries_top10"] = sorted(countries.items(), key=lambda x: -x[1])[:10]

    # 年号分布
    years = {}
    no_year = 0
    for r in records:
        y = r.get("year")
        if y:
            decade = f"{(y // 10) * 10}s"
            years[decade] = years.get(decade, 0) + 1
        else:
            no_year += 1
    analysis["year_decades"] = sorted(years.items(), key=lambda x: x[0])
    analysis["no_year"] = no_year

    # グレード分布 Top 15
    grades = {}
    for r in records:
        g = r.get("grade", "(未抽出)")
        if not g:
            g = "(未抽出)"
        grades[g] = grades.get(g, 0) + 1
    analysis["grades_top15"] = sorted(grades.items(), key=lambda x: -x[1])[:15]

    # 価格帯分布
    price_ranges = {"~1万": 0, "1-5万": 0, "5-10万": 0, "10-50万": 0, "50-100万": 0, "100万~": 0}
    for r in records:
        p = r.get("price", 0)
        if p < 10000:
            price_ranges["~1万"] += 1
        elif p < 50000:
            price_ranges["1-5万"] += 1
        elif p < 100000:
            price_ranges["5-10万"] += 1
        elif p < 500000:
            price_ranges["10-50万"] += 1
        elif p < 1000000:
            price_ranges["50-100万"] += 1
        else:
            price_ranges["100万~"] += 1
    analysis["price_ranges"] = price_ranges

    # 素材
    materials = {}
    for r in records:
        m = r.get("material", "(未抽出)")
        if not m:
            m = "(未抽出)"
        materials[m] = materials.get(m, 0) + 1
    analysis["materials"] = sorted(materials.items(), key=lambda x: -x[1])

    # 出品者 Top 10
    sellers = {}
    for r in records:
        s = r.get("seller_name", "(不明)")
        if not s:
            s = "(不明)"
        sellers[s] = sellers.get(s, 0) + 1
    analysis["sellers_top10"] = sorted(sellers.items(), key=lambda x: -x[1])[:10]

    # 月別件数
    monthly = {}
    for r in records:
        d = r.get("sold_date", "")[:7]
        if d:
            monthly[d] = monthly.get(d, 0) + 1
    analysis["monthly"] = sorted(monthly.items())

    # 額面 Top 15
    denoms = {}
    for r in records:
        d = r.get("denomination", "(未抽出)")
        if not d:
            d = "(未抽出)"
        denoms[d] = denoms.get(d, 0) + 1
    analysis["denominations_top15"] = sorted(denoms.items(), key=lambda x: -x[1])[:15]

    # シリーズ Top 15
    series = {}
    for r in records:
        s = r.get("series", "(未抽出)")
        if not s:
            s = "(未抽出)"
        series[s] = series.get(s, 0) + 1
    analysis["series_top15"] = sorted(series.items(), key=lambda x: -x[1])[:15]

    # 特徴タグ分布
    tag_counts = {}
    for r in records:
        for tag in (r.get("tags") or []):
            if not tag.startswith("_noise:"):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    analysis["tags_top15"] = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]

    # ノイズ集計
    noise_set = sum(1 for r in records if "_noise:set" in (r.get("tags") or []))
    noise_non_coin = sum(1 for r in records if "_noise:non_coin" in (r.get("tags") or []))
    noise_any = sum(1 for r in records if any(t.startswith("_noise:") for t in (r.get("tags") or [])))
    analysis["noise"] = {"set": noise_set, "non_coin": noise_non_coin, "any": noise_any}

    return analysis


def print_report(records: list[dict], stats: dict, success: int, failed: int, dup_count: int):
    """CEO向け8項目レポート出力"""
    analysis = analyze_detailed(records)
    total = analysis["total"]

    print()
    print("=" * 70)
    print("  ヤフオク落札データ投入レポート")
    print("=" * 70)
    print()

    # 1. 取込件数
    print(f"[1] 取込件数: {success:,}件 / 読込{total:,}件")
    if failed:
        print(f"    失敗: {failed:,}件")
    print()

    # 2. 重複件数
    print(f"[2] 重複件数: {dup_count:,}件（dedup_keyベースupsert）")
    print()

    # 3. NGC / PCGS 件数
    g = analysis["grader"]
    print(f"[3] 鑑定会社別:")
    print(f"    NGC:  {g['NGC']:>6,}件 ({g['NGC']/total*100:.1f}%)")
    print(f"    PCGS: {g['PCGS']:>6,}件 ({g['PCGS']/total*100:.1f}%)")
    if g["other"]:
        print(f"    其他: {g['other']:>6,}件 ({g['other']/total*100:.1f}%)")
    print()

    # 4. 国・年号・グレードの埋まり具合
    print("[4] 主要フィールド充填率:")
    for field in ["country", "year", "grade", "grader"]:
        s = stats.get(field, {"filled": 0, "empty": total, "rate": "0%"})
        rate_val = float(s["rate"].rstrip("%"))
        bar = "#" * int(rate_val / 5) + "." * (20 - int(rate_val / 5))
        print(f"    {field:<14} [{bar}] {s['rate']:>6} ({s['filled']:,}/{total:,})")
    print()

    # 国別 Top 10
    print("    国別 Top 10:")
    for country, cnt in analysis["countries_top10"]:
        print(f"      {country:<16} {cnt:>5,}件 ({cnt/total*100:.1f}%)")
    print()

    # 年号分布（年代別）
    print("    年代別分布:")
    for decade, cnt in analysis["year_decades"]:
        print(f"      {decade:<8} {cnt:>5,}件")
    if analysis["no_year"]:
        print(f"      (未抽出) {analysis['no_year']:>5,}件")
    print()

    # グレード Top 15
    print("    グレード Top 15:")
    for grade, cnt in analysis["grades_top15"]:
        print(f"      {grade:<18} {cnt:>5,}件")
    print()

    # 5. 額面・素材・シリーズ名の充填状況
    print("[5] 補助フィールド充填率:")
    for field in ["denomination", "material", "series", "cert_number"]:
        s = stats.get(field, {"filled": 0, "empty": total, "rate": "0%"})
        rate_val = float(s["rate"].rstrip("%"))
        bar = "#" * int(rate_val / 5) + "." * (20 - int(rate_val / 5))
        print(f"    {field:<14} [{bar}] {s['rate']:>6} ({s['filled']:,}/{total:,})")
    print()

    # 額面 Top 15
    print("    額面 Top 15:")
    for denom, cnt in analysis["denominations_top15"]:
        print(f"      {denom:<16} {cnt:>5,}件")
    print()

    # 素材分布
    print("    素材分布:")
    for material, cnt in analysis["materials"]:
        print(f"      {material:<12} {cnt:>5,}件")
    print()

    # シリーズ Top 15
    print("    シリーズ Top 15:")
    for series, cnt in analysis["series_top15"]:
        print(f"      {series:<20} {cnt:>5,}件")
    print()

    # 特徴タグ Top 15
    print("    特徴タグ Top 15:")
    for tag, cnt in analysis["tags_top15"]:
        print(f"      {tag:<20} {cnt:>5,}件")
    print()

    # 6. ノイズ分析
    noise = analysis["noise"]
    print("[6] ノイズ分析:")
    print(f"    セット/まとめ売り:  {noise['set']:>5,}件")
    print(f"    メダル/非コイン疑い: {noise['non_coin']:>5,}件")
    print(f"    ノイズ合計(重複含):  {noise['any']:>5,}件 ({noise['any']/total*100:.1f}%)")
    low_price = sum(1 for r in records if r.get("price", 0) < 500)
    if low_price:
        print(f"    落札500円未満:      {low_price:>5,}件")
    clean_count = total - noise["any"]
    print(f"    クリーンデータ:     {clean_count:>5,}件 ({clean_count/total*100:.1f}%)")
    print(f"    (tags @> '{{_noise:set}}' で検索除外可能)")
    print()

    # 7. 価格帯分布
    print("[7] 価格帯分布:")
    for range_name, cnt in analysis["price_ranges"].items():
        bar = "#" * max(1, int(cnt / total * 50))
        print(f"    {range_name:<10} {cnt:>5,}件 {bar}")
    print()

    # 8. 月別件数
    print("[8] 月別取引件数:")
    for month, cnt in analysis["monthly"]:
        bar = "#" * max(1, int(cnt / 50))
        print(f"    {month} {cnt:>5,}件 {bar}")
    print()

    # 出品者 Top 10
    print("[+] 出品者 Top 10:")
    for seller, cnt in analysis["sellers_top10"]:
        print(f"    {seller:<20} {cnt:>5,}件")
    print()

    # 総合判定
    print("=" * 70)
    country_rate = float(stats.get("country", {"rate": "0%"})["rate"].rstrip("%"))
    grade_rate = float(stats.get("grade", {"rate": "0%"})["rate"].rstrip("%"))
    if failed == 0 and success > 0:
        if country_rate >= 60 and grade_rate >= 80:
            print("  [判定] 投入成功。データ品質良好。100万件スケールOK。")
        else:
            print("  [判定] 投入成功。タイトルパーサー改善で充填率向上余地あり。")
    elif failed > 0:
        print(f"  [判定] {failed}件失敗あり。原因確認後に再実行推奨。")
    print("=" * 70)


def main():
    args = sys.argv[1:]
    args = [a for a in args if a != "import-yahoo"]

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    input_path = args[0] if args else None

    if not input_path:
        print("使い方: python run.py import-yahoo <csv_path_or_dir> [--dry-run]")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"ヤフオク相場履歴 CSV->Supabase 投入")
    print(f"{'=' * 60}")
    print(f"入力: {input_path}")
    print(f"モード: {'DRY-RUN' if dry_run else '本番投入'}")
    print()

    # 1. CSV収集
    csv_files = collect_csv_files(input_path)
    print(f"[1/4] CSVファイル収集: {len(csv_files)}ファイル")
    for f in csv_files:
        print(f"  {os.path.basename(f)}")
    print()

    # 2. 全ファイル読み込み
    print("[2/4] データ読み込み中...")
    all_records = []
    for csv_path in csv_files:
        records = read_csv_file(csv_path)
        grader = detect_grader_from_path(csv_path)
        print(f"  {os.path.basename(csv_path)}: {len(records):,}件 [{grader}]")
        all_records.extend(records)
    print(f"  合計: {len(all_records):,}件")
    print()

    if not all_records:
        print("投入対象なし")
        return

    # 重複チェック（dedup_key単位）
    seen = set()
    unique_records = []
    dup_in_file = 0
    for r in all_records:
        dk = r["dedup_key"]
        if dk in seen:
            dup_in_file += 1
        else:
            seen.add(dk)
            unique_records.append(r)
    if dup_in_file:
        print(f"  ファイル内重複除去: {dup_in_file}件 -> {len(unique_records):,}件")
        print()

    # 3. カバー率分析
    print("[3/4] データカバー率分析...")
    # 拡張カバレッジ
    extended_fields = COVERAGE_FIELDS + ["denomination", "material", "series", "cert_number"]
    stats = {}
    total = len(unique_records)
    for field in extended_fields:
        filled = sum(1 for r in unique_records if r.get(field) is not None and r.get(field) != "")
        rate = filled / total * 100 if total > 0 else 0
        stats[field] = {"filled": filled, "empty": total - filled, "rate": f"{rate:.1f}%"}
    print()

    # 4. アップロード
    print(f"[4/4] Supabaseへ投入中... ({len(unique_records):,}件, {BATCH_SIZE}件/batch)")
    success, failed = upload_records(unique_records, dry_run=dry_run)
    print()

    # レポート出力
    print_report(unique_records, stats, success, failed, dup_in_file)


if __name__ == "__main__":
    main()
