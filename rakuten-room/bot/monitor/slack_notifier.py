"""ROOM BOT v5.0 - Slack Webhook通知

Incoming Webhook でSlackにレポート・アラートを送信する。
.env の SLACK_WEBHOOK_URL を使用。
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()


def send_message(text: str, channel: str = None) -> bool:
    """Slackにメッセージを送信

    Args:
        text: 送信テキスト
        channel: チャンネル名（省略時はWebhookデフォルト）

    Returns:
        bool: 送信成功/失敗
    """
    webhook_url = config.SLACK_WEBHOOK_URL
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL が未設定 - Slack通知スキップ")
        print(f"[Slack未設定] コンソール出力:\n{text}")
        return False

    payload = {"text": text}
    if channel:
        payload["channel"] = channel

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            if res.getcode() == 200:
                logger.info("Slack通知送信成功")
                return True
            else:
                logger.warning(f"Slack通知: HTTP {res.getcode()}")
                return False
    except urllib.error.HTTPError as e:
        logger.error(f"Slack通知エラー: HTTP {e.code}")
        return False
    except Exception as e:
        logger.error(f"Slack通知エラー: {e}")
        return False


def send_morning_report(date: str = None) -> bool:
    """朝9時レポートをSlackに送信"""
    from monitor.daily_report import generate_slack_morning
    date = date or datetime.now().strftime("%Y-%m-%d")
    text = generate_slack_morning(date)
    return send_message(text)


def send_night_report(date: str = None) -> bool:
    """23時日報+計画をSlackに送信"""
    from monitor.daily_report import generate_slack_night
    date = date or datetime.now().strftime("%Y-%m-%d")
    text = generate_slack_night(date)
    return send_message(text)


def send_alert(message: str) -> bool:
    """異常検知アラートをSlackに送信"""
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"[ALERT] {date}\n{message}"
    return send_message(text)


def send_critical_alert(date: str = None) -> bool:
    """CRITICALヘルスアラート"""
    from monitor.health_checker import check_health
    date = date or datetime.now().strftime("%Y-%m-%d")
    result = check_health(date)

    if result["status"] != "CRITICAL":
        return False

    text = f"[CRITICAL] {date}\nシステム異常検知\n"
    for w in result.get("warnings", []):
        text += f"- {w}\n"
    text += "自動停止しました。確認してください。"

    return send_message(text)
