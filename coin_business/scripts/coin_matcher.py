"""コイン同一性判定モジュール

スラブ1行目相当の情報を抽出し、完全一致で判定する。
スラブ1行目 = 年号 + ミントマーク + シリーズ + 額面 + 素材

例:
  "1998 P EAGLE S$1" → year=1998, mint=P, series=Eagle, denom=$1, metal=Silver
  "1908 D SAINT GAUDENS $20" → year=1908, mint=D, series=Saint Gaudens, denom=$20, metal=Gold
"""
import re


# ミントマーク抽出パターン
MINT_MARKS = {"P", "D", "S", "W", "CC", "O"}

# シリーズ名正規化マップ（英語・日本語 → 正規化名）
SERIES_NORMALIZE = {
    # American
    "morgan": "Morgan Dollar",
    "モルガン": "Morgan Dollar",
    "peace": "Peace Dollar",
    "ピース": "Peace Dollar",
    "eagle": None,  # Silver/Gold/Platinumで分岐するので後処理
    "イーグル": None,
    "silver eagle": "Silver Eagle",
    "シルバーイーグル": "Silver Eagle",
    "gold eagle": "Gold Eagle",
    "platinum eagle": "Platinum Eagle",
    "saint gaudens": "Saint Gaudens",
    "saint-gaudens": "Saint Gaudens",
    "walking liberty": "Walking Liberty",
    "ウォーキングリバティ": "Walking Liberty",
    "liberty head": "Liberty Head",
    "indian": "Indian Head",
    "buffalo": "Buffalo",
    # British
    "sovereign": "Sovereign",
    "ソブリン": "Sovereign",
    "britannia": "Britannia",
    "ブリタニア": "Britannia",
    "queen's beast": "Queens Beast",
    "king's beast": "Kings Beast",
    "gothic crown": "Gothic Crown",
    "ゴシッククラウン": "Gothic Crown",
    "portraits": "Portraits of a Queen",
    "ポートレート": "Portraits of a Queen",
    # Chinese
    "panda": "Panda",
    "パンダ": "Panda",
    # Canadian
    "maple leaf": "Maple Leaf",
    "メイプルリーフ": "Maple Leaf",
    # Australian
    "kangaroo": "Kangaroo",
    "カンガルー": "Kangaroo",
    "kookaburra": "Kookaburra",
    "クッカバラ": "Kookaburra",
    "koala": "Koala",
    "コアラ": "Koala",
    # Austrian
    "philharmonic": "Philharmonic",
    "フィルハーモニー": "Philharmonic",
    # South African
    "krugerrand": "Krugerrand",
    "クルーガーランド": "Krugerrand",
    # Mexican
    "libertad": "Libertad",
    "リベルタード": "Libertad",
    # Peru
    "sol": "Soles",
    "ソル": "Soles",
}

# 額面抽出パターン（英語・日本語対応）
DENOMINATION_PATTERNS = [
    # USD
    (re.compile(r'S?\$(\d+)', re.IGNORECASE), "USD"),
    (re.compile(r'(\d+)\s*(?:ドル|Dollar|Dollars)', re.IGNORECASE), "USD"),
    # GBP
    (re.compile(r'£(\d+)', re.IGNORECASE), "GBP"),
    (re.compile(r'(\d+)\s*(?:ポンド|Pound|Pounds)', re.IGNORECASE), "GBP"),
    # Yuan
    (re.compile(r'(\d+)\s*(?:元|Yuan)', re.IGNORECASE), "CNY"),
    # Soles
    (re.compile(r'(\d+)\s*(?:ソル|Sol|Soles)', re.IGNORECASE), "PEN"),
    # Franc
    (re.compile(r'(\d+)\s*(?:フラン|Franc|Francs)', re.IGNORECASE), "CHF"),
    # Lire
    (re.compile(r'(\d+)\s*(?:リラ|Lir[ae])', re.IGNORECASE), "ITL"),
    # Mark
    (re.compile(r'(\d+)\s*(?:マルク|Mark)', re.IGNORECASE), "DEM"),
]

# 素材判定
METAL_PATTERNS = [
    (re.compile(r'\b(?:gold|金貨|金)\b', re.IGNORECASE), "Gold"),
    (re.compile(r'\b(?:silver|銀貨|銀)\b', re.IGNORECASE), "Silver"),
    (re.compile(r'\b(?:platinum|プラチナ|白金)\b', re.IGNORECASE), "Platinum"),
    (re.compile(r'\b(?:palladium|パラジウム)\b', re.IGNORECASE), "Palladium"),
]

# サイズ抽出
SIZE_PATTERNS = [
    (re.compile(r'(\d+(?:/\d+)?)\s*(?:oz|オンス)', re.IGNORECASE), "oz"),
    (re.compile(r'(\d+)\s*g\b', re.IGNORECASE), "g"),
]


def extract_slab_key(title: str) -> dict:
    """タイトルからスラブ1行目相当の情報を抽出

    Returns:
        {
            "year": "1998",
            "mint": "P",
            "series": "Silver Eagle",
            "denom": "$1",
            "metal": "Silver",
            "size": "1oz",
            "slab_key": "1998-P-Silver Eagle-$1-Silver-1oz",
        }
    """
    result = {}

    # Year（「1908年」「1982年」の日本語表記にも対応）
    year_m = re.search(r'\b(1[0-9]{3}|20[0-2][0-9])\s*年?\b', title)
    result["year"] = year_m.group(1) if year_m else ""

    # Mint mark
    # Look for single letter after year or hyphen: "1998-P", "1908 D", "1885-S"
    mint = ""
    mint_m = re.search(r'\b(?:19|20)\d{2}[-\s]?([A-Z]{1,2})\b', title)
    if mint_m and mint_m.group(1) in MINT_MARKS:
        mint = mint_m.group(1)
    result["mint"] = mint

    # Metal
    metal = ""
    for pat, metal_name in METAL_PATTERNS:
        if pat.search(title):
            metal = metal_name
            break
    result["metal"] = metal

    # Series
    series = ""
    title_lower = title.lower()
    # Check longer patterns first to avoid partial matches
    sorted_keys = sorted(SERIES_NORMALIZE.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in title_lower:
            val = SERIES_NORMALIZE[key]
            if val is None:
                # "eagle" alone - needs metal context
                if metal == "Silver":
                    val = "Silver Eagle"
                elif metal == "Gold":
                    val = "Gold Eagle"
                elif metal == "Platinum":
                    val = "Platinum Eagle"
                else:
                    val = "Eagle"
            series = val
            break
    result["series"] = series

    # Denomination
    denom = ""
    for pat, currency in DENOMINATION_PATTERNS:
        m = pat.search(title)
        if m:
            # 「1円スタート」等を除外
            context = title[max(0, m.start()-5):m.end()+10]
            if "スタート" in context or "出品" in context:
                continue
            amount = m.group(1)
            if currency == "USD":
                denom = f"${amount}"
            elif currency == "GBP":
                denom = f"£{amount}"
            else:
                denom = f"{amount}{currency}"
            break
    result["denom"] = denom

    # Size
    size = ""
    for pat, unit in SIZE_PATTERNS:
        m = pat.search(title)
        if m:
            size = f"{m.group(1)}{unit}"
            break
    result["size"] = size

    # Grade type (PF vs MS vs PR vs AU) — PF and MS are completely different
    grade_type = ""
    grade_m = re.search(r'\b(PF|PR|MS|AU)\s*\d{1,2}', title, re.IGNORECASE)
    if grade_m:
        gt = grade_m.group(1).upper()
        if gt == "PR":
            gt = "PF"  # PR = PF (Proof)
        grade_type = gt
    result["grade_type"] = grade_type

    # Label (ER / FDOI / First Strike / First Releases)
    label = ""
    title_upper = title.upper()
    if "FIRST DAY" in title_upper or "FDOI" in title_upper:
        label = "FDOI"
    elif "FIRST RELEASE" in title_upper or "EARLY RELEASE" in title_upper:
        label = "FR"
    elif "FIRST STRIKE" in title_upper:
        label = "FS"
    result["label"] = label

    # Signed
    result["signed"] = bool(re.search(r'(?:signed|autograph|サイン)', title, re.IGNORECASE))

    # Deep Cameo / Ultra Cameo
    dcam = ""
    if re.search(r'DEEP\s*CAMEO|DCAM', title, re.IGNORECASE):
        dcam = "DCAM"
    elif re.search(r'ULTRA\s*CAMEO|UCAM|UC\b', title, re.IGNORECASE):
        dcam = "UCAM"
    result["cameo"] = dcam

    # Build slab key
    parts = [result["year"], result["mint"], result["series"],
             result["denom"], result["metal"], result["size"],
             result["grade_type"], result["label"]]
    result["slab_key"] = "-".join(p for p in parts if p)

    return result


def is_same_coin(ebay_info: dict, yahoo_info: dict) -> tuple[bool, str]:
    """2つのコイン情報が同一コイン種かを判定

    Returns:
        (is_match, reason)
    """
    mismatches = []

    # 必須一致: year
    if ebay_info["year"] != yahoo_info["year"]:
        return False, f"year: {ebay_info['year']} vs {yahoo_info['year']}"

    # 必須一致: metal
    if ebay_info["metal"] and yahoo_info["metal"]:
        if ebay_info["metal"] != yahoo_info["metal"]:
            return False, f"metal: {ebay_info['metal']} vs {yahoo_info['metal']}"

    # 必須一致: series (if both have it)
    if ebay_info["series"] and yahoo_info["series"]:
        if ebay_info["series"] != yahoo_info["series"]:
            return False, f"series: {ebay_info['series']} vs {yahoo_info['series']}"

    # 必須一致: denom — 両方あれば一致必須、片方だけある場合は不一致扱い
    ebay_denom = ebay_info.get("denom", "")
    yahoo_denom = yahoo_info.get("denom", "")
    if ebay_denom and yahoo_denom:
        if ebay_denom != yahoo_denom:
            return False, f"denom: {ebay_denom} vs {yahoo_denom}"
    elif ebay_denom or yahoo_denom:
        # 片方だけ額面がある場合、half/1/2の不一致を検出
        has_half_ebay = "half" in ebay_info.get("slab_key", "").lower() or "1/2" in str(ebay_denom)
        has_half_yahoo = "half" in yahoo_info.get("slab_key", "").lower() or "1/2" in str(yahoo_denom)
        if has_half_ebay != has_half_yahoo:
            return False, f"half mismatch: ebay={has_half_ebay} yahoo={has_half_yahoo}"

    # 必須一致: size — 両方あれば一致必須
    ebay_size = ebay_info.get("size", "")
    yahoo_size = yahoo_info.get("size", "")
    if ebay_size and yahoo_size:
        if ebay_size != yahoo_size:
            return False, f"size: {ebay_size} vs {yahoo_size}"

    # ミントマーク: 両方ある場合は一致必須
    if ebay_info["mint"] and yahoo_info["mint"]:
        if ebay_info["mint"] != yahoo_info["mint"]:
            return False, f"mint: {ebay_info['mint']} vs {yahoo_info['mint']}"

    # グレード種別: PF vs MS は完全別物
    if ebay_info.get("grade_type") and yahoo_info.get("grade_type"):
        if ebay_info["grade_type"] != yahoo_info["grade_type"]:
            return False, f"grade_type: {ebay_info['grade_type']} vs {yahoo_info['grade_type']}"

    # サイン: 片方だけサイン入りは別物
    if ebay_info.get("signed") != yahoo_info.get("signed"):
        if ebay_info.get("signed") or yahoo_info.get("signed"):
            return False, f"signed: {ebay_info.get('signed')} vs {yahoo_info.get('signed')}"

    # 一致項目数を数える — 最低4項目必須に引き上げ
    matched = 0
    for key in ["year", "metal", "series", "denom", "size", "grade_type"]:
        if ebay_info.get(key) and yahoo_info.get(key) and ebay_info[key] == yahoo_info[key]:
            matched += 1

    if matched < 4:
        return False, f"insufficient match fields: {matched}"

    return True, f"matched {matched} fields"


if __name__ == "__main__":
    # テスト
    tests = [
        ("1998 P PROOF AMERICAN SILVER EAGLE NGC PF70 ULTRA CAMEO 1 Oz",
         "1998 W 1/2 Oz Proof American Eagle NGC PF70 UCAM パラジウム"),
        ("1908-D Gold St. Gaudens Double Eagle No Motto $20 PCGS MS63",
         "アメリカ 20ドル金貨 1908年 PCGS MS63"),
        ("2026 King's Beast Lion of England 1 oz Silver Proof",
         "2026 英国 5ポンド銀貨 エリザベス2世 第2肖像 NGC PF70UC"),
        ("1982 Gold Panda 1/10 oz. NGC MS69",
         "中国 1/10oz金貨 1982年 パンダ NGC MS68"),
        ("1885-O Morgan Silver Dollar PCGS MS64",
         "1881年 モルガンダラー銀貨 Morgan Silver Dollar PCGS MS-65"),
    ]

    for ebay_title, yahoo_title in tests:
        e = extract_slab_key(ebay_title)
        y = extract_slab_key(yahoo_title)
        match, reason = is_same_coin(e, y)
        print(f"eBay:  {ebay_title[:55]}")
        print(f"  key: {e['slab_key']}")
        print(f"Yahoo: {yahoo_title[:55]}")
        print(f"  key: {y['slab_key']}")
        print(f"  => {'MATCH' if match else 'NO MATCH'}: {reason}")
        print()
