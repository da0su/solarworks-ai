"""Solar Works ウォッチドッグ

scheduler.py の稼働を監視し、異常を検知するモニター。
- scheduler.log を60秒ごとに監視
- ERROR / FAILED / Traceback / timeout を検知 → 異常判定
- scheduler.log が長時間更新されない場合 → 異常判定
- 異常を logs/watchdog.log に記録
- Slack通知口を関数分離（Webhook未設定でも安全）

使い方:
  python watchdog.py

停止:
  Ctrl+C
"""

import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# === 設定 ===
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
SCHEDULER_LOG = LOGS_DIR / "scheduler.log"
WATCHDOG_LOG = LOGS_DIR / "watchdog.log"

# 監視間隔（秒）
CHECK_INTERVAL = 60

# scheduler.log がこの秒数以上更新されなければ異常判定
STALE_THRESHOLD = 600  # 10分

# 異常キーワード（大文字小文字を区別しない）
ERROR_KEYWORDS = ["ERROR", "FAILED", "Traceback", "timeout"]

# Slack Webhook URL（未設定時は空文字列）
SLACK_WEBHOOK_URL = os.environ.get("SOLARWORKS_SLACK_WEBHOOK", "")


# === ログ設定 ===
def setup_logger():
    """watchdog 用ロガーを作成する"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)

    # ファイルハンドラ
    fh = logging.FileHandler(WATCHDOG_LOG, encoding="utf-8")
    fh.setLevel(logging.INFO)

    # フォーマット
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logger()


def log(level, message):
    """ログファイル + PowerShell 両方に出力する"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{level}] {message}"
    print(line)

    if level == "ERROR":
        logger.error(message)
    elif level == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


# === Slack 通知 ===
def notify_slack(title, detail):
    """Slack に異常通知を送信する（Webhook 未設定でも落ちない）"""
    if not SLACK_WEBHOOK_URL:
        log("INFO", "[Slack] Webhook未設定のためスキップ")
        return False

    payload = {
        "text": f"🚨 *Solar Works Watchdog Alert*\n*{title}*\n```{detail}```"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log("INFO", "[Slack] 通知送信成功")
                return True
            else:
                log("WARNING", f"[Slack] 通知失敗 (HTTP {resp.status})")
                return False
    except urllib.error.URLError as e:
        log("WARNING", f"[Slack] 通知送信エラー: {e}")
        return False
    except Exception as e:
        log("WARNING", f"[Slack] 予期しないエラー: {e}")
        return False


# === 監視ロジック ===
def check_log_errors(last_position):
    """scheduler.log の新しい行をチェックし、異常キーワードを検知する

    Returns:
        tuple: (新しいファイル位置, 検知した異常リスト)
    """
    alerts = []

    if not SCHEDULER_LOG.exists():
        return last_position, alerts

    try:
        with open(SCHEDULER_LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_position)
            new_lines = f.readlines()
            new_position = f.tell()
    except Exception as e:
        log("ERROR", f"scheduler.log 読み取りエラー: {e}")
        return last_position, alerts

    for line in new_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        for keyword in ERROR_KEYWORDS:
            if keyword.lower() in line_stripped.lower():
                alerts.append(line_stripped)
                break  # 1行に複数キーワードがあっても1回だけ記録

    return new_position, alerts


def check_log_stale():
    """scheduler.log が長時間更新されていないかチェックする

    Returns:
        tuple: (異常かどうか, 経過秒数)
    """
    if not SCHEDULER_LOG.exists():
        return True, -1  # ファイル自体がない

    try:
        mtime = SCHEDULER_LOG.stat().st_mtime
        elapsed = time.time() - mtime
        return elapsed > STALE_THRESHOLD, elapsed
    except Exception as e:
        log("ERROR", f"scheduler.log stat エラー: {e}")
        return False, 0


# === メイン ===
def main():
    log("INFO", "=" * 50)
    log("INFO", "Solar Works ウォッチドッグ 起動")
    log("INFO", f"監視対象: {SCHEDULER_LOG}")
    log("INFO", f"監視間隔: {CHECK_INTERVAL}秒")
    log("INFO", f"無更新閾値: {STALE_THRESHOLD}秒")
    log("INFO", f"異常キーワード: {ERROR_KEYWORDS}")
    log("INFO", f"Slack通知: {'有効' if SLACK_WEBHOOK_URL else '無効（Webhook未設定）'}")
    log("INFO", "=" * 50)

    # 既存ログの末尾から監視開始（起動前のエラーは無視）
    last_position = 0
    if SCHEDULER_LOG.exists():
        last_position = SCHEDULER_LOG.stat().st_size
        log("INFO", f"既存ログ末尾から監視開始 (position: {last_position})")

    # 連続 stale 通知の抑制（同じ状態を何度も通知しない）
    stale_notified = False

    try:
        while True:
            # --- チェック1: 新しいエラー行の検知 ---
            last_position, alerts = check_log_errors(last_position)

            if alerts:
                stale_notified = False  # ログ更新があったのでリセット
                for alert_line in alerts:
                    log("ERROR", f"[異常検知] {alert_line}")

                title = f"scheduler.log に異常検知 ({len(alerts)}件)"
                detail = "\n".join(alerts[:10])  # 最大10行
                notify_slack(title, detail)

            # --- チェック2: ログ無更新チェック ---
            is_stale, elapsed = check_log_stale()

            if is_stale and not stale_notified:
                if elapsed < 0:
                    msg = "scheduler.log が存在しません（scheduler未起動の可能性）"
                else:
                    minutes = int(elapsed // 60)
                    msg = f"scheduler.log が {minutes}分間 更新されていません"

                log("WARNING", f"[無更新検知] {msg}")
                notify_slack("Scheduler 無更新アラート", msg)
                stale_notified = True

            elif not is_stale:
                stale_notified = False  # 更新が再開されたらリセット

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("INFO", "ウォッチドッグ停止 (Ctrl+C)")
        sys.exit(0)


if __name__ == "__main__":
    main()
