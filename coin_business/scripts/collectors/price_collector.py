"""価格データ取得 - CoinGecko API

対象通貨の価格・時価総額・24h変動率を取得し、タイムスタンプ付きで保存。
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import requests

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import config

logger = logging.getLogger(__name__)


def fetch_prices() -> dict:
    """CoinGecko APIから主要通貨の価格データを取得"""
    url = f"{config.COINGECKO_BASE_URL}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(config.TARGET_COINS),
        "order": "market_cap_desc",
        "sparkline": False,
        "price_change_percentage": "1h,24h,7d",
    }
    if config.COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = config.COINGECKO_API_KEY

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    raw = response.json()
    timestamp = datetime.utcnow().isoformat() + "Z"

    result = {
        "timestamp": timestamp,
        "source": "coingecko",
        "data": [],
    }

    for coin in raw:
        result["data"].append({
            "id": coin["id"],
            "symbol": coin["symbol"].upper(),
            "name": coin["name"],
            "current_price_usd": coin["current_price"],
            "market_cap_usd": coin["market_cap"],
            "total_volume_usd": coin["total_volume"],
            "price_change_1h_pct": coin.get("price_change_percentage_1h_in_currency"),
            "price_change_24h_pct": coin.get("price_change_percentage_24h_in_currency"),
            "price_change_7d_pct": coin.get("price_change_percentage_7d_in_currency"),
            "ath_usd": coin.get("ath"),
            "ath_change_pct": coin.get("ath_change_percentage"),
        })

    return result


def save_prices(data: dict) -> Path:
    """価格データをタイムスタンプ付きJSONで保存"""
    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_prices.json"
    filepath = config.PRICES_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"価格データ保存: {filepath}")
    return filepath


def collect() -> Path:
    """価格データの取得→保存を実行"""
    logger.info("価格データ取得開始")
    data = fetch_prices()
    filepath = save_prices(data)
    logger.info(f"価格データ取得完了: {len(data['data'])}通貨")
    return filepath


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    collect()
