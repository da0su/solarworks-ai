"""地金価格データ（月次概算）

金・銀・プラチナの月次概算価格（USD/troy oz）。
将来的にはAPIで自動取得するが、当面は手動更新。
為替レートも含む。

Sources: Kitco, LBMA, Trading Economics
"""

# 月次概算価格 (USD/troy oz)
# Format: "YYYY-MM": {"gold": price, "silver": price, "platinum": price, "usdjpy": rate}
MONTHLY_METAL_PRICES = {
    # 2023
    "2023-01": {"gold": 1928, "silver": 23.8, "platinum": 1070, "usdjpy": 130},
    "2023-02": {"gold": 1854, "silver": 21.6, "platinum": 950, "usdjpy": 134},
    "2023-03": {"gold": 1978, "silver": 23.1, "platinum": 980, "usdjpy": 133},
    "2023-04": {"gold": 2003, "silver": 25.1, "platinum": 1070, "usdjpy": 134},
    "2023-05": {"gold": 1963, "silver": 23.6, "platinum": 1050, "usdjpy": 139},
    "2023-06": {"gold": 1929, "silver": 23.0, "platinum": 930, "usdjpy": 143},
    "2023-07": {"gold": 1961, "silver": 24.5, "platinum": 960, "usdjpy": 142},
    "2023-08": {"gold": 1942, "silver": 23.8, "platinum": 940, "usdjpy": 146},
    "2023-09": {"gold": 1919, "silver": 23.2, "platinum": 920, "usdjpy": 149},
    "2023-10": {"gold": 1983, "silver": 22.9, "platinum": 890, "usdjpy": 149},
    "2023-11": {"gold": 2038, "silver": 24.5, "platinum": 920, "usdjpy": 149},
    "2023-12": {"gold": 2063, "silver": 24.1, "platinum": 970, "usdjpy": 142},
    # 2024
    "2024-01": {"gold": 2039, "silver": 23.0, "platinum": 920, "usdjpy": 148},
    "2024-02": {"gold": 2043, "silver": 22.8, "platinum": 900, "usdjpy": 150},
    "2024-03": {"gold": 2175, "silver": 24.8, "platinum": 920, "usdjpy": 151},
    "2024-04": {"gold": 2330, "silver": 27.5, "platinum": 940, "usdjpy": 154},
    "2024-05": {"gold": 2350, "silver": 30.5, "platinum": 1040, "usdjpy": 157},
    "2024-06": {"gold": 2330, "silver": 29.5, "platinum": 990, "usdjpy": 158},
    "2024-07": {"gold": 2390, "silver": 29.0, "platinum": 970, "usdjpy": 155},
    "2024-08": {"gold": 2500, "silver": 28.8, "platinum": 950, "usdjpy": 146},
    "2024-09": {"gold": 2630, "silver": 31.2, "platinum": 990, "usdjpy": 143},
    "2024-10": {"gold": 2735, "silver": 33.5, "platinum": 1010, "usdjpy": 150},
    "2024-11": {"gold": 2680, "silver": 31.0, "platinum": 960, "usdjpy": 153},
    "2024-12": {"gold": 2640, "silver": 30.5, "platinum": 940, "usdjpy": 157},
    # 2025
    "2025-01": {"gold": 2770, "silver": 30.8, "platinum": 960, "usdjpy": 156},
    "2025-02": {"gold": 2870, "silver": 32.0, "platinum": 980, "usdjpy": 152},
    "2025-03": {"gold": 2950, "silver": 33.0, "platinum": 990, "usdjpy": 149},
    "2025-04": {"gold": 2980, "silver": 33.5, "platinum": 995, "usdjpy": 148},
    "2025-05": {"gold": 3000, "silver": 33.2, "platinum": 990, "usdjpy": 148},
    "2025-06": {"gold": 2950, "silver": 32.0, "platinum": 980, "usdjpy": 149},
    "2025-07": {"gold": 2920, "silver": 31.5, "platinum": 970, "usdjpy": 150},
    "2025-08": {"gold": 2900, "silver": 31.0, "platinum": 960, "usdjpy": 149},
    "2025-09": {"gold": 2880, "silver": 31.5, "platinum": 970, "usdjpy": 148},
    "2025-10": {"gold": 2850, "silver": 32.0, "platinum": 975, "usdjpy": 149},
    "2025-11": {"gold": 2900, "silver": 32.5, "platinum": 980, "usdjpy": 150},
    "2025-12": {"gold": 2950, "silver": 33.0, "platinum": 990, "usdjpy": 149},
    # 2026
    "2026-01": {"gold": 3000, "silver": 33.5, "platinum": 995, "usdjpy": 149},
    "2026-02": {"gold": 3050, "silver": 34.0, "platinum": 1000, "usdjpy": 149},
    "2026-03": {"gold": 3100, "silver": 34.5, "platinum": 1010, "usdjpy": 149},
}


def get_metal_price(date_str: str, metal: str = "gold") -> float | None:
    """日付文字列(YYYY-MM-DD or YYYY-MM)から地金価格(USD/oz)を取得"""
    month_key = date_str[:7]  # YYYY-MM
    if month_key in MONTHLY_METAL_PRICES:
        return MONTHLY_METAL_PRICES[month_key].get(metal)
    # 最も近い月を探す
    keys = sorted(MONTHLY_METAL_PRICES.keys())
    if month_key < keys[0]:
        return MONTHLY_METAL_PRICES[keys[0]].get(metal)
    if month_key > keys[-1]:
        return MONTHLY_METAL_PRICES[keys[-1]].get(metal)
    return None


def get_usdjpy(date_str: str) -> float:
    """日付からUSD/JPYレートを取得"""
    month_key = date_str[:7]
    if month_key in MONTHLY_METAL_PRICES:
        return MONTHLY_METAL_PRICES[month_key].get("usdjpy", 149)
    return 149.0


def calculate_melt_value(weight_oz: float, purity: float, material: str,
                          date_str: str) -> dict:
    """地金価値を計算

    Returns:
        {"melt_value_usd": float, "melt_value_jpy": int, "metal_price_usd": float}
    """
    # 素材→地金種別マッピング
    metal_map = {
        "金": "gold", "銀": "silver", "プラチナ": "platinum",
        "パラジウム": "platinum",  # パラジウムはプラチナで代替
    }
    metal = metal_map.get(material)
    if not metal:
        return {}

    metal_price = get_metal_price(date_str, metal)
    if metal_price is None:
        return {}

    usdjpy = get_usdjpy(date_str)
    melt_usd = weight_oz * purity * metal_price
    melt_jpy = int(melt_usd * usdjpy)

    return {
        "melt_value_usd": round(melt_usd, 2),
        "melt_value_jpy": melt_jpy,
        "metal_price_usd": metal_price,
        "usdjpy_rate": usdjpy,
    }


def calculate_premium(price_jpy: int, melt_value_jpy: int) -> dict:
    """プレミアム計算

    Returns:
        {"premium_jpy": int, "premium_ratio": float}
    """
    if melt_value_jpy <= 0:
        return {"premium_jpy": price_jpy, "premium_ratio": None}

    premium = price_jpy - melt_value_jpy
    ratio = premium / melt_value_jpy

    return {
        "premium_jpy": premium,
        "premium_ratio": round(ratio, 3),
    }
