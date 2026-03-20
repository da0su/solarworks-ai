"""CEO通知モジュール - 音声 + Slack の統合通知

全コンポーネント（executor / scheduler / Slack BOT）から呼び出し可能。
通知種別ごとにBAT + Slack文言を管理する。

使い方:
    from notifier import notify, NotifyType

    # 承認要求
    notify(NotifyType.APPROVAL, detail="ログイン切れ検出")

    # Slack連携あり
    notify(NotifyType.APPROVAL, detail="CDP接続エラー", slack_fn=slack_notify)
"""

import subprocess
import threading
import time
from enum import Enum
from pathlib import Path
from logger.logger import setup_logger

logger = setup_logger()

# scripts/ ディレクトリ
SCRIPTS_DIR = Path(__file__).parent / "scripts"


class NotifyType(Enum):
    """通知種別"""
    APPROVAL = "approval_needed"    # 承認要求（確認をお願いします）
    POST_DONE = "post_done"         # 投稿完了
    LIKE_DONE = "like_done"         # いいね完了
    FOLLOW_DONE = "follow_done"     # フォロー完了
    DEV_DONE = "dev_done"           # 修正完了
    ERROR = "error"                 # エラー


# 通知種別 → BAT名 + Slack文言
_NOTIFY_CONFIG = {
    NotifyType.APPROVAL: {
        "bat": "notify_approval_needed.bat",
        "slack_emoji": ":rotating_light:",
        "slack_text": "確認をお願いします",
    },
    NotifyType.POST_DONE: {
        "bat": "notify_post_done.bat",
        "slack_emoji": ":white_check_mark:",
        "slack_text": "投稿が完了しました",
    },
    NotifyType.LIKE_DONE: {
        "bat": "notify_like_done.bat",
        "slack_emoji": ":white_check_mark:",
        "slack_text": "いいねが完了しました",
    },
    NotifyType.FOLLOW_DONE: {
        "bat": "notify_follow_done.bat",
        "slack_emoji": ":white_check_mark:",
        "slack_text": "フォローが完了しました",
    },
    NotifyType.DEV_DONE: {
        "bat": "notify_dev_done.bat",
        "slack_emoji": ":tools:",
        "slack_text": "修正が完了しました",
    },
    NotifyType.ERROR: {
        "bat": "notify_error.bat",
        "slack_emoji": ":x:",
        "slack_text": "エラーが発生しました",
    },
}

# 二重通知防止: 同一種別の最小間隔（秒）
_NOTIFY_COOLDOWN = 10
_last_notify_time: dict[str, float] = {}
_notify_lock = threading.Lock()


def notify(
    notify_type: NotifyType,
    detail: str = "",
    slack_fn=None,
    sound: bool = True,
) -> bool:
    """CEO通知を送信する

    Args:
        notify_type: 通知種別
        detail: 詳細メッセージ（例: "CDP接続エラー", "ログイン切れ検出"）
        slack_fn: Slack送信関数 (slack_notify) — Noneならスキップ
        sound: 音声を鳴らすか（デフォルトTrue）

    Returns:
        True=通知送信, False=クールダウン中でスキップ
    """
    config = _NOTIFY_CONFIG.get(notify_type)
    if not config:
        logger.warning(f"未定義の通知種別: {notify_type}")
        return False

    # 二重通知防止
    with _notify_lock:
        now = time.time()
        key = notify_type.value
        last = _last_notify_time.get(key, 0)
        if now - last < _NOTIFY_COOLDOWN:
            logger.debug(f"通知スキップ(クールダウン中): {key}")
            return False
        _last_notify_time[key] = now

    # Slack通知
    slack_text = config["slack_text"]
    if detail:
        slack_text = f"{config['slack_emoji']} {config['slack_text']}\n> {detail}"
    else:
        slack_text = f"{config['slack_emoji']} {config['slack_text']}"

    if slack_fn:
        try:
            slack_fn(slack_text)
        except Exception as e:
            logger.error(f"Slack通知エラー: {e}")

    logger.info(f"CEO通知: [{notify_type.value}] {detail or config['slack_text']}")

    # 音声通知（非同期 — メイン処理をブロックしない）
    if sound:
        bat_path = SCRIPTS_DIR / config["bat"]
        if bat_path.exists():
            threading.Thread(
                target=_play_sound,
                args=(str(bat_path),),
                daemon=True,
            ).start()
        else:
            logger.warning(f"通知BAT未存在: {bat_path}")

    return True


def _play_sound(bat_path: str) -> None:
    """バックグラウンドで通知音を再生"""
    try:
        subprocess.run(
            ["cmd.exe", "/c", bat_path],
            timeout=30,
            capture_output=True,
        )
    except Exception as e:
        logger.error(f"通知音再生エラー: {e}")
