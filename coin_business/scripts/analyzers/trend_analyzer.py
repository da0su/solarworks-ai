"""トレンド分析 - 価格データの分析・異常検知

蓄積された価格データを分析し、トレンド・異常値を検出する。
取得ロジックには一切依存しない（レイヤー分離原則）。
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")))
import config

logger = logging.getLogger(__name__)


def load_latest_prices() -> dict | None:
    """最新の価格データファイルを読み込む"""
    files = sorted(config.PRICES_DIR.glob("*_prices.json"), reverse=True)
    if not files:
        logger.warning("価格データが見つかりません")
        return None

    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


def detect_alerts(data: dict) -> list[dict]:
    """異常値を検知してアラートリストを返す"""
    alerts = []
    thresholds = config.ALERT_THRESHOLDS

    for coin in data.get("data", []):
        symbol = coin["symbol"]
        change_1h = coin.get("price_change_1h_pct") or 0

        # 急騰検知
        if change_1h >= thresholds["price_surge_1h_pct"]:
            alerts.append({
                "type": "SURGE",
                "symbol": symbol,
                "change_1h_pct": change_1h,
                "price_usd": coin["current_price_usd"],
                "severity": "HIGH",
                "message": f"{symbol} 急騰: 1h {change_1h:+.2f}% (${coin['current_price_usd']:,.2f})",
            })

        # 急落検知
        if change_1h <= thresholds["price_drop_1h_pct"]:
            alerts.append({
                "type": "DROP",
                "symbol": symbol,
                "change_1h_pct": change_1h,
                "price_usd": coin["current_price_usd"],
                "severity": "HIGH",
                "message": f"{symbol} 急落: 1h {change_1h:+.2f}% (${coin['current_price_usd']:,.2f})",
            })

    return alerts


def generate_market_summary(data: dict) -> dict:
    """市場サマリーを生成（結論→根拠→データ）"""
    coins = data.get("data", [])
    if not coins:
        return {"conclusion": "データなし"}

    # 全体の方向性を判定
    up_count = sum(1 for c in coins if (c.get("price_change_24h_pct") or 0) > 0)
    down_count = len(coins) - up_count
    avg_change = sum(c.get("price_change_24h_pct") or 0 for c in coins) / len(coins)

    if avg_change > 2:
        market_tone = "強気"
    elif avg_change > 0:
        market_tone = "やや強気"
    elif avg_change > -2:
        market_tone = "やや弱気"
    else:
        market_tone = "弱気"

    # 結論→根拠→データの構造
    summary = {
        "timestamp": data["timestamp"],
        "conclusion": f"市場全体は{market_tone}（24h平均変動率 {avg_change:+.2f}%）",
        "evidence": {
            "up_count": up_count,
            "down_count": down_count,
            "avg_24h_change_pct": round(avg_change, 2),
            "market_tone": market_tone,
        },
        "data": [
            {
                "symbol": c["symbol"],
                "price_usd": c["current_price_usd"],
                "change_24h_pct": round(c.get("price_change_24h_pct") or 0, 2),
                "change_7d_pct": round(c.get("price_change_7d_pct") or 0, 2),
                "volume_usd": c["total_volume_usd"],
            }
            for c in coins
        ],
    }

    return summary


def analyze() -> dict | None:
    """分析を実行して結果を返す"""
    logger.info("トレンド分析開始")
    data = load_latest_prices()
    if not data:
        return None

    alerts = detect_alerts(data)
    summary = generate_market_summary(data)

    result = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "alerts": alerts,
        "alert_count": len(alerts),
    }

    if alerts:
        logger.warning(f"アラート検知: {len(alerts)}件")
        for a in alerts:
            logger.warning(f"  {a['message']}")
    else:
        logger.info("アラートなし")

    logger.info(f"トレンド分析完了: {summary['conclusion']}")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    result = analyze()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
