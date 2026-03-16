"""ROOM BOT v6.2 - Slack CEOコマンド連携（排他制御付き）

CEOがiPhone Slackからメッセージを送信し、
ROOM BOTの運用を制御する常駐プロセス。

起動:
  python slack_room_bot.py

Slackコマンド:
  room status                # 状態確認
  room on                    # 運用ON
  room off                   # 運用OFF
  room plus like N           # 臨時いいねN件
  room plus follow N         # 臨時フォローN件
  room plus post N           # 臨時投稿N件
  room generate-month        # 月間スケジュール生成

安全設計:
  - 排他制御: イベント重複排除 + 実行ロック + クールダウン
  - 未知コマンドは拒否 + ヘルプ表示
  - 全操作を slack_bot.log に記録
  - changed_by: "slack" で操作元を区別
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent

# .env 読み込み
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Slack トークン
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

# ログ設定
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

slack_logger = logging.getLogger("slack_room_bot")
slack_logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(
    LOG_DIR / "slack_bot.log", encoding="utf-8"
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
slack_logger.addHandler(file_handler)

console_handler = logging.StreamHandler(
    io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8"
    else sys.stdout
)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
slack_logger.addHandler(console_handler)

# ============================================================
# 排他制御（3層防御）
# ============================================================

class CommandGuard:
    """コマンドの重複実行を防止する3層ガード

    Layer 1: イベント重複排除 (event_ts)
      - Slackイベントの event_ts を記録し、同一イベントの再処理を防ぐ
      - Socket Mode再送やWebSocket再接続による二重配信を防止

    Layer 2: 実行ロック (action type)
      - 同じアクション種別（like/follow/post）の並行実行を防ぐ
      - スレッドロックで排他制御

    Layer 3: クールダウン (同一コマンド)
      - 同一コマンド文字列が短時間内に連続到着した場合にスキップ
      - CEO二重タップ防止
    """

    # クールダウン秒数
    COOLDOWN_SECONDS = 15

    # イベントTTL（古いイベントを自動クリーンアップ）
    EVENT_TTL_SECONDS = 300  # 5分

    def __init__(self):
        self._lock = threading.Lock()

        # Layer 1: 処理済みイベントTS
        self._processed_events: dict[str, float] = {}  # event_ts -> processed_at

        # Layer 2: アクション別実行ロック
        self._action_locks: dict[str, threading.Lock] = {}
        self._action_running: dict[str, str] = {}  # action -> command_text

        # Layer 3: コマンド別最終実行時刻
        self._last_command_time: dict[str, float] = {}  # command_text -> timestamp

    def check_event_duplicate(self, event_ts: str) -> bool:
        """Layer 1: 同一イベントの重複チェック

        Returns:
            True = 重複（スキップすべき）, False = 初回（処理してよい）
        """
        with self._lock:
            # 古いエントリをクリーンアップ
            now = time.time()
            expired = [
                ts for ts, t in self._processed_events.items()
                if now - t > self.EVENT_TTL_SECONDS
            ]
            for ts in expired:
                del self._processed_events[ts]

            # 重複チェック
            if event_ts in self._processed_events:
                return True  # 重複

            # 記録
            self._processed_events[event_ts] = now
            return False  # 初回

    def check_cooldown(self, command_text: str) -> bool:
        """Layer 3: 同一コマンドのクールダウンチェック

        Returns:
            True = クールダウン中（スキップすべき）, False = OK
        """
        with self._lock:
            now = time.time()
            last_time = self._last_command_time.get(command_text, 0)
            if now - last_time < self.COOLDOWN_SECONDS:
                return True  # クールダウン中
            self._last_command_time[command_text] = now
            return False

    def try_acquire_action(self, action: str, command_text: str) -> bool:
        """Layer 2: アクション実行ロック取得

        Returns:
            True = ロック取得成功（実行してよい）, False = 実行中（スキップ）
        """
        with self._lock:
            if action not in self._action_locks:
                self._action_locks[action] = threading.Lock()

        lock = self._action_locks[action]
        acquired = lock.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._action_running[action] = command_text
        return acquired

    def release_action(self, action: str) -> None:
        """Layer 2: アクション実行ロック解放"""
        with self._lock:
            self._action_running.pop(action, None)

        lock = self._action_locks.get(action)
        if lock:
            try:
                lock.release()
            except RuntimeError:
                pass  # 既にrelease済み

    def get_running_action(self, action: str) -> str | None:
        """実行中のコマンドを取得"""
        with self._lock:
            return self._action_running.get(action)


# グローバルインスタンス
_guard = CommandGuard()


def _get_action_type(args: list[str]) -> str:
    """コマンド引数からアクション種別を特定する

    排他制御の粒度:
      - "plus_like"   : room plus like N
      - "plus_follow" : room plus follow N
      - "plus_post"   : room plus post N
      - "quick"       : status/on/off/generate-month（即座完了のため排他不要）
    """
    if len(args) >= 2 and args[0] == "plus":
        return f"plus_{args[1]}"
    return "quick"


# ============================================================
# コマンド解析・実行
# ============================================================

# 許可コマンドパターン
ALLOWED_COMMANDS = [
    # (regex, description)
    (r"^room\s+status$", "状態確認"),
    (r"^room\s+on$", "運用ON"),
    (r"^room\s+off$", "運用OFF"),
    (r"^room\s+plus\s+post\s+(\d+)$", "臨時投稿"),
    (r"^room\s+plus\s+like\s+(\d+)$", "臨時いいね"),
    (r"^room\s+plus\s+follow\s+(\d+)$", "臨時フォロー"),
    (r"^room\s+generate-month$", "月間スケジュール生成"),
    (r"^room\s+generate-month\s+--month\s+(\d{4}-\d{2})$", "月間スケジュール生成(指定月)"),
]

HELP_TEXT = """*ROOM BOT CEOコマンド*

```
room status                  状態確認
room on                      運用ON
room off                     運用OFF
room plus post N             臨時投稿N件
room plus like N             臨時いいねN件
room plus follow N           臨時フォローN件
room generate-month          月間スケジュール生成
```"""


def parse_command(text: str) -> dict | None:
    """メッセージをコマンドに変換する

    Returns:
        {"args": [...], "description": "..."} or None
    """
    text = text.strip().lower()

    # "room" で始まらないメッセージは無視
    if not text.startswith("room"):
        return None

    for pattern, desc in ALLOWED_COMMANDS:
        m = re.match(pattern, text, re.IGNORECASE)
        if m:
            # run.py room 用の引数リストを構築
            parts = text.split()
            # "room" を除いた部分が run.py room のサブ引数
            args = parts[1:]  # ["status"] or ["plus", "like", "10"] etc.
            return {"args": args, "description": desc}

    # room で始まるが未知コマンド
    return {"error": f"未知のコマンドです: `{text}`\n\n{HELP_TEXT}"}


def execute_room_command(args: list[str]) -> str:
    """run.py room コマンドを実行して結果を返す"""
    cmd = [
        sys.executable, str(PROJECT_ROOT / "run.py"), "room"
    ] + args

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    slack_logger.info(f"実行: python run.py room {' '.join(args)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,  # 5分タイムアウト（like/followの実行時間考慮）
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        output = result.stdout.strip()
        if result.stderr.strip():
            # stderrにログ出力が混ざるので、ERRORのみ抽出
            err_lines = [
                l for l in result.stderr.strip().splitlines()
                if "ERROR" in l or "Traceback" in l or "Exception" in l
            ]
            if err_lines:
                output += "\n\n_stderr:_\n```\n" + "\n".join(err_lines[:5]) + "\n```"

        if result.returncode != 0 and not output:
            output = f"コマンド失敗 (exit code: {result.returncode})"

        slack_logger.info(f"結果: exit={result.returncode}, output={len(output)}chars")
        return output or "(出力なし)"

    except subprocess.TimeoutExpired:
        slack_logger.error("タイムアウト（5分）")
        return "タイムアウト: 実行が5分を超えました。ログを確認してください。"
    except Exception as e:
        slack_logger.error(f"実行エラー: {e}")
        return f"実行エラー: {e}"


# ============================================================
# Slack BOT（Socket Mode）
# ============================================================

def start_bot():
    """Slack BOTを起動する"""
    if not SLACK_BOT_TOKEN:
        print("エラー: SLACK_BOT_TOKEN が .env に設定されていません")
        print("")
        print(".env に以下を追加してください:")
        print("  SLACK_BOT_TOKEN=xoxb-xxxx")
        print("  SLACK_APP_TOKEN=xapp-xxxx")
        sys.exit(1)

    if not SLACK_APP_TOKEN:
        print("エラー: SLACK_APP_TOKEN が .env に設定されていません")
        print("")
        print("Socket Mode用のApp-Level Tokenが必要です:")
        print("  SLACK_APP_TOKEN=xapp-xxxx")
        sys.exit(1)

    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        print("エラー: slack-bolt がインストールされていません")
        print("")
        print("以下を実行してください:")
        print("  pip install slack-bolt slack-sdk")
        sys.exit(1)

    app = App(token=SLACK_BOT_TOKEN)

    @app.event("message")
    def handle_message(event, say):
        """メッセージイベントを処理する"""
        text = event.get("text", "").strip()
        user = event.get("user", "unknown")
        channel = event.get("channel", "unknown")
        event_ts = event.get("event_ts", "")
        client_msg_id = event.get("client_msg_id", "")

        # bot自身のメッセージは無視
        if event.get("bot_id"):
            return

        # "room" で始まらないメッセージは無視
        if not text.lower().startswith("room"):
            return

        slack_logger.info(
            f"受信: user={user} channel={channel} text={text} "
            f"event_ts={event_ts} client_msg_id={client_msg_id}"
        )

        # ── Layer 1: イベント重複排除 ──
        dedup_key = event_ts or client_msg_id or f"{user}_{text}_{time.time()}"
        if _guard.check_event_duplicate(dedup_key):
            slack_logger.info(f"スキップ(Layer1-重複イベント): event_ts={event_ts}")
            return

        # コマンド解析
        parsed = parse_command(text)

        if parsed is None:
            return  # roomで始まらない → 無視

        if "error" in parsed:
            say(parsed["error"])
            slack_logger.info(f"拒否: {parsed['error'][:50]}")
            return

        args = parsed["args"]
        desc = parsed["description"]
        action_type = _get_action_type(args)

        # ── Layer 3: クールダウン（quick系は除外） ──
        if action_type != "quick":
            command_key = text.lower().strip()
            if _guard.check_cooldown(command_key):
                slack_logger.info(
                    f"スキップ(Layer3-クールダウン): {text} "
                    f"({CommandGuard.COOLDOWN_SECONDS}秒以内の同一コマンド)"
                )
                say(
                    f":warning: `{desc}` は{CommandGuard.COOLDOWN_SECONDS}秒以内に"
                    f"同じコマンドが実行されたためスキップしました。"
                )
                return

        # ── Layer 2: 実行ロック（quick系は除外） ──
        if action_type != "quick":
            running_cmd = _guard.get_running_action(action_type)
            if not _guard.try_acquire_action(action_type, text):
                slack_logger.info(
                    f"スキップ(Layer2-実行中ロック): {text} "
                    f"(実行中: {running_cmd})"
                )
                say(
                    f":hourglass: `{desc}` は現在実行中のため、"
                    f"このリクエストはスキップしました。"
                )
                return

        try:
            # 実行中メッセージ
            say(f":rocket: `{desc}` を実行中...")

            # コマンド実行
            output = execute_room_command(args)

            # 結果をコードブロックで送信
            say(f"```\n{output}\n```")

        finally:
            # ロック解放（quick系は取得していないので不要）
            if action_type != "quick":
                _guard.release_action(action_type)

    @app.event("app_mention")
    def handle_mention(event, say):
        """@メンション時もコマンドを処理する"""
        text = event.get("text", "").strip()
        # メンション部分を除去
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        if not text.lower().startswith("room"):
            say(HELP_TEXT)
            return

        # 通常のメッセージ処理に委譲
        event["text"] = text
        handle_message(event, say)

    # 起動
    slack_logger.info("=" * 60)
    slack_logger.info("ROOM BOT Slack連携 v6.2 起動（排他制御付き）")
    slack_logger.info(f"  クールダウン: {CommandGuard.COOLDOWN_SECONDS}秒")
    slack_logger.info(f"  イベントTTL: {CommandGuard.EVENT_TTL_SECONDS}秒")
    slack_logger.info("=" * 60)

    print("=" * 60)
    print("ROOM BOT Slack連携 v6.2 起動中...（排他制御付き）")
    print(f"  クールダウン: {CommandGuard.COOLDOWN_SECONDS}秒")
    print("  Ctrl+C で停止")
    print("=" * 60)

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    start_bot()
