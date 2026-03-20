"""ROOM BOT v2 - ログ管理"""

import json
import logging
from datetime import datetime
from pathlib import Path

import config


def setup_logger(name: str = "room_bot") -> logging.Logger:
    """ログファイル + コンソール出力のロガーを設定"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 日付ごとのログファイル
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = config.LOG_DIR / f"{today}.log"

    # ファイルハンドラ
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # コンソールハンドラ
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    ))

    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


def save_post_result(result: dict) -> None:
    """POST_LOG.json に投稿結果を追記"""
    log_path = config.POST_LOG_PATH

    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"log_name": "楽天ROOM投稿履歴", "version": "1.0", "posts": []}

    data["posts"].append(result)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_screenshot_path(label: str) -> Path:
    """スクリーンショットの保存パスを生成"""
    today = datetime.now().strftime("%Y-%m-%d")
    screenshot_dir = config.SCREENSHOT_DIR / today
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%H%M%S")
    return screenshot_dir / f"{timestamp}_{label}.png"
