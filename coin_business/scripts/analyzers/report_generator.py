"""レポート生成 - 日次/週次レポートを outputs/ に出力

アウトプット設計原則: 結論→根拠→データ の順で整理。
CEOがすぐ判断できる形に整える。
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import config
from scripts.analyzers.trend_analyzer import analyze

logger = logging.getLogger(__name__)


def generate_daily_report() -> Path | None:
    """日次レポートを生成して outputs/daily/ に保存"""
    logger.info("日次レポート生成開始")

    analysis = analyze()
    if not analysis:
        logger.error("分析データなし。レポート生成中止")
        return None

    report = {
        "report_type": "daily",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "date": datetime.now().strftime("%Y-%m-%d"),

        # 結論（CEOが最初に見る）
        "conclusion": analysis["summary"]["conclusion"],

        # アラート（即時判断が必要なもの）
        "alerts": analysis["alerts"],

        # 根拠（結論の裏付け）
        "evidence": analysis["summary"]["evidence"],

        # データ（詳細を確認したい場合）
        "market_data": analysis["summary"]["data"],
    }

    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d')}_daily.json"
    filepath = config.DAILY_OUTPUT_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"日次レポート保存: {filepath}")
    return filepath


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    generate_daily_report()
