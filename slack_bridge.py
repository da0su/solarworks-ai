"""AI間Slack連携ブリッジ v2.0
キャップさん <-> サイバーさん がSlack経由で構造化メッセージをやり取り

Usage:
    python slack_bridge.py watch --interval 5          # 常時監視（5秒）
    python slack_bridge.py send-task --task test-ping --to cyber
    python slack_bridge.py send-task --task ebay-search --to cyber
    python slack_bridge.py retry-pending                # 未完了タスクのリトライ
    python slack_bridge.py status                       # 現在のタスク状態一覧
    python slack_bridge.py set-sender cap               # 送信者ID設定
    python slack_bridge.py state-summary                # system_state サマリー
    python slack_bridge.py state-audit                  # state整合性チェック
    python slack_bridge.py approve --task ebay-review --by cap  # cap承認

    # 旧互換
    python slack_bridge.py send "メッセージ"
    python slack_bridge.py receive
    python slack_bridge.py read
"""
import os
import sys
import time
import json
import uuid
import argparse
import subprocess
import threading
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone, timedelta

# root .env から SLACK_BOT_TOKEN を読み込む（Git管理外）
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("slack_sdk がインストールされていません。")
    print("pip install slack_sdk を実行してください。")
    sys.exit(1)


# ============================================================
# 定数・設定
# ============================================================
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
AI_BRIDGE_CHANNEL = "C0ALNTMJ2JZ"
CEO_ROOM_CHANNEL = "C0ALSAPMYHY"
BRIDGE_DELIMITER = "---BRIDGE_MSG---"
MSG_VERSION = "1.0"

# タイムアウト設定
ACK_TIMEOUT_SEC = 180       # 3分
DONE_TIMEOUT_SEC = 1800     # 30分
MAX_RETRIES = 2             # 再送最大2回（計3回試行）
RETRY_CHECK_INTERVAL = 30   # 30秒ごとにリトライチェック

# データディレクトリ
DATA_DIR = Path.home() / ".slack_bridge"
TASK_REGISTRY_FILE = DATA_DIR / "task_registry.jsonl"
PENDING_TASKS_FILE = DATA_DIR / "pending_tasks.jsonl"
LAST_SEEN_FILE = DATA_DIR / "last_seen.txt"
SENDER_FILE = DATA_DIR / "sender.txt"
LOG_FILE = DATA_DIR / "bridge.log"
EVENTS_FILE = DATA_DIR / "events.jsonl"
LATEST_MSG_FILE = DATA_DIR / "latest_msg.txt"

# 旧ファイルパス（マイグレーション用）
OLD_SENDER_FILE = Path.home() / ".slack_bridge_sender"
OLD_LAST_SEEN_FILE = Path.home() / ".slack_bridge_last_seen"
OLD_LATEST_MSG_FILE = Path.home() / ".slack_bridge_latest_msg"

# ACK不要のメッセージタイプ（ループ防止）
NO_ACK_TYPES = {"ACK", "DONE", "ERROR", "BLOCKED", "ESCALATE"}

# メッセージ起動元
SOURCE_AUTO   = "auto"    # 依存チェーンによる自動起動
SOURCE_MANUAL = "manual"  # 人手による起動

# State管理ファイル
STATE_FILE = Path(__file__).parent / "state" / "system_state.json"
MAX_HISTORY = 50  # recent_historyの最大保持件数

# 定時スケジュール（JST）
SCHEDULE_SLOTS = ["07:30", "12:30", "18:30"]
SCHEDULE_STATE_FILE = DATA_DIR / "schedule_state.json"
SCHEDULE_CHECK_INTERVAL = 60  # 60秒ごとにスロット確認

# GPT申し送りディレクトリ
GPT_MOUSIOKURI_DIR = Path(__file__).parent / "gpt_mousiokuri"


# ============================================================
# 初期化
# ============================================================
def ensure_data_dir():
    """データディレクトリを作成し、旧ファイルをマイグレーション"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 旧sender → 新sender
    if OLD_SENDER_FILE.exists() and not SENDER_FILE.exists():
        SENDER_FILE.write_text(OLD_SENDER_FILE.read_text().strip(), encoding="utf-8")

    # 旧last_seen → 新last_seen
    if OLD_LAST_SEEN_FILE.exists() and not LAST_SEEN_FILE.exists():
        LAST_SEEN_FILE.write_text(OLD_LAST_SEEN_FILE.read_text().strip(), encoding="utf-8")


ensure_data_dir()


# ============================================================
# State管理（system_state.json 原子的更新）v4.0
# ============================================================

# ERROR分類
class ErrorType:
    CONFIG_MISSING   = "CONFIG_MISSING"    # 設定・ファイル不足
    ACK_TIMEOUT      = "ACK_TIMEOUT"       # ACK待ちタイムアウト
    DONE_TIMEOUT     = "DONE_TIMEOUT"      # DONE待ちタイムアウト
    EXECUTION_ERROR  = "EXECUTION_ERROR"   # ハンドラ実行時例外
    MANUAL_REQUIRED  = "MANUAL_REQUIRED"   # 人手介入が必要
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED" # 依存タスクが失敗
    UNKNOWN          = "UNKNOWN"           # 分類不能

# タスク依存関係マップ（タスク名 → 前提タスク名 / 次タスク名）
# format: task_name -> {"requires": prev_task, "next": next_task, "to": default_receiver}
TASK_FLOW: dict[str, dict] = {
    "git-pull":    {"requires": None,           "next": "ebay-search", "to": "cyber"},
    "ebay-search": {"requires": "git-pull",     "next": "ebay-review", "to": "cyber"},
    "ebay-review": {"requires": "ebay-search",  "next": "ceo-report",  "to": "cap"},
    "ceo-report":  {"requires": "ebay-review",  "next": None,          "to": "cap"},
    "test-ping":      {"requires": None,             "next": None,            "to": "cyber"},
    "report":         {"requires": None,             "next": None,            "to": "cyber"},
    "set-env":        {"requires": None,             "next": None,            "to": "cyber"},
    "rakuten-status": {"requires": None,             "next": "rakuten-report","to": "cyber"},
    "rakuten-report": {"requires": "rakuten-status", "next": None,            "to": "cap"},
    "daily-check":    {"requires": None,             "next": "daily-report",  "to": "cyber"},
    "daily-report":   {"requires": "daily-check",    "next": None,            "to": "cap"},
    "coin-status":    {"requires": None,             "next": "coin-report",   "to": "cyber"},
    "coin-report":    {"requires": "coin-status",    "next": None,            "to": "cap"},
}
# 後方互換のため TASK_DEPS も維持
TASK_DEPS: dict[str, str | None] = {k: v.get("next") for k, v in TASK_FLOW.items()}

# 全ステータス
class TaskStatus:
    QUEUED        = "queued"         # 送信済み ACK待ち
    WAITING_ACK   = "waiting_ack"    # ACK明示待ち（queued の別名、summary用）
    RECEIVED      = "received"       # 受信側で受信済み
    ACKNOWLEDGED  = "acknowledged"   # ACK送受信済み
    RUNNING       = "running"        # ハンドラ実行中
    WAITING_DONE  = "waiting_done"   # DONE明示待ち
    WAITING_MANUAL = "waiting_manual" # 人手介入待ち
    BLOCKED       = "blocked"        # 依存タスク未完了/失敗でブロック
    DONE          = "done"           # 正常完了
    ERROR         = "error"          # エラー終了

# 状態 → next_action テキスト（機械的に決まる）
_NEXT_ACTION_MAP: dict[str, str] = {
    TaskStatus.QUEUED:         "waiting ACK from receiver",
    TaskStatus.WAITING_ACK:    "waiting ACK from receiver",
    TaskStatus.RECEIVED:       "sending ACK to sender",
    TaskStatus.ACKNOWLEDGED:   "executing task handler",
    TaskStatus.RUNNING:        "waiting for handler completion",
    TaskStatus.WAITING_DONE:   "waiting DONE from executor",
    TaskStatus.WAITING_MANUAL: "waiting for manual intervention",
    TaskStatus.BLOCKED:        "waiting for dependency to complete",
    TaskStatus.DONE:           "",   # 動的に算出
    TaskStatus.ERROR:          "",   # 動的に算出
}

# エラーメッセージ → ErrorType の優先マッチリスト（長いパターンを先に置く）
_ERROR_KEYWORDS: list[tuple[str, str]] = [
    ("DONE timeout", ErrorType.DONE_TIMEOUT),    # "timeout" より先にチェック
    ("timed out",    ErrorType.ACK_TIMEOUT),
    ("timeout",      ErrorType.ACK_TIMEOUT),
    ("not found",    ErrorType.CONFIG_MISSING),
    ("Script not",   ErrorType.CONFIG_MISSING),
    ("interrupted",  ErrorType.MANUAL_REQUIRED),
]

JST = timezone(timedelta(hours=9))


def _classify_error(msg: str) -> str:
    """エラーメッセージからErrorTypeを自動判定"""
    for keyword, etype in _ERROR_KEYWORDS:
        if keyword.lower() in msg.lower():
            return etype
    return ErrorType.EXECUTION_ERROR


def _calc_next_action(status: str, task_name: str, error_type: str = None,
                      waiting_for: str = None, depends_on_next: str = None) -> str | None:
    """状態に応じて次アクションを機械的に算出"""
    if status == "done":
        if depends_on_next:
            return f"trigger next task: {depends_on_next}"
        return None
    if status == "error":
        if error_type == ErrorType.CONFIG_MISSING:
            return "fix config / check .env then retry"
        if error_type in (ErrorType.ACK_TIMEOUT, ErrorType.DONE_TIMEOUT):
            return "check receiver status then retry-pending"
        if error_type == ErrorType.MANUAL_REQUIRED:
            return "manual intervention required"
        return "review error log then retry"
    if status == "queued" and waiting_for:
        return f"waiting ACK from {waiting_for}"
    return _NEXT_ACTION_MAP.get(status)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _timeout_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class StateManager:
    """system_state.json の読み書き。atomic write で破損防止。v3.0"""

    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not self.path.exists():
            return self._default_state()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return self._default_state()

    def save(self, state: dict):
        """atomic write: tmpに書いてrenameで置き換え"""
        state["updated_at"] = datetime.now(JST).isoformat()
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def _default_state(self) -> dict:
        return {
            "version": "3.0",
            "updated_at": datetime.now(JST).isoformat(),
            "system_status": "idle",
            "next_action": None,
            "current_tasks": [],
            "recent_history": [],
        }

    def _make_task_entry(self, task_id: str, task_name: str, owner: str,
                         status: str, waiting_for: str = None,
                         depends_on: str = None, max_retries: int = 3,
                         timeout_sec: int = ACK_TIMEOUT_SEC,
                         source: str = SOURCE_MANUAL,
                         workflow_id: str = None) -> dict:
        """タスクエントリを標準フォーマットで生成"""
        depends_on_next = TASK_DEPS.get(task_name)
        return {
            "task_id":      task_id,
            "task_name":    task_name,
            "owner":        owner,
            "status":       status,
            "source":       source,
            "workflow_id":  workflow_id or str(uuid.uuid4()),
            "created_at":   _now_iso(),
            "updated_at":   _now_iso(),
            "waiting_for":  waiting_for,
            "depends_on":   depends_on,
            "depends_on_next": depends_on_next,
            "retry_count":  0,
            "max_retries":  max_retries,
            "timeout_at":   _timeout_iso(timeout_sec),
            "last_error":   None,
            "error_type":   None,
            "next_action":  _calc_next_action(status, task_name, waiting_for=waiting_for),
            "review_status": None,
            "approved_by":   None,
            "approved_at":   None,
            "report_status":   None,
            "reported_at":     None,
            "reported_channel": None,
        }

    # --- 状態遷移メソッド ---

    def task_received(self, task_id: str, task_name: str, owner: str,
                      depends_on: str = None, workflow_id: str = None):
        """TASK受信時 (status=received)"""
        state = self.load()
        entry = self._make_task_entry(
            task_id, task_name, owner, "received",
            depends_on=depends_on, timeout_sec=DONE_TIMEOUT_SEC,
            workflow_id=workflow_id,
        )
        state["current_tasks"].append(entry)
        state["system_status"] = "busy"
        state["next_action"] = _calc_next_action("received", task_name)
        self.save(state)

    def task_queued(self, task_id: str, task_name: str, owner: str, to: str,
                    depends_on: str = None, workflow_id: str = None):
        """タスク送信時 (status=queued)"""
        state = self.load()
        entry = self._make_task_entry(
            task_id, task_name, owner, "queued",
            waiting_for=to, depends_on=depends_on,
            timeout_sec=ACK_TIMEOUT_SEC,
            workflow_id=workflow_id,
        )
        state["current_tasks"].append(entry)
        state["system_status"] = "busy"
        state["next_action"] = _calc_next_action("queued", task_name, waiting_for=to)
        self.save(state)

    def task_acknowledged(self, task_id: str):
        """ACK受信/送信時 (status=acknowledged)"""
        state = self.load()
        for t in state["current_tasks"]:
            if t["task_id"] == task_id:
                t["status"] = "acknowledged"
                t["waiting_for"] = None
                t["timeout_at"] = _timeout_iso(DONE_TIMEOUT_SEC)
                t["updated_at"] = _now_iso()
                t["next_action"] = _calc_next_action("acknowledged", t.get("task_name", ""))
                break
        state["next_action"] = _calc_next_action("acknowledged", "")
        self.save(state)

    def task_running(self, task_id: str):
        """実行開始時 (status=running)"""
        state = self.load()
        for t in state["current_tasks"]:
            if t["task_id"] == task_id:
                t["status"] = "running"
                t["updated_at"] = _now_iso()
                t["next_action"] = _calc_next_action("running", t.get("task_name", ""))
                break
        state["next_action"] = "waiting for handler completion"
        self.save(state)

    def task_done(self, task_id: str, result_summary: str = None):
        """完了時 → recent_historyへ移動"""
        state = self.load()
        task = self._pop_task(state, task_id)
        if task:
            task["status"] = "done"
            task["updated_at"] = _now_iso()
            task["timeout_at"] = None
            if result_summary:
                task["result"] = result_summary
            depends_on_next = task.get("depends_on_next")
            task["next_action"] = _calc_next_action(
                "done", task.get("task_name", ""), depends_on_next=depends_on_next)
            state["recent_history"].insert(0, task)
            state["recent_history"] = state["recent_history"][:MAX_HISTORY]
        state["system_status"] = "busy" if state["current_tasks"] else "idle"
        state["next_action"] = (
            state["current_tasks"][0].get("next_action") if state["current_tasks"] else None
        )
        self.save(state)

    def task_error(self, task_id: str, error_msg: str,
                   error_type: str = None):
        """エラー時 → recent_historyへ移動"""
        etype = error_type or _classify_error(error_msg)
        state = self.load()
        task = self._pop_task(state, task_id)
        if task:
            task["status"] = "error"
            task["updated_at"] = _now_iso()
            task["timeout_at"] = None
            task["last_error"] = error_msg
            task["error_type"] = etype
            task["next_action"] = _calc_next_action("error", task.get("task_name", ""), etype)
            state["recent_history"].insert(0, task)
            state["recent_history"] = state["recent_history"][:MAX_HISTORY]
        # system_status: 他に active なタスクがあれば busy、なければ error
        active = [t for t in state["current_tasks"]
                  if t.get("status") not in (TaskStatus.BLOCKED,)]
        state["system_status"] = "busy" if active else "error"
        state["next_action"] = task.get("next_action") if task else "review error"
        self.save(state)

    def task_retry(self, task_id: str):
        """リトライ時: retry_countをインクリメント"""
        state = self.load()
        for t in state["current_tasks"]:
            if t["task_id"] == task_id:
                t["retry_count"] = t.get("retry_count", 0) + 1
                t["status"] = "queued"
                t["last_error"] = None
                t["error_type"] = None
                t["timeout_at"] = _timeout_iso(ACK_TIMEOUT_SEC)
                t["updated_at"] = _now_iso()
                t["next_action"] = _calc_next_action("queued", t.get("task_name", ""),
                                                      waiting_for=t.get("waiting_for"))
                break
        self.save(state)

    def task_waiting_manual(self, task_id: str, reason: str):
        """人手介入待ち (status=waiting_manual)"""
        state = self.load()
        for t in state["current_tasks"]:
            if t["task_id"] == task_id:
                t["status"] = TaskStatus.WAITING_MANUAL
                t["last_error"] = reason
                t["error_type"] = ErrorType.MANUAL_REQUIRED
                t["updated_at"] = _now_iso()
                t["next_action"] = _calc_next_action(
                    TaskStatus.WAITING_MANUAL, t.get("task_name", ""))
                break
        state["system_status"] = "waiting_manual"
        state["next_action"] = "manual intervention required"
        self.save(state)

    def task_blocked(self, task_id: str, blocked_by: str):
        """依存タスク未完了/失敗によるブロック (status=blocked)"""
        state = self.load()
        for t in state["current_tasks"]:
            if t["task_id"] == task_id:
                t["status"] = TaskStatus.BLOCKED
                t["last_error"] = f"blocked by: {blocked_by}"
                t["error_type"] = ErrorType.DEPENDENCY_FAILED
                t["updated_at"] = _now_iso()
                t["next_action"] = f"wait for {blocked_by} to complete"
                break
        state["system_status"] = "blocked"
        state["next_action"] = f"resolve dependency: {blocked_by}"
        self.save(state)

    # --- 依存制御 ---

    def check_dependency(self, task_name: str) -> tuple[bool, str | None]:
        """
        依存タスクが完了しているか確認。
        ceo-report は ebay-review が done かつ review_status=approved が必須。
        Returns: (can_run, blocked_reason)
          can_run=True  → 実行可能
          can_run=False → blocked_reason に理由
        """
        flow = TASK_FLOW.get(task_name, {})
        requires = flow.get("requires")
        if requires is None:
            return True, None

        state = self.load()
        # current_tasks に requires が処理中ならブロック
        for t in state.get("current_tasks", []):
            if t.get("task_name") == requires:
                s = t.get("status", "")
                if s in (TaskStatus.ERROR, TaskStatus.BLOCKED):
                    return False, f"{requires} failed (status={s})"
                return False, f"{requires} still in progress (status={s})"

        # recent_history で requires が done を確認
        for t in state.get("recent_history", []):
            if t.get("task_name") == requires:
                if t.get("status") != TaskStatus.DONE:
                    return False, f"{requires} ended with status={t.get('status')}"
                # ceo-report は ebay-review の承認が必須
                if task_name == "ceo-report" and requires == "ebay-review":
                    rev = t.get("review_status")
                    if rev != "approved":
                        return False, (
                            f"ebay-review not approved by cap "
                            f"(review_status={rev or 'pending'}). "
                            f"Run: python slack_bridge.py approve --task ebay-review"
                        )
                return True, None

        # history にも存在しない → 未実行
        return False, f"{requires} has not run yet"

    def enqueue_next(self, from_task_name: str, from_task_id: str,
                     parent_workflow_id: str = None) -> str | None:
        """
        from_task_name が done のとき、TASK_FLOW の next タスクを
        state に queued エントリとして自動追加し、task_id を返す。
        実際の Slack 送信は呼び出し側が行う。

        安全ガード:
        - current_tasks に同名の未完了タスクがある場合はスキップ
        - recent_history の直近に同名の done があり、完了時刻が60秒以内の場合はスキップ（重複起動防止）
        - source=auto を付与

        parent_workflow_id:
        - 指定された場合はそれを使用（親コンテキスト直接継承・優先）
        - 省略時は recent_history から検索（fallback）

        Returns: new task_id or None (skip の場合)
        """
        flow = TASK_FLOW.get(from_task_name, {})
        next_task = flow.get("next")
        if not next_task:
            return None

        state = self.load()

        # ガード1: current_tasks に同名の未完了タスクが存在する
        for t in state.get("current_tasks", []):
            if t.get("task_name") == next_task and t.get("status") not in (
                    TaskStatus.DONE, TaskStatus.ERROR):
                logger.info(f"enqueue_next: {next_task} already active (status={t.get('status')}), skip")
                return None

        # ガード2: recent_history の直近60秒以内に同名 done がある（重複起動防止）
        now_utc = datetime.now(timezone.utc)
        for t in state.get("recent_history", [])[:5]:
            if t.get("task_name") == next_task and t.get("status") == TaskStatus.DONE:
                updated = t.get("updated_at", "")
                try:
                    dt = datetime.fromisoformat(updated)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now_utc - dt).total_seconds() < 60:
                        logger.info(f"enqueue_next: {next_task} completed <60s ago, skip duplicate")
                        return None
                except ValueError:
                    pass

        # 親 workflow_id を引き継ぐ（親コンテキスト直接継承を優先、なければ history から検索）
        parent_wf_id = parent_workflow_id
        if parent_wf_id is None:
            for t in state.get("recent_history", []):
                if t.get("task_name") == from_task_name and t.get("status") == TaskStatus.DONE:
                    parent_wf_id = t.get("workflow_id")
                    break

        new_id = str(uuid.uuid4())
        owner  = get_sender()
        to     = flow.get("to", "cyber")
        entry  = self._make_task_entry(
            new_id, next_task, owner, TaskStatus.QUEUED,
            waiting_for=to, depends_on=from_task_name,
            source=SOURCE_AUTO,
            workflow_id=parent_wf_id,
        )
        state["current_tasks"].append(entry)
        state["system_status"] = "busy"
        state["next_action"] = f"[auto] enqueued {next_task} -> {to}"
        self.save(state)
        logger.info(f"enqueue_next: {from_task_name} done -> auto-enqueue {next_task} (id={new_id[:8]})")
        return new_id

    def approve_task(self, task_name: str, approved_by: str) -> bool:
        """
        直近の done タスク（task_name）に承認フラグを設定。
        ceo-report 自動起動の条件として使用。
        Returns: True if approval was recorded, False if task not found.
        """
        state = self.load()
        for t in state.get("recent_history", []):
            if t.get("task_name") == task_name and t.get("status") == TaskStatus.DONE:
                t["review_status"] = "approved"
                t["approved_by"]   = approved_by
                t["approved_at"]   = _now_iso()
                self.save(state)
                logger.info(f"approve_task: {task_name} approved by {approved_by}")
                log_event("approved", {"task": task_name, "approved_by": approved_by})
                return True
        logger.warning(f"approve_task: no done {task_name} found in history")
        return False

    def is_approved(self, task_name: str) -> bool:
        """task_name の直近 done エントリが review_status=approved か確認"""
        state = self.load()
        for t in state.get("recent_history", []):
            if t.get("task_name") == task_name and t.get("status") == TaskStatus.DONE:
                return t.get("review_status") == "approved"
        return False

    def task_report_sent(self, task_id: str, channel: str,
                         status: str = "sent") -> bool:
        """
        ceo-report 送信結果を記録する。
        recent_history の該当エントリに report_status / reported_at / reported_channel を設定。
        Returns: True if entry found and updated.
        """
        state = self.load()
        # recent_history を優先（task_done 後は history に移動済み）
        for t in state.get("recent_history", []):
            if t.get("task_id") == task_id:
                t["report_status"]   = status
                t["reported_at"]     = _now_iso()
                t["reported_channel"] = channel
                self.save(state)
                logger.info(f"task_report_sent: {task_id[:8]} status={status} channel={channel}")
                return True
        # まだ current_tasks にある場合（稀）
        for t in state.get("current_tasks", []):
            if t.get("task_id") == task_id:
                t["report_status"]   = status
                t["reported_at"]     = _now_iso()
                t["reported_channel"] = channel
                self.save(state)
                return True
        logger.warning(f"task_report_sent: task_id {task_id[:8]} not found")
        return False

    def get_task_by_id(self, task_id: str) -> dict | None:
        """current_tasks + recent_history からタスクを検索"""
        state = self.load()
        for t in state.get("current_tasks", []):
            if t["task_id"] == task_id:
                return t
        for t in state.get("recent_history", []):
            if t["task_id"] == task_id:
                return t
        return None

    # --- 監査 ---

    def audit(self) -> list[dict]:
        """
        state の整合性チェック。問題リストを返す。
        - DONE なのに current_tasks に残っている
        - error なのに retry_count が max_retries 未満なのにそのまま
        - blocked なのに depends_on が存在しない
        - timeout_at を過ぎているのに running のまま
        """
        issues = []
        state = self.load()
        now_utc = datetime.now(timezone.utc)

        for t in state.get("current_tasks", []):
            tid  = t.get("task_id", "?")[:8]
            name = t.get("task_name", "?")
            st   = t.get("status", "?")

            # timeout_at を過ぎているのに running / waiting_ack
            if st in (TaskStatus.RUNNING, TaskStatus.WAITING_ACK, TaskStatus.QUEUED):
                timeout_at = t.get("timeout_at")
                if timeout_at:
                    try:
                        deadline = datetime.fromisoformat(timeout_at)
                        if deadline.tzinfo is None:
                            deadline = deadline.replace(tzinfo=timezone.utc)
                        if now_utc > deadline:
                            issues.append({
                                "level": "WARN",
                                "task_id": tid, "task_name": name,
                                "issue": f"timeout_at exceeded but status={st}",
                            })
                    except ValueError:
                        pass

            # blocked なのに depends_on が TASK_FLOW に存在しない
            if st == TaskStatus.BLOCKED:
                dep = t.get("depends_on")
                if dep and dep not in TASK_FLOW:
                    issues.append({
                        "level": "ERROR",
                        "task_id": tid, "task_name": name,
                        "issue": f"blocked by unknown dependency: {dep}",
                    })

        return issues

    def _pop_task(self, state: dict, task_id: str) -> dict | None:
        for i, t in enumerate(state["current_tasks"]):
            if t["task_id"] == task_id:
                return state["current_tasks"].pop(i)
        return {
            "task_id":   task_id,
            "task_name": "unknown",
            "owner":     get_sender(),
            "created_at": _now_iso(),
            "last_error": None,
            "error_type": None,
            "next_action": None,
            "depends_on_next": None,
        }


# グローバルインスタンス
state_mgr = StateManager()


# ============================================================
# ログ
# ============================================================
logger = logging.getLogger("slack_bridge")
logger.setLevel(logging.DEBUG)

_fh = RotatingFileHandler(
    str(LOG_FILE), encoding="utf-8",
    maxBytes=5 * 1024 * 1024,   # 5 MB でローテーション
    backupCount=3,               # bridge.log.1 / .2 / .3 を保持
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

_sh = logging.StreamHandler(sys.stdout)
_sh.setLevel(logging.INFO)
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
# CP932 環境 (Windows) でも emoji を含むログが落ちないよう stream を UTF-8 ラップ
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").upper() not in ("UTF-8", "UTF8"):
    import io as _io
    _sh.stream = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
logger.addHandler(_sh)


_EVENTS_MAX_LINES = 10_000   # これを超えたら古い行を半分削除

def log_event(event_type: str, data: dict):
    """events.jsonlにJSONLイベントを追記。10,000行超で古い半分を自動削除"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    # 定期的な行数チェック（100イベントに1回で十分）
    try:
        import random as _rnd
        if _rnd.randint(0, 99) == 0:
            _prune_events_file()
    except Exception:
        pass


def _prune_events_file():
    """events.jsonl が _EVENTS_MAX_LINES 超なら古い半分を削除（atomic write）"""
    if not EVENTS_FILE.exists():
        return
    lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) <= _EVENTS_MAX_LINES:
        return
    keep = lines[len(lines) // 2:]   # 新しい半分を残す
    tmp = EVENTS_FILE.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(keep), encoding="utf-8")
    tmp.replace(EVENTS_FILE)


# ============================================================
# Slackクライアント（429バックオフ付き）
# ============================================================
_client = None


def get_client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=SLACK_BOT_TOKEN)
    return _client


def slack_call_with_backoff(func, *args, max_backoff=60, **kwargs):
    """Slack API呼び出し。429時に指数バックオフ"""
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except SlackApiError as e:
            if e.response.status_code == 429:
                wait = min(2 ** attempt, max_backoff)
                retry_after = int(e.response.headers.get("Retry-After", wait))
                wait = max(wait, retry_after)
                logger.warning(f"Rate limited (429). Waiting {wait}s...")
                time.sleep(wait)
                attempt += 1
            else:
                raise


# ============================================================
# 送信者ID管理
# ============================================================
def get_sender() -> str:
    if SENDER_FILE.exists():
        return SENDER_FILE.read_text(encoding="utf-8").strip()
    return "unknown"


def set_sender(sender_id: str):
    SENDER_FILE.write_text(sender_id, encoding="utf-8")
    logger.info(f"送信者IDを '{sender_id}' に設定しました")


# ============================================================
# メッセージ構築・パース
# ============================================================
def build_bridge_message(display_text: str, msg_data: dict) -> str:
    """表示テキスト + BRIDGE_DELIMITER + JSON の2段構成メッセージを構築"""
    json_str = json.dumps(msg_data, ensure_ascii=False, indent=None)
    return f"{display_text}\n{BRIDGE_DELIMITER}\n{json_str}"


def parse_bridge_message(text: str) -> dict | None:
    """BRIDGE_DELIMITER以降をJSONパース。なければNone"""
    if BRIDGE_DELIMITER not in text:
        return None
    parts = text.split(BRIDGE_DELIMITER, 1)
    if len(parts) < 2:
        return None
    try:
        return json.loads(parts[1].strip())
    except json.JSONDecodeError:
        logger.warning(f"JSONパース失敗: {parts[1][:100]}")
        return None


def make_task_msg(from_id: str, to_id: str, task: str, payload: dict = None,
                  msg_type: str = "TASK", task_id: str = None,
                  correlation_id: str = None,
                  source: str = SOURCE_MANUAL,
                  workflow_id: str = None) -> dict:
    """標準メッセージ構造を生成。
    - source=auto: 依存チェーン自動起動
    - source=manual: 手動起動
    - workflow_id: 依存チェーン全体の追跡ID（省略時は新規生成）
    """
    return {
        "version": MSG_VERSION,
        "from": from_id,
        "to": to_id,
        "type": msg_type,
        "task": task,
        "task_id": task_id or str(uuid.uuid4()),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "workflow_id": workflow_id or str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "payload": payload or {},
    }


def make_response_msg(original: dict, msg_type: str, payload: dict = None) -> dict:
    """ACK/DONE/ERROR応答メッセージを生成"""
    return {
        "version": MSG_VERSION,
        "from": get_sender(),
        "to": original["from"],
        "type": msg_type,
        "task": original.get("task", ""),
        "task_id": original["task_id"],
        "correlation_id": original.get("correlation_id", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }


# ============================================================
# Slack送受信（旧互換維持）
# ============================================================
def send_message(text: str, sender: str = None, channel: str = None) -> bool:
    """Slackチャンネルにメッセージを送信（旧互換 + 新形式対応）"""
    client = get_client()
    channel = channel or AI_BRIDGE_CHANNEL
    sender = sender or get_sender()

    # BRIDGE_DELIMITERが含まれていなければ旧形式としてプレフィックス付与
    if BRIDGE_DELIMITER not in text:
        text = f"[{sender}] {text}"

    try:
        slack_call_with_backoff(
            client.chat_postMessage,
            channel=channel,
            text=text,
        )
        logger.info(f"送信完了: {text[:80]}")
        log_event("send", {"channel": channel, "text": text[:200]})
        return True
    except SlackApiError as e:
        logger.error(f"送信エラー: {e.response['error']}")
        return False


def send_bridge_msg(msg_data: dict, channel: str = None) -> bool:
    """構造化ブリッジメッセージを送信"""
    sender = msg_data.get("from", get_sender())
    msg_type = msg_data.get("type", "TASK")
    task = msg_data.get("task", "")
    display = f"[{sender}] [{msg_type}] {task}"
    if msg_type == "DONE":
        display = f"[{sender}] [{msg_type}] {task} 完了"
    elif msg_type == "ERROR":
        display = f"[{sender}] [{msg_type}] {task} エラー"
    elif msg_type == "ACK":
        display = f"[{sender}] [{msg_type}] {task} 受信確認"
    elif msg_type == "BLOCKED":
        wait_for = msg_data.get("payload", {}).get("wait_for", "")
        display = f"[{sender}] [BLOCKED] {task} 待機中 (requires: {wait_for})"
    elif msg_type == "ESCALATE":
        display = f"[{sender}] [ESCALATE] {task} エスカレーション"

    full_text = build_bridge_message(display, msg_data)
    return send_message(full_text, sender=sender, channel=channel)


def receive_messages(limit: int = 5, channel: str = None) -> list:
    """チャンネルの最新メッセージを取得（旧互換維持）"""
    client = get_client()
    channel = channel or AI_BRIDGE_CHANNEL
    try:
        result = slack_call_with_backoff(
            client.conversations_history,
            channel=channel,
            limit=limit,
        )
        messages = []
        for msg in result.get("messages", []):
            messages.append({
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
                "user": msg.get("user", "bot"),
            })
        return messages
    except SlackApiError as e:
        logger.error(f"受信エラー: {e.response['error']}")
        return []


# ============================================================
# タスクレジストリ（重複防止）
# ============================================================
def load_task_registry() -> dict:
    """task_id -> {status, timestamp, ...} の辞書をロード"""
    registry = {}
    if TASK_REGISTRY_FILE.exists():
        with open(TASK_REGISTRY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    registry[entry["task_id"]] = entry
                except (json.JSONDecodeError, KeyError):
                    continue
    return registry


def save_task_registry_entry(task_id: str, status: str, extra: dict = None):
    """タスクレジストリに1エントリ追記"""
    entry = {
        "task_id": task_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        entry.update(extra)
    with open(TASK_REGISTRY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log_event("registry_update", entry)


def is_task_processed(task_id: str) -> bool:
    """同一task_idが既に処理済みかチェック"""
    registry = load_task_registry()
    return task_id in registry


# ============================================================
# Pending Tasks管理（リトライ/エスカレーション）
# ============================================================
def load_pending_tasks() -> list:
    """pending_tasks.jsonlを全行ロード"""
    tasks = []
    if PENDING_TASKS_FILE.exists():
        with open(PENDING_TASKS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tasks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return tasks


def save_pending_tasks(tasks: list):
    """pending_tasks.jsonlを上書き保存"""
    with open(PENDING_TASKS_FILE, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def add_pending_task(msg_data: dict):
    """送信タスクをpendingに追加"""
    entry = {
        "task_id": msg_data["task_id"],
        "task": msg_data.get("task", ""),
        "to": msg_data.get("to", ""),
        "msg_data": msg_data,
        "status": "sent",
        "retry_count": 0,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "ack_at": None,
        "done_at": None,
    }
    tasks = load_pending_tasks()
    tasks.append(entry)
    save_pending_tasks(tasks)


def update_pending_task(task_id: str, updates: dict):
    """pending taskのステータスを更新"""
    tasks = load_pending_tasks()
    for t in tasks:
        if t["task_id"] == task_id:
            t.update(updates)
            break
    save_pending_tasks(tasks)


def remove_pending_task(task_id: str):
    """完了/エスカレート済みタスクをpendingから除去"""
    tasks = load_pending_tasks()
    tasks = [t for t in tasks if t["task_id"] != task_id]
    save_pending_tasks(tasks)


# ============================================================
# last_seen_ts管理
# ============================================================
def get_last_seen_ts() -> str:
    if LAST_SEEN_FILE.exists():
        return LAST_SEEN_FILE.read_text(encoding="utf-8").strip()
    return "0"


def save_last_seen_ts(ts: str):
    LAST_SEEN_FILE.write_text(ts, encoding="utf-8")


# ============================================================
# タスクハンドラ
# ============================================================
def handle_test_ping(msg_data: dict) -> dict:
    """疎通テスト。即時DONE返送"""
    return {"result": "pong", "handled_at": datetime.now(timezone.utc).isoformat()}


def handle_ebay_search(msg_data: dict) -> dict:
    """ebay_auction_search.pyをsubprocessで実行し、新規候補があればキャップにebay-review TASK送信"""
    script = Path(__file__).parent / "coin_business" / "scripts" / "ebay_auction_search.py"
    if not script.exists():
        return {"error": f"Script not found: {script}"}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=600, encoding="utf-8",
            cwd=str(script.parent.parent),
        )
        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-500:] if result.stderr else "",
                "returncode": result.returncode,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out after 600s"}
    except Exception as e:
        return {"error": str(e)}

    # 結果ファイル読み込み
    matches_file = Path(__file__).parent / "coin_business" / "data" / "ebay_matches_latest.json"
    if not matches_file.exists():
        return {"error": "Results file not generated", "stdout": result.stdout[-1000:] if result.stdout else ""}

    try:
        with open(matches_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read results: {e}"}

    matches = data.get("matches", [])
    new_matches = [m for m in matches if m.get("is_new", False)]

    # 新規候補がある場合、ebay-review TASK を _post_send_msg に入れて返す。
    # dispatch_task._run が DONE 送信後に送ることで、受信側の競合状態を防ぐ。
    # （ハンドラ内で直接送ると DONE より先に届き、依存チェック失敗を招く）
    #
    # 【Slack長文対策 正式仕様】
    # Slackには要約のみ送る（候補数・上位10件スリム）。
    # 詳細は candidates_YYYYMMDD_HHMMSS.json に保存し、ファイルパスをペイロードに含める。
    # Slack 4000文字制限: 21件フル送信は ~10,650 chars → 切り捨てでJSONパース失敗するため禁止。
    post_msg = None
    if new_matches:
        # 1) 全候補をファイル保存（詳細参照用）
        from datetime import datetime as _dt
        _ts_str = _dt.now().strftime("%Y%m%d_%H%M%S")
        candidates_file = DATA_DIR / f"ebay_candidates_{_ts_str}.json"
        _save_data = {
            "workflow_id": msg_data.get("workflow_id", ""),
            "searched_at": data.get("searched_at", ""),
            "total_count": len(new_matches),
            "candidates": new_matches,
        }
        with open(candidates_file, "w", encoding="utf-8") as _f:
            json.dump(_save_data, _f, ensure_ascii=False, indent=2)
        logger.info(f"候補ファイル保存: {candidates_file} ({len(new_matches)}件)")

        # 2) Slack送信はスリム要約のみ（上位10件・必須フィールドのみ）
        _KEEP = {"mgmt_no", "db_line1", "db_grader", "db_grade",
                 "ebay_limit_usd", "ebay_limit_jpy", "bid_count", "ebay_url", "is_new",
                 "judgment", "judgment_reason"}   # 判定結果を追加（切断点①修正）
        slim_matches = [{k: v for k, v in m.items() if k in _KEEP}
                        for m in new_matches[:10]]  # 上位10件 ≈ 3,258 chars < 4,000

        # 3) daily_candidates テーブルへ OK/REVIEW 案件を INSERT（切断点②修正）
        _ok_matches = [m for m in new_matches if m.get("judgment") in ("OK", "REVIEW")]
        if _ok_matches:
            try:
                from scripts.supabase_client import get_client as _get_supabase
                _conn = _get_supabase()
                _now_iso = _dt.now(timezone.utc).isoformat()
                _rows = [
                    {
                        "mgmt_no": m.get("mgmt_no", ""),
                        "ebay_url": m.get("ebay_url", ""),
                        "buy_limit_jpy": m.get("ebay_limit_jpy", 0),
                        "judgment": m.get("judgment", ""),
                        "judgment_reason": m.get("judgment_reason", ""),
                        "ebay_title": m.get("ebay_title", ""),
                        "api_price_usd": m.get("api_price_usd"),
                        "bid_count": m.get("bid_count", 0),
                        "created_at": _now_iso,
                        "updated_at": _now_iso,
                    }
                    for m in _ok_matches
                ]
                _conn.table("daily_candidates").upsert(
                    _rows, on_conflict="mgmt_no,ebay_url"
                ).execute()
                logger.info(f"daily_candidates INSERT: {len(_rows)}件 (OK/REVIEW)")
            except Exception as _e:
                logger.warning(f"daily_candidates INSERT失敗（処理は続行）: {_e}")

        post_msg = make_task_msg(
            from_id=get_sender(),
            to_id="cap",
            task="ebay-review",
            workflow_id=msg_data.get("workflow_id"),   # 親チェーンのID引き継ぎ
            payload={
                "candidates": slim_matches,            # 表示用スリム版（上位10件）
                "count": len(new_matches),             # 実際の全候補数
                "total_matches": len(matches),
                "searched_at": data.get("searched_at", ""),
                "candidates_file": str(candidates_file),  # 詳細ファイルパス（参照用）
                "workflow_id": msg_data.get("workflow_id", ""),
            },
        )
        logger.info(f"ebay-review TASK準備完了: 新規{len(new_matches)}件 (上位10件のみSlack送信, "
                    f"全件: {candidates_file.name}) - DONE後送信")

    return {
        "total_searched": data.get("total_searched", 0),
        "match_count": len(matches),
        "new_count": len(new_matches),
        "review_sent": len(new_matches) > 0,
        "_post_send_msg": post_msg,   # dispatch_task._run がDONE後に送信
    }


def handle_ebay_review(msg_data: dict) -> dict:
    """候補リストを受け取り、ファイルに保存 + コンソール表示

    ペイロード仕様（Slack長文対策 正式仕様）:
      candidates      : スリム候補リスト（上位10件・必須フィールドのみ）
      count           : 実際の全候補数（Slack上の表示件数と異なる場合あり）
      candidates_file : サイバーさん上の詳細ファイルパス（参照用）
      workflow_id     : 親チェーンのworkflow_id
    """
    payload = msg_data.get("payload", {})
    candidates = payload.get("candidates", [])
    searched_at = payload.get("searched_at", "")
    total_count = payload.get("count", len(candidates))   # 全候補数（Slack上は上位10件）
    candidates_file_ref = payload.get("candidates_file", "")  # cyberの詳細ファイルパス
    wf_id = payload.get("workflow_id") or msg_data.get("workflow_id", "")

    # ファイル保存（受信した分を保存）
    review_data = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "workflow_id": wf_id,
        "searched_at": searched_at,
        "displayed_count": len(candidates),
        "total_count": total_count,
        "candidates_file_on_cyber": candidates_file_ref,  # 詳細参照先
        "candidates": candidates,
    }
    outfile = DATA_DIR / "ebay_review_candidates.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    if candidates_file_ref:
        logger.info(f"詳細ファイル参照: {candidates_file_ref} (全{total_count}件)")

    # コンソールに候補一覧表示
    _display_count = len(candidates)
    _note = f"（全{total_count}件中 上位{_display_count}件表示）" if total_count > _display_count else f"（{_display_count}件）"
    print(f"\n{'='*60}")
    print(f"eBay仕入れ候補 {_note}")
    if candidates_file_ref:
        print(f"詳細ファイル: {candidates_file_ref}")
    print(f"{'='*60}")
    for i, c in enumerate(candidates, 1):
        _jdg = c.get('judgment', '')
        _jdg_label = {"OK": "✅OK", "REVIEW": "🔶REVIEW", "NG": "❌NG", "CEO判断": "👤CEO判断"}.get(_jdg, _jdg)
        print(f"\n{i}. #{c.get('mgmt_no','')} | {c.get('db_line1','')} {c.get('db_grader','')} {c.get('db_grade','')}  [{_jdg_label}]")
        print(f"   仕入上限: USD{c.get('ebay_limit_usd',0):,} ({c.get('ebay_limit_jpy',0):,}円)")
        print(f"   入札: {c.get('bid_count',0)}件")
        if c.get('judgment_reason'):
            print(f"   判定根拠: {c.get('judgment_reason','')}")
        print(f"   URL: {c.get('ebay_url','')}")
    print(f"\n{'='*60}")
    print(f"保存先: {outfile}")
    print(f"[!] ceo-report を起動するには cap の承認が必要です:")
    print(f"    python slack_bridge.py approve --task ebay-review --by cap")
    print(f"{'='*60}\n")

    return {
        "saved_to": str(outfile),
        "displayed_count": len(candidates),
        "total_count": total_count,
        "candidates_file_on_cyber": candidates_file_ref,
        "workflow_id": wf_id,
        "review_status": "pending",
        "note": "cap approval required before ceo-report auto-trigger",
    }


def handle_git_pull(msg_data: dict) -> dict:
    """git pullを実行"""
    repo_dir = Path(__file__).parent
    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, timeout=60,
            cwd=str(repo_dir), encoding="utf-8",
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"error": str(e)}


def handle_report(msg_data: dict) -> dict:
    """sourcing_report.txtの内容を返す"""
    report_file = Path(__file__).parent / "coin_business" / "data" / "sourcing_report.txt"
    if not report_file.exists():
        return {"error": "sourcing_report.txt not found"}
    content = report_file.read_text(encoding="utf-8")
    # 長すぎる場合は末尾を切り詰め
    if len(content) > 3000:
        content = content[:3000] + "\n... (truncated)"
    return {"report": content}


# ============================================================
# 安全ストア方式 (CTO Pattern B)
# ============================================================
SECURE_STORE_PATHS = [
    Path(__file__).parent / ".secure_env",          # 優先1: Git管理外
    Path(__file__).parent / ".env.local",            # 優先3: Git ignore
]
ALLOWED_SECURE_KEYS = {"EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_DEV_ID"}


def _load_secure_store() -> dict:
    """安全ストアから値を読み込み。優先順: .secure_env > OS環境変数 > .env.local"""
    store = {}
    # ファイルベース（低優先から読み込み、高優先で上書き）
    for path in reversed(SECURE_STORE_PATHS):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key in ALLOWED_SECURE_KEYS:
                    store[key] = val
    # OS環境変数（最高優先）
    for key in ALLOWED_SECURE_KEYS:
        env_val = os.environ.get(key)
        if env_val:
            store[key] = env_val
    return store


def handle_set_env(msg_data: dict) -> dict:
    """安全ストアからキーを取得し、coin_business/.envに反映"""
    payload = msg_data.get("payload", {})
    requested_keys = payload.get("keys", [])

    # 許可キーのみ
    rejected = [k for k in requested_keys if k not in ALLOWED_SECURE_KEYS]
    if rejected:
        return {"error": f"Rejected keys (not in allowlist): {rejected}"}

    # 安全ストアから値取得
    store = _load_secure_store()
    missing = [k for k in requested_keys if k not in store]
    if missing:
        return {"error": f"Keys not found in secure store: {missing}"}

    # coin_business/.env に反映
    env_file = Path(__file__).parent / "coin_business" / ".env"
    if not env_file.exists():
        return {"error": f".env not found: {env_file}"}

    env_content = env_file.read_text(encoding="utf-8")
    updated_keys = []

    for key in requested_keys:
        val = store[key]
        # 既存行を置換 or 追加
        import re as _re
        pattern = _re.compile(rf"^{_re.escape(key)}\s*=.*$", _re.MULTILINE)
        if pattern.search(env_content):
            env_content = pattern.sub(f"{key}={val}", env_content)
        else:
            env_content = env_content.rstrip() + f"\n{key}={val}\n"
        updated_keys.append(key)

    # atomic write
    tmp_file = env_file.with_suffix(".env.tmp")
    tmp_file.write_text(env_content, encoding="utf-8")
    tmp_file.replace(env_file)

    # ログには値を出さない（キー名のみ）
    logger.info(f"set-env完了: {updated_keys}")
    log_event("set_env", {"keys": updated_keys, "status": "updated"})

    return {"status": "updated", "keys": updated_keys}


def handle_ceo_report(msg_data: dict) -> dict:
    """ceo-report タスクハンドラ: CEO報告を#ceo-roomに送信"""
    task_id  = msg_data.get("task_id", "")
    payload  = msg_data.get("payload", {})
    file_path = payload.get("file_path")
    success = cmd_ceo_report(file_path=file_path, task_id=task_id)
    if success:
        return {"status": "sent", "channel": CEO_ROOM_CHANNEL}
    return {"status": "failed", "error": "CEO報告送信失敗"}


# ============================================================
# 楽天ROOM ステータスハンドラ
# ============================================================
def _parse_queue_stats(text: str) -> dict:
    """queue-status stdout から数値をパース"""
    import re
    def _int(pattern):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else 0
    return {
        "total":   _int(r"合計:\s+(\d+)"),
        "queued":  _int(r"待機:\s+(\d+)"),
        "running": _int(r"実行中:\s+(\d+)"),
        "posted":  _int(r"成功:\s+(\d+)"),
        "failed":  _int(r"失敗:\s+(\d+)"),
        "skipped": _int(r"スキップ:\s+(\d+)"),
    }


def _parse_health_stats(text: str) -> dict:
    """health stdout から主要指標をパース"""
    import re
    def _str(pattern, default="?"):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else default
    def _int(pattern, default=0):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else default
    def _float(pattern, default=0.0):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else default
    return {
        "status":           _str(r"総合ステータス:\s+(\S+)"),
        "pool_size":        _int(r"pool_size.*?(\d+)"),
        "consecutive_fails":_int(r"consecutive_fails.*?(\d+)"),
        "success_rate":     _float(r"success_rate.*?([\d.]+)"),
        "skip_rate":        _float(r"skip_rate.*?([\d.]+)"),
        "pool_depletion_days": _int(r"pool_depletion_days.*?(\d+)", default=-1),
    }


def _load_schedule(bot_dir: Path) -> tuple[list, list, bool, str]:
    """
    daily_plan.json から今日・翌日バッチを取得。
    Returns: (today_batches, tomorrow_batches, schedule_unknown, warning_msg)
    """
    from datetime import date as _date, timedelta
    today_str    = _date.today().isoformat()
    tomorrow_str = (_date.today() + timedelta(days=1)).isoformat()
    plan_file = bot_dir / "data" / "daily_plan.json"
    try:
        with open(plan_file, encoding="utf-8") as f:
            plan = json.load(f)
        plan_date = plan.get("date", "")
        batches_raw = plan.get("post", {}).get("batches", [])
        if not batches_raw:
            return [], [], True, "daily_plan.jsonにbatchesキーが存在しません"
        slim_batches = [
            {"id": b.get("id","?"), "start": b.get("start","?"),
             "count": b.get("count",0), "status": b.get("status","pending")}
            for b in batches_raw
        ]
        if plan_date == today_str:
            return slim_batches, [], False, ""
        elif plan_date == tomorrow_str:
            return [], slim_batches, False, ""
        else:
            # 日付が合わない → 参考情報として返す
            return slim_batches, [], False, f"daily_plan.jsonの日付({plan_date})が今日({today_str})と異なります"
    except FileNotFoundError:
        return [], [], True, "daily_plan.jsonが存在しません (23:50以降に生成予定)"
    except (json.JSONDecodeError, KeyError) as e:
        return [], [], True, f"daily_plan.json読み込み失敗: {e}"


def _month_end_check(pool_count: int, daily_avg: int = 95) -> dict:
    """月末チェック: 残日数とプール不足警告"""
    from datetime import date as _date, timedelta
    today = _date.today()
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    days_left = (next_month - timedelta(days=1) - today).days
    needed = days_left * daily_avg if days_left > 0 else 0
    short  = max(0, needed - pool_count)
    return {
        "flag":       days_left <= 5,
        "days_left":  days_left,
        "pool_count": pool_count,
        "needed":     needed,
        "short":      short,
    }


# ============================================================
# 定時スケジュール管理
# ============================================================
def _load_schedule_state() -> dict:
    """~/.slack_bridge/schedule_state.json を読む。不存在は {}"""
    try:
        if SCHEDULE_STATE_FILE.exists():
            with open(SCHEDULE_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_schedule_state(state: dict):
    """schedule_state.json に atomic write"""
    tmp = SCHEDULE_STATE_FILE.with_suffix(".tmp")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(SCHEDULE_STATE_FILE)
    except OSError as e:
        logger.warning(f"schedule_state保存失敗: {e}")


def _check_and_fire_schedules():
    """
    SCHEDULE_SLOTS の各時刻を確認し、未発火スロットがあれば daily-check を送信。
    重複発火防止: last_fired_date == today かつ status in (running, done) ならスキップ。
    """
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = _load_schedule_state()

    for slot in SCHEDULE_SLOTS:
        hour, minute = map(int, slot.split(":"))
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot_time:
            continue  # まだ時刻に達していない

        slot_entry = state.get(slot, {})
        last_fired = slot_entry.get("last_fired_date", "")
        slot_status = slot_entry.get("status", "")

        if last_fired == today and slot_status in ("running", "done"):
            continue  # 重複発火防止

        # 発火
        wf_id = str(uuid.uuid4())
        my_sender = get_sender()
        task_msg = make_task_msg(
            from_id=my_sender, to_id="cyber",
            task="daily-check",
            payload={"slot": slot, "date": today},
            workflow_id=wf_id,
            source=SOURCE_AUTO,
        )
        success = send_bridge_msg(task_msg)
        if success:
            add_pending_task(task_msg)
            task_id = task_msg["task_id"]
            state_mgr.task_queued(task_id, "daily-check", my_sender, "cyber",
                                  workflow_id=wf_id)
            state[slot] = {
                "last_fired_date": today,
                "status":          "running",
                "workflow_id":     wf_id,
                "task_id":         task_id,
                "fired_at":        now.isoformat(),
            }
            _save_schedule_state(state)
            logger.info(f"[scheduler] {slot} fired: daily-check wf={wf_id[:8]}")
            log_event("schedule_fired", {"slot": slot, "workflow_id": wf_id})
        else:
            logger.warning(f"[scheduler] {slot} 発火失敗 (Slack送信エラー)")


# ============================================================
# コインリサーチ ステータス収集
# ============================================================
def _collect_coin_status(coin_dir: Path) -> tuple[dict, list]:
    """
    coin_business/run.py count + stats --clean を実行し、slim dictを返す。
    Returns: (coin_data, errors_list)
    """
    import re
    errors = []
    coin_data: dict = {"status": "OK", "total_records": 0,
                       "recent_3m_count": None, "avg_price_jpy": None}

    # --- count ---
    r_count = subprocess.run(
        ["python", "run.py", "count"],
        cwd=str(coin_dir), capture_output=True, text=True,
        timeout=30, check=False, encoding="utf-8", errors="replace",
    )
    if r_count.returncode != 0:
        errors.append({"step": "coin-count", "stderr": r_count.stderr[:300]})
        coin_data["status"] = "WARNING"
    else:
        m = re.search(r"合計\s+([\d,]+)件", r_count.stdout)
        if m:
            coin_data["total_records"] = int(m.group(1).replace(",", ""))

    # --- stats --clean --time ---
    r_stats = subprocess.run(
        ["python", "run.py", "stats", "--clean", "--time"],
        cwd=str(coin_dir), capture_output=True, text=True,
        timeout=60, check=False, encoding="utf-8", errors="replace",
    )
    if r_stats.returncode != 0:
        errors.append({"step": "coin-stats", "stderr": r_stats.stderr[:300]})
        coin_data["status"] = "WARNING"
    else:
        # 直近3か月行: ">>>  直近3か月     5,000件    150,000円    100,000円"
        m3 = re.search(
            r">>>\s*直近3か月\s+([\d,]+)件\s+([\d,]+)円\s+([\d,]+)円",
            r_stats.stdout
        )
        if m3:
            coin_data["recent_3m_count"] = int(m3.group(1).replace(",", ""))
            coin_data["avg_price_jpy"]   = int(m3.group(2).replace(",", ""))
            coin_data["med_price_jpy"]   = int(m3.group(3).replace(",", ""))

    if not errors:
        coin_data["status"] = "OK"
    return coin_data, errors


def handle_rakuten_status(msg_data: dict) -> dict:
    """
    楽天ROOM botの全体ステータスを収集してcapに報告。
    cyber側で実行 → DONE後にrakuten-reportをcapへ自動送信。
    """
    bot_dir = Path(__file__).parent / "rakuten-room" / "bot"
    wf_id   = msg_data.get("workflow_id", "")
    errors  = []

    # --- 1. queue-status ---
    q_result = subprocess.run(
        ["python", "run.py", "queue-status"],
        cwd=str(bot_dir), capture_output=True, text=True,
        timeout=30, check=False, encoding="utf-8", errors="replace",
    )
    queue_stats = _parse_queue_stats(q_result.stdout) if q_result.returncode == 0 else {}
    if q_result.returncode != 0:
        errors.append(f"queue-status failed(rc={q_result.returncode}): {q_result.stderr[:200]}")

    # --- 2. health ---
    h_result = subprocess.run(
        ["python", "run.py", "health"],
        cwd=str(bot_dir), capture_output=True, text=True,
        timeout=30, check=False, encoding="utf-8", errors="replace",
    )
    health_stats = _parse_health_stats(h_result.stdout) if h_result.returncode == 0 else {"status": "UNKNOWN"}
    if h_result.returncode != 0:
        errors.append(f"health failed(rc={h_result.returncode}): {h_result.stderr[:200]}")

    # --- 3. schedule (daily_plan.json) ---
    today_batches, tomorrow_batches, schedule_unknown, schedule_warn = _load_schedule(bot_dir)
    if schedule_unknown or schedule_warn:
        errors.append(f"schedule_warning: {schedule_warn}")

    # --- 4. 月末チェック ---
    pool_count   = health_stats.get("pool_size", 0)
    month_end    = _month_end_check(pool_count)

    # --- 5. 全データを latest.json に保存 ---
    status_file = DATA_DIR / "rakuten_status_latest.json"
    full_data = {
        "collected_at":   datetime.now(timezone.utc).isoformat(),
        "workflow_id":    wf_id,
        "queue":          {"stats": queue_stats, "stdout": q_result.stdout, "stderr": q_result.stderr},
        "health":         {"stats": health_stats, "stdout": h_result.stdout, "stderr": h_result.stderr},
        "schedule":       {"today": today_batches, "tomorrow": tomorrow_batches,
                           "unknown": schedule_unknown, "warning": schedule_warn},
        "month_end":      month_end,
        "errors":         errors,
    }
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(full_data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        errors.append(f"latest.json保存失敗: {e}")

    # --- 6. slim payload 構築 (< 2500 chars 目標) ---
    slim = {
        "date":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "queue":     queue_stats,
        "health":    health_stats,
        "schedule":  {"today": today_batches, "tomorrow": tomorrow_batches,
                      "unknown": schedule_unknown},
        "month_end": month_end,
        "file_path": str(status_file),
        "workflow_id": wf_id,
        "errors":    errors[:3],   # 最大3件
    }

    # schedule_unknown を state に warning として記録
    if schedule_unknown:
        try:
            _s = state_mgr.load()
            _s["next_action"] = f"[WARNING] rakuten-status: {schedule_warn}"
            state_mgr.save(_s)
        except Exception:
            pass

    # --- 7. rakuten-report を _post_send_msg で DONE後に自動送信 ---
    post_msg = make_task_msg(
        from_id=get_sender(), to_id="cap",
        task="rakuten-report",
        workflow_id=wf_id,
        payload=slim,
    )

    return {
        "collected_at": full_data["collected_at"],
        "file_path":    str(status_file),
        "queue":        queue_stats,
        "health_status": health_stats.get("status", "UNKNOWN"),
        "schedule_unknown": schedule_unknown,
        "month_end_flag": month_end["flag"],
        "errors":       errors,
        "_post_send_msg": post_msg,
    }


def _format_rakuten_report(payload: dict) -> str:
    """slim payload を CEO向けSlackメッセージに整形"""
    from datetime import date as _date
    lines = []
    date_str = payload.get("date", _date.today().isoformat())

    # --- 月末警告（先頭） ---
    me = payload.get("month_end", {})
    if me.get("flag"):
        days = me.get("days_left", 0)
        pool = me.get("pool_count", 0)
        needed = me.get("needed", 0)
        short  = me.get("short", 0)
        lines.append(f"⚠️ *月末チェック* [月末まで{days}日]")
        lines.append(f"  プール残高: {pool}件 / 必要推定: {needed}件")
        if short > 0:
            lines.append(f"  → ⚡ {short}件不足！`python run.py replenish` を実行してください")
        else:
            lines.append(f"  → ✅ プール残量は十分です")
        lines.append("")

    # --- ヘッダー ---
    lines.append(f"🏠 *楽天ROOM ステータス* ({date_str})")
    lines.append("")

    # --- キュー状況 ---
    q = payload.get("queue", {})
    total   = q.get("total", "?")
    posted  = q.get("posted", "?")
    failed  = q.get("failed", "?")
    queued  = q.get("queued", "?")
    skipped = q.get("skipped", "?")
    lines.append(f"📊 *本日キュー*")
    lines.append(f"  合計: {total}件 | 成功: {posted} | 失敗: {failed} | 待機: {queued} | スキップ: {skipped}")
    lines.append("")

    # --- ヘルス ---
    h = payload.get("health", {})
    hs = h.get("status", "UNKNOWN")
    icon = {"OK": "✅", "WARNING": "⚠️", "CRITICAL": "🚨"}.get(hs, "❓")
    pool_sz  = h.get("pool_size", "?")
    con_fail = h.get("consecutive_fails", "?")
    suc_rate = h.get("success_rate", 0)
    suc_pct  = f"{suc_rate * 100:.0f}%" if isinstance(suc_rate, float) else "?"
    lines.append(f"💪 *ヘルス*: {icon} {hs}")
    lines.append(f"  プール: {pool_sz}件 | 連続失敗: {con_fail} | 成功率: {suc_pct}")
    lines.append("")

    # --- スケジュール ---
    sched = payload.get("schedule", {})
    if sched.get("unknown"):
        lines.append(f"📅 *スケジュール*: ⚠️ 取得できませんでした (daily_plan.json未生成か破損)")
    else:
        today_b    = sched.get("today", [])
        tomorrow_b = sched.get("tomorrow", [])
        if today_b:
            lines.append(f"📅 *本日スケジュール*")
            for b in today_b:
                st_icon = {"completed": "✅", "running": "🔄", "failed": "❌"}.get(b.get("status",""), "⏳")
                lines.append(f"  {b.get('id','batch'):8s}: {b.get('start','?')} 開始 — {b.get('count','?')}件 {st_icon}")
        if tomorrow_b:
            lines.append(f"📅 *明日スケジュール*")
            for b in tomorrow_b:
                lines.append(f"  {b.get('id','batch'):8s}: {b.get('start','?')} 開始 — {b.get('count','?')}件 (予定)")
    lines.append("")

    # --- エラー ---
    errs = payload.get("errors", [])
    if errs:
        lines.append(f"⚠️ *収集エラー*")
        for e in errs[:3]:
            lines.append(f"  - {e[:120]}")
        lines.append("")

    # --- フッター ---
    fp = payload.get("file_path", "")
    wf = payload.get("workflow_id", "")[:8]
    lines.append(f"_(詳細: `rakuten_status_latest.json` | wf={wf})_")

    return "\n".join(lines)


def handle_rakuten_report(msg_data: dict) -> dict:
    """
    capで実行: rakuten-statusの結果を#ceo-roomに整形投稿。
    """
    task_id = msg_data.get("task_id", "")
    payload = msg_data.get("payload", {})

    text = _format_rakuten_report(payload)

    # 2500文字上限チェック
    if len(text) > 2500:
        text = text[:2450] + "\n...(省略)"

    success = send_message(text, channel=CEO_ROOM_CHANNEL)
    status  = "sent" if success else "failed"

    # state に report_status を記録
    if task_id:
        state_mgr.task_report_sent(task_id, CEO_ROOM_CHANNEL, status=status)

    return {"status": status, "channel": CEO_ROOM_CHANNEL, "report_status": status}


# ============================================================
# 全事業 日次チェック ハンドラ
# ============================================================
def handle_daily_check(msg_data: dict) -> dict:
    """
    cyberで実行: 全事業のステータスを収集してcapにdaily-reportを送信。
    07:30 / 12:30 / 18:30 の定時スロットから自動発火。
    """
    import time as _time
    start_time = _time.time()
    bot_dir  = Path(__file__).parent / "rakuten-room" / "bot"
    coin_dir = Path(__file__).parent / "coin_business"
    wf_id    = msg_data.get("workflow_id", "")
    payload_in = msg_data.get("payload", {})
    slot     = payload_in.get("slot", "??:??")

    # errors を事業単位で分離 (CTO要件)
    errors: dict = {"rakuten": [], "coin": [], "web": []}

    # -------------------------------------------------------
    # 1. 楽天ROOM (既存ヘルパー再利用)
    # -------------------------------------------------------
    q_result = subprocess.run(
        ["python", "run.py", "queue-status"],
        cwd=str(bot_dir), capture_output=True, text=True,
        timeout=30, check=False, encoding="utf-8", errors="replace",
    )
    queue_stats = _parse_queue_stats(q_result.stdout) if q_result.returncode == 0 else {}
    if q_result.returncode != 0:
        errors["rakuten"].append({"step": "queue-status",
                                  "stderr": q_result.stderr[:200]})

    h_result = subprocess.run(
        ["python", "run.py", "health"],
        cwd=str(bot_dir), capture_output=True, text=True,
        timeout=30, check=False, encoding="utf-8", errors="replace",
    )
    health_stats = (_parse_health_stats(h_result.stdout)
                    if h_result.returncode == 0 else {"status": "UNKNOWN"})
    if h_result.returncode != 0:
        errors["rakuten"].append({"step": "health",
                                  "stderr": h_result.stderr[:200]})

    today_batches, tomorrow_batches, schedule_unknown, schedule_warn = _load_schedule(bot_dir)
    if schedule_unknown or schedule_warn:
        errors["rakuten"].append({"step": "schedule", "warn": schedule_warn})

    pool_count = health_stats.get("pool_size", 0)
    month_end  = _month_end_check(pool_count)

    if schedule_unknown:
        try:
            _s = state_mgr.load()
            _s["next_action"] = f"[WARNING] daily-check: {schedule_warn}"
            state_mgr.save(_s)
        except Exception:
            pass

    rakuten_data = {
        "queue":     queue_stats,
        "health":    health_stats,
        "schedule":  {"today": today_batches, "tomorrow": tomorrow_batches,
                      "unknown": schedule_unknown},
        "month_end": month_end,
    }

    # -------------------------------------------------------
    # 2. コインリサーチ
    # -------------------------------------------------------
    coin_data, coin_errors = _collect_coin_status(coin_dir)
    errors["coin"].extend(coin_errors)

    # -------------------------------------------------------
    # 3. WEB事業 (placeholder)
    # -------------------------------------------------------
    web_data = {"status": "not_implemented"}

    # -------------------------------------------------------
    # 4. 実行時間計測 (CTO要件)
    # -------------------------------------------------------
    elapsed_sec  = round(_time.time() - start_time, 1)
    timeout_flag = elapsed_sec > 25   # 30s timeout の 5s 前

    # -------------------------------------------------------
    # 5. 全データを daily_check_latest.json に保存
    # -------------------------------------------------------
    status_file = DATA_DIR / "daily_check_latest.json"
    collected_at = datetime.now(timezone.utc).isoformat()
    full_data = {
        "collected_at": collected_at,
        "workflow_id":  wf_id,
        "slot":         slot,
        "rakuten":      {**rakuten_data,
                         "queue_stdout":   q_result.stdout,
                         "health_stdout":  h_result.stdout},
        "coin":         coin_data,
        "web":          web_data,
        "errors":       errors,
        "elapsed_sec":  elapsed_sec,
        "timeout_flag": timeout_flag,
    }
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(full_data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        errors["rakuten"].append({"step": "save_file", "stderr": str(e)})

    # -------------------------------------------------------
    # 6. slim payload 構築 (< 2500 chars)
    # -------------------------------------------------------
    slim = {
        "slot":        slot,
        "date":        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rakuten":     rakuten_data,
        "coin":        coin_data,
        "web":         web_data,
        "errors":      errors,
        "elapsed_sec": elapsed_sec,
        "timeout_flag": timeout_flag,
        "file_path":   str(status_file),
        "workflow_id": wf_id,
        "collected_at": collected_at,
    }

    # -------------------------------------------------------
    # 7. daily-report を _post_send_msg で DONE後に自動送信
    # -------------------------------------------------------
    post_msg = make_task_msg(
        from_id=get_sender(), to_id="cap",
        task="daily-report",
        workflow_id=wf_id,
        payload=slim,
    )

    return {
        "collected_at":   collected_at,
        "file_path":      str(status_file),
        "slot":           slot,
        "elapsed_sec":    elapsed_sec,
        "timeout_flag":   timeout_flag,
        "health_status":  health_stats.get("status", "UNKNOWN"),
        "coin_status":    coin_data.get("status", "UNKNOWN"),
        "errors":         errors,
        "_post_send_msg": post_msg,
    }


def _format_daily_report(payload: dict) -> str:
    """slim payload を全事業 CEO向けSlackメッセージに整形"""
    from datetime import date as _date
    lines = []
    date_str = payload.get("date", _date.today().isoformat())
    slot     = payload.get("slot", "??:??")

    # --- 月末警告（先頭） ---
    rakuten = payload.get("rakuten", {})
    me = rakuten.get("month_end", {})
    if me.get("flag"):
        days   = me.get("days_left", 0)
        pool   = me.get("pool_count", 0)
        needed = me.get("needed", 0)
        short  = me.get("short", 0)
        lines.append(f"⚠️ *月末チェック* [月末まで{days}日]")
        lines.append(f"  プール残高: {pool}件 / 必要推定: {needed}件")
        if short > 0:
            lines.append(f"  → ⚡ {short}件不足！`python run.py replenish`")
        else:
            lines.append(f"  → ✅ プール残量は十分です")
        lines.append("")

    # --- ヘッダー ---
    lines.append(f"📊 *全事業 定時レポート* ({slot} / {date_str})")
    lines.append("")

    # --- 楽天ROOM ---
    lines.append("🏠 *楽天ROOM*")
    q = rakuten.get("queue", {})
    h = rakuten.get("health", {})
    if q:
        total  = q.get("total", "?")
        posted = q.get("posted", "?")
        failed = q.get("failed", "?")
        queued = q.get("queued", "?")
        lines.append(f"  キュー: 合計{total}件 | 成功{posted} | 失敗{failed} | 待機{queued}")
    if h:
        hs      = h.get("status", "UNKNOWN")
        icon    = {"OK": "✅", "WARNING": "⚠️", "CRITICAL": "🚨"}.get(hs, "❓")
        pool_sz = h.get("pool_size", "?")
        suc_r   = h.get("success_rate", 0)
        suc_pct = f"{suc_r * 100:.0f}%" if isinstance(suc_r, float) else "?"
        lines.append(f"  ヘルス: {icon} {hs} | プール{pool_sz}件 | 成功率{suc_pct}")
    sched = rakuten.get("schedule", {})
    if sched.get("unknown"):
        lines.append("  スケジュール: ⚠️ 未生成か破損")
    else:
        today_b = sched.get("today", [])
        if today_b:
            parts = []
            for b in today_b:
                st_icon = {"completed": "✅", "running": "🔄", "failed": "❌"}.get(
                    b.get("status", ""), "⏳")
                parts.append(f"{b.get('id','?')} {b.get('start','?')}({b.get('count','?')}件{st_icon})")
            lines.append(f"  スケジュール: {' / '.join(parts)}")
    lines.append("")

    # --- コインリサーチ ---
    coin = payload.get("coin", {})
    lines.append("🪙 *コインリサーチ*")
    total_r = coin.get("total_records", 0)
    lines.append(f"  DBレコード: {total_r:,}件")
    r3 = coin.get("recent_3m_count")
    if r3 is not None:
        avg_p = coin.get("avg_price_jpy")
        med_p = coin.get("med_price_jpy")
        avg_s = f"平均¥{avg_p:,}" if avg_p else ""
        med_s = f"中央¥{med_p:,}" if med_p else ""
        detail = " | ".join(filter(None, [f"直近3か月: {r3:,}件", avg_s, med_s]))
        lines.append(f"  {detail}")
    c_status = coin.get("status", "UNKNOWN")
    if c_status != "OK":
        lines.append(f"  ⚠️ coin status: {c_status}")
    lines.append("")

    # --- WEB事業 ---
    web = payload.get("web", {})
    lines.append("🌐 *WEB事業*")
    w_status = web.get("status", "?")
    if w_status == "not_implemented":
        lines.append("  (未実装)")
    else:
        lines.append(f"  ステータス: {w_status}")
    lines.append("")

    # --- エラー ---
    errors = payload.get("errors", {})
    all_errs = []
    for biz, errs in (errors.items() if isinstance(errors, dict) else []):
        for e in errs:
            step = e.get("step", "?")
            msg  = e.get("stderr", e.get("warn", ""))[:80]
            all_errs.append(f"[{biz}/{step}] {msg}")
    if all_errs:
        lines.append("⚠️ *収集エラー*")
        for e in all_errs[:5]:
            lines.append(f"  - {e}")
        lines.append("")
    else:
        lines.append("❌ エラー: なし")
        lines.append("")

    # --- timeout警告 ---
    if payload.get("timeout_flag"):
        elapsed = payload.get("elapsed_sec", 0)
        lines.append(f"⚠️ *タイムアウト警告*: 収集時間 {elapsed}秒 (基準25秒超)")
        lines.append("")

    # --- フッター ---
    wf = payload.get("workflow_id", "")[:8]
    lines.append(f"_(詳細: `daily_check_latest.json` | wf={wf})_")

    return "\n".join(lines)


def handle_daily_report(msg_data: dict) -> dict:
    """
    capで実行: daily-checkの結果を#ceo-roomに整形投稿。
    """
    task_id    = msg_data.get("task_id", "")
    payload    = msg_data.get("payload", {})
    wf_id      = msg_data.get("workflow_id", "")
    slot       = payload.get("slot", "??:??")

    text = _format_daily_report(payload)

    # 2500文字上限チェック
    if len(text) > 2500:
        text = text[:2450] + "\n...(省略)"

    success = send_message(text, channel=CEO_ROOM_CHANNEL)
    status  = "sent" if success else "failed"

    # state に report_status を記録
    if task_id:
        state_mgr.task_report_sent(task_id, CEO_ROOM_CHANNEL, status=status)

    # schedule_state の status を "done" に更新
    try:
        sched_state = _load_schedule_state()
        today_str = datetime.now().strftime("%Y-%m-%d")
        # wf_id で対応するスロットを検索して更新
        for s_slot, s_entry in sched_state.items():
            if (s_entry.get("workflow_id") == wf_id
                    and s_entry.get("last_fired_date") == today_str):
                s_entry["status"] = "done"
                break
        _save_schedule_state(sched_state)
    except Exception as e:
        logger.warning(f"schedule_state done更新失敗: {e}")

    return {"status": status, "channel": CEO_ROOM_CHANNEL,
            "report_status": status, "slot": slot}


# ============================================================
# コイン事業 ステータスハンドラ
# ============================================================
def handle_coin_status(msg_data: dict) -> dict:
    """
    cyberで実行: コイン事業のステータスを収集してcapにcoin-reportを送信。
    """
    import time as _time
    start_time = _time.time()
    coin_dir = Path(__file__).parent / "coin_business"
    wf_id    = msg_data.get("workflow_id", "")
    errors   = []

    # --- 1. DB件数 + 3か月stats (既存ヘルパー再利用) ---
    coin_data, coin_errors = _collect_coin_status(coin_dir)
    errors.extend(coin_errors)

    # --- 2. eBay候補ファイル読み込み ---
    candidates_file = DATA_DIR / "ebay_review_candidates.json"
    ebay_candidates = {"count": 0, "received_at": None}
    try:
        if candidates_file.exists():
            with open(candidates_file, encoding="utf-8") as f:
                cdata = json.load(f)
            if isinstance(cdata, dict):
                # count が None / 不正値の場合は candidates リストで代替
                raw_count = cdata.get("count", None)
                safe_count = (int(raw_count) if isinstance(raw_count, (int, float))
                              and raw_count >= 0 else len(cdata.get("candidates", [])))
                ebay_candidates = {
                    "count":       safe_count,
                    "received_at": cdata.get("received_at"),
                }
            elif isinstance(cdata, list):
                ebay_candidates = {"count": len(cdata), "received_at": None}
            # count が負数 / 非整数になっていないか最終確認
            ebay_candidates["count"] = max(0, int(ebay_candidates.get("count") or 0))
    except (OSError, json.JSONDecodeError) as e:
        errors.append({"step": "ebay-candidates", "stderr": str(e)[:200]})

    # --- 3. 最終ebay-search日時 (state から取得) ---
    last_ebay_search = None
    try:
        _s = state_mgr.load()
        for t in _s.get("recent_history", []):
            if t.get("task_name") == "ebay-search" and t.get("status") == "done":
                last_ebay_search = t.get("updated_at")
                break
    except Exception:
        pass

    elapsed_sec  = round(_time.time() - start_time, 1)
    timeout_flag = elapsed_sec > 25

    # --- 4. 全データを coin_status_latest.json に保存 ---
    status_file  = DATA_DIR / "coin_status_latest.json"
    collected_at = datetime.now(timezone.utc).isoformat()
    full_data = {
        "collected_at":    collected_at,
        "workflow_id":     wf_id,
        "coin":            coin_data,
        "ebay_candidates": ebay_candidates,
        "last_ebay_search": last_ebay_search,
        "errors":          errors,
        "elapsed_sec":     elapsed_sec,
        "timeout_flag":    timeout_flag,
    }
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(full_data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        errors.append({"step": "save_file", "stderr": str(e)})

    # --- 5. slim payload 構築 ---
    slim = {
        "date":             datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "coin":             coin_data,
        "ebay_candidates":  ebay_candidates,
        "last_ebay_search": last_ebay_search,
        "errors":           errors[:3],
        "file_path":        str(status_file),
        "workflow_id":      wf_id,
        "collected_at":     collected_at,
    }

    # --- 6. coin-report を _post_send_msg で DONE後に自動送信 ---
    post_msg = make_task_msg(
        from_id=get_sender(), to_id="cap",
        task="coin-report",
        workflow_id=wf_id,
        payload=slim,
    )

    return {
        "collected_at":    collected_at,
        "file_path":       str(status_file),
        "coin_status":     coin_data.get("status", "UNKNOWN"),
        "ebay_candidates": ebay_candidates["count"],
        "errors":          errors,
        "elapsed_sec":     elapsed_sec,
        "_post_send_msg":  post_msg,
    }


def _format_coin_report(payload: dict) -> str:
    """slim payload をコイン事業CEO向けSlackメッセージに整形"""
    from datetime import date as _date
    lines = []
    date_str = payload.get("date", _date.today().isoformat())

    # --- ヘッダー ---
    lines.append(f"🪙 *コインリサーチ ステータス* ({date_str})")
    lines.append("")

    # --- DBレコード ---
    coin = payload.get("coin", {})
    total = coin.get("total_records", 0)
    lines.append(f"📊 *DBレコード*: {total:,}件")
    lines.append("")

    # --- 3か月stats ---
    r3 = coin.get("recent_3m_count")
    if r3 is not None:
        avg_p = coin.get("avg_price_jpy")
        med_p = coin.get("med_price_jpy")
        parts = [f"件数: {r3:,}件"]
        if avg_p:
            parts.append(f"平均: ¥{avg_p:,}")
        if med_p:
            parts.append(f"中央: ¥{med_p:,}")
        lines.append(f"📈 *直近3か月* (仕入判断データ)")
        lines.append(f"  {' | '.join(parts)}")
        lines.append("")

    c_status = coin.get("status", "UNKNOWN")
    if c_status != "OK":
        icon = "⚠️" if c_status == "WARNING" else "🚨"
        lines.append(f"{icon} coin status: {c_status}")
        lines.append("")

    # --- eBay候補 ---
    ec = payload.get("ebay_candidates", {})
    ec_count = ec.get("count", 0)
    if ec_count > 0:
        ec_recv = ec.get("received_at", "")
        ec_date = ec_recv[:10] if ec_recv else "?"
        lines.append(f"🔍 *eBay候補* (未レビュー): {ec_count}件  ({ec_date})")
        lines.append(f"  → `python slack_bridge.py send-task --task ebay-review --to cap`")
    else:
        lines.append(f"🔍 *eBay候補*: なし  (`python slack_bridge.py send-task --task ebay-search --to cyber` を実行)")
    lines.append("")

    # --- 最終eBay検索 ---
    les = payload.get("last_ebay_search")
    if les:
        try:
            _dt = datetime.fromisoformat(les).astimezone(JST)
            les_str = _dt.strftime("%Y-%m-%d %H:%M JST")
        except Exception:
            les_str = les[:19]
        lines.append(f"📅 *最終eBay検索*: {les_str}")
        lines.append("")

    # --- エラー ---
    errs = payload.get("errors", [])
    if errs:
        lines.append(f"⚠️ *収集エラー*")
        for e in errs[:3]:
            if isinstance(e, dict):
                lines.append(f"  - [{e.get('step','?')}] {e.get('stderr','')[:100]}")
            else:
                lines.append(f"  - {str(e)[:100]}")
        lines.append("")

    # --- フッター ---
    wf = payload.get("workflow_id", "")[:8]
    lines.append(f"_(詳細: `coin_status_latest.json` | wf={wf})_")

    return "\n".join(lines)


def handle_coin_report(msg_data: dict) -> dict:
    """
    capで実行: coin-statusの結果を#ceo-roomに整形投稿。
    """
    task_id = msg_data.get("task_id", "")
    payload = msg_data.get("payload", {})

    text = _format_coin_report(payload)

    # 2500文字上限チェック
    if len(text) > 2500:
        text = text[:2450] + "\n...(省略)"

    success = send_message(text, channel=CEO_ROOM_CHANNEL)
    status  = "sent" if success else "failed"

    if task_id:
        state_mgr.task_report_sent(task_id, CEO_ROOM_CHANNEL, status=status)

    return {"status": status, "channel": CEO_ROOM_CHANNEL, "report_status": status}


# ============================================================
# 日次申し送り (daily-handoff) — cap local コマンド
# ============================================================
HANDOFF_FILE = DATA_DIR / "daily_handoff.json"


def _generate_daily_handoff() -> dict:
    """
    既存データファイルから構造化申し送りドキュメントを生成。
    サイバーへの送信なし。すべてcap-local。
    """
    from datetime import date as _date

    now_jst  = datetime.now(JST)
    today    = now_jst.strftime("%Y-%m-%d")
    now_str  = now_jst.strftime("%Y-%m-%d %H:%M JST")

    # --- データ収集 ---
    state_data       = state_mgr.load()
    sched_state      = _load_schedule_state()
    daily_check_data = _safe_load_json(DATA_DIR / "daily_check_latest.json")
    rakuten_data     = _safe_load_json(DATA_DIR / "rakuten_status_latest.json")
    coin_data        = _safe_load_json(DATA_DIR / "coin_status_latest.json")
    candidates_data  = _safe_load_json(DATA_DIR / "ebay_review_candidates.json")

    # --- 楽天ROOM状態 ---
    rakuten_health = "不明"
    rakuten_pool   = 0
    if rakuten_data:
        h = rakuten_data.get("health", {}).get("stats", {})
        if not h:
            h = rakuten_data.get("health", {})
        rakuten_health = h.get("status", "不明")
        rakuten_pool   = h.get("pool_size", 0)
    elif daily_check_data:
        h = daily_check_data.get("rakuten", {}).get("health", {})
        rakuten_health = h.get("status", "不明")
        rakuten_pool   = h.get("pool_size", 0)

    # --- コイン状態 ---
    coin_total  = 0
    coin_recent = None
    if coin_data:
        c = coin_data.get("coin", {})
        coin_total  = c.get("total_records", 0)
        coin_recent = c.get("recent_3m_count")
    elif daily_check_data:
        c = daily_check_data.get("coin", {})
        coin_total  = c.get("total_records", 0)
        coin_recent = c.get("recent_3m_count")

    # --- eBay候補 (None/異常値ガード) ---
    ebay_count = 0
    try:
        if isinstance(candidates_data, dict):
            raw = candidates_data.get("count", None)
            ebay_count = (int(raw) if isinstance(raw, (int, float)) and raw >= 0
                          else len(candidates_data.get("candidates", [])))
        elif isinstance(candidates_data, list):
            ebay_count = len(candidates_data)
        ebay_count = max(0, int(ebay_count or 0))
    except Exception:
        ebay_count = 0

    # --- 最終ebay-search ---
    last_ebay_search = None
    for t in state_data.get("recent_history", []):
        if t.get("task_name") == "ebay-search" and t.get("status") == "done":
            last_ebay_search = t.get("updated_at", "")[:16].replace("T", " ")
            break

    # --- 定時スロット状態 ---
    slot_status = {}
    for slot in SCHEDULE_SLOTS:
        entry = sched_state.get(slot, {})
        if entry.get("last_fired_date") == today:
            slot_status[slot] = entry.get("status", "fired")
        else:
            slot_status[slot] = "not_fired"

    # --- エラー履歴 (当日分 & 未処理のみ: max_retries=0は承認済みスタレとして除外) ---
    recent_errors = [
        {"task": t.get("task_name"), "error": t.get("last_error", "")[:120],
         "at": t.get("updated_at", "")[:16]}
        for t in state_data.get("recent_history", [])
        if t.get("status") == "error"
        and t.get("updated_at", "")[:10] == today
        and t.get("max_retries", 3) > 0  # max_retries=0 は手動処理済みとして除外
    ][:5]

    # --- 未承認eBay候補数 (承認済みは除外) ---
    pending_count = 0
    try:
        if isinstance(candidates_data, dict):
            cands = candidates_data.get("candidates", [])
            if cands:
                pending_count = len([c for c in cands if not c.get("approved")])
            elif candidates_data.get("review_status") != "approved":
                pending_count = ebay_count  # fallback: 個別フラグなければ全件
        elif isinstance(candidates_data, list):
            pending_count = len([c for c in candidates_data if not c.get("approved")])
        pending_count = max(0, int(pending_count or 0))
    except Exception:
        pending_count = 0

    # --- 要対応 ---
    decision_required = []
    if pending_count > 0:
        decision_required.append(
            f"ebay-review 承認待ち ({pending_count}件): "
            f"python slack_bridge.py approve --task ebay-review --by cap"
        )
    if rakuten_health in ("WARNING", "CRITICAL"):
        decision_required.append(
            f"楽天ROOM health={rakuten_health}: python run.py health で詳細確認"
        )

    # --- 8セクション構築 ---
    handoff = {
        "generated_at":       now_str,
        "date":               today,
        "company_direction":  "CEO→CAP（代表COO）→cyber 3層自動運用。楽天ROOM自動投稿+コイン仕入れリサーチ。",
        "progress": {
            "daily_check":  f"07:30/12:30/18:30 定時チェック稼働中",
            "schedule_slots": slot_status,
            "rakuten":      f"health={rakuten_health} / pool={rakuten_pool}件",
            "coin":         f"DB={coin_total:,}件 / 直近3か月={coin_recent}件" if coin_recent else f"DB={coin_total:,}件",
            "ebay":         f"候補{ebay_count}件 (未承認{pending_count}件) / 最終検索={last_ebay_search or '不明'}",
        },
        "current_issues":     recent_errors,
        "next_priority": [
            "cap/cyber watch 常時起動維持",
            "daily-check 定時発火 監視 (schedule_state.json 確認)",
            f"ebay-review 承認待ち ({pending_count}件)" if pending_count > 0 else "ebay-search 次回実行予定",
        ],
        "risks": [
            f"楽天ROOM health={rakuten_health}" + ("" if rakuten_health == "OK" else " ⚠️"),
            "schedule_state.json 未生成の場合は cap watch 再起動が必要" if not sched_state else "スケジュール管理 正常",
        ],
        "operational_knowledge": [
            "Slack 2500字制限: slim payload のみ送信、詳細は*_latest.jsonを参照",
            "重複発火防止: schedule_state.json の status=done で制御",
            "セッション引継ぎ: このファイル(daily_handoff.json)を読む",
            "cyberは git pull 後に watch 再起動で最新コードを読み込む",
        ],
        "behavioral_notes": {
            "ceo":   "batを押すだけ。日次は #ceo-room を確認。判断事項のみCAPに指示。",
            "cap":   "代表COO。watch常時起動。daily-handoff で日次申し送り生成。",
            "cyber": "実処理担当。watch常時起動。git pullで最新コード維持。ebay-search実行役。",
        },
        "decision_required": decision_required,
        "state_snapshot": {
            "system_status": state_data.get("system_status", "?"),
            "current_tasks": len(state_data.get("current_tasks", [])),
            "recent_errors": len(recent_errors),
        },
        "data_files": {
            "daily_check_latest":  str(DATA_DIR / "daily_check_latest.json"),
            "rakuten_status_latest": str(DATA_DIR / "rakuten_status_latest.json"),
            "coin_status_latest":  str(DATA_DIR / "coin_status_latest.json"),
            "ebay_candidates":     str(DATA_DIR / "ebay_review_candidates.json"),
            "handoff":             str(HANDOFF_FILE),
        },
    }
    return handoff


def _safe_load_json(path: Path):
    """JSON読み込み。不存在/破損は None を返す"""
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _format_handoff_summary(handoff: dict) -> str:
    """CEO向け #ceo-room 投稿用サマリー (< 1000 chars)"""
    lines = []
    now_str = handoff.get("generated_at", "")
    lines.append(f"📋 *日次申し送り* ({now_str})")
    lines.append("")

    prog = handoff.get("progress", {})
    r_text = prog.get("rakuten", "")
    c_text = prog.get("coin", "")
    e_text = prog.get("ebay", "")
    r_icon = "✅" if "OK" in r_text else "⚠️"
    lines.append(f"▶ 楽天ROOM: {r_icon} {r_text}")
    lines.append(f"▶ コイン: {c_text}")
    lines.append(f"▶ eBay: {e_text}")

    # 定時スロット
    slot_st = prog.get("schedule_slots", {})
    slot_line = []
    for slot in SCHEDULE_SLOTS:
        s = slot_st.get(slot, "not_fired")
        icon = "✅" if s == "done" else ("🔄" if s == "running" else "⏳")
        slot_line.append(f"{slot}{icon}")
    lines.append(f"▶ 定時チェック: {' '.join(slot_line)}")
    lines.append("")

    decisions = handoff.get("decision_required", [])
    if decisions:
        lines.append("⚡ *CEO判断が必要*:")
        for d in decisions[:3]:
            lines.append(f"  - {d[:100]}")
        lines.append("")

    issues = handoff.get("current_issues", [])
    if issues:
        lines.append(f"⚠️ *本日エラー*: {len(issues)}件")
        lines.append("")

    lines.append(f"📁 詳細: `daily_handoff.json`")
    return "\n".join(lines)


def _write_gpt_handoff_files(handoff: dict):
    """
    gpt_mousiokuri/ に3ファイルを生成する。
    daily-handoff 実行時に同時呼び出し。
    """
    GPT_MOUSIOKURI_DIR.mkdir(parents=True, exist_ok=True)

    prog    = handoff.get("progress", {})
    issues  = handoff.get("current_issues", [])
    prio    = handoff.get("next_priority", [])
    risks   = handoff.get("risks", [])
    know    = handoff.get("operational_knowledge", [])
    notes   = handoff.get("behavioral_notes", {})
    decs    = handoff.get("decision_required", [])
    snap    = handoff.get("state_snapshot", {})
    gen_at  = handoff.get("generated_at", "?")
    date_s  = handoff.get("date", "?")
    co_dir  = handoff.get("company_direction", "")
    files_d = handoff.get("data_files", {})

    # ---- ① gpt_handoff_latest.json ----
    _atomic_write_json(GPT_MOUSIOKURI_DIR / "gpt_handoff_latest.json", handoff)

    # ---- ② gpt_handoff_latest.md ----
    def _list_md(items):
        if not items:
            return "  (なし)\n"
        out = ""
        for item in items:
            if isinstance(item, dict):
                out += f"  - [{item.get('task','?')}] {item.get('error','')[:100]}  ({item.get('at','')})\n"
            elif item:
                out += f"  - {str(item)[:120]}\n"
        return out or "  (なし)\n"

    md_lines = [
        f"# GPT申し送り - {gen_at}",
        "",
        f"## 1. Company Direction",
        f"  {co_dir}",
        "",
        f"## 2. Progress vs Objective",
        f"  - 楽天ROOM: {prog.get('rakuten', '不明')}",
        f"  - コイン: {prog.get('coin', '不明')}",
        f"  - eBay: {prog.get('ebay', '不明')}",
        f"  - 定時チェック: {prog.get('daily_check', '不明')}",
    ]
    slots = prog.get("schedule_slots", {})
    for slot, s in slots.items():
        icon = "✅" if s == "done" else ("🔄" if s == "running" else "⏳")
        md_lines.append(f"    {slot}: {icon} {s}")
    md_lines += [
        "",
        f"## 3. Current Issues",
        _list_md(issues),
        f"## 4. Next Priority",
        _list_md(prio),
        f"## 5. Risk / Bottlenecks",
        _list_md(risks),
        f"## 6. Operational Knowledge",
        _list_md(know),
        f"## 7. Behavioral Notes",
        f"  - CEO: {notes.get('ceo', '')}",
        f"  - CAP: {notes.get('cap', '')}",
        f"  - cyber: {notes.get('cyber', '')}",
        "",
        f"## 8. Decision Required",
        _list_md(decs),
    ]
    _atomic_write_text(GPT_MOUSIOKURI_DIR / "gpt_handoff_latest.md",
                       "\n".join(md_lines))

    # ---- ③ gpt_bootstrap.txt ----
    bt_issues = "\n".join(
        f"  - [{i.get('task','?')}] {i.get('error','')[:80]}" for i in issues[:3]
    ) if issues else "  (なし)"
    bt_prio = "\n".join(f"  {n+1}. {p}" for n, p in enumerate(prio[:5]) if p)
    bt_decs = "\n".join(f"  - {d[:100]}" for d in decs[:3]) if decs else "  (なし)"
    slots_str = "  " + " / ".join(
        f"{sl}={'done' if ss=='done' else 'not_fired'}" for sl, ss in slots.items()
    )

    bootstrap = f"""あなたは前回のセッションから継続しているCTO（GPT）です。

これは新しいセッションですが、以下の情報が現在の正しい状態です。
会話履歴は存在しないため、この情報のみを基準に判断してください。

# CURRENT STATE
- system_status: {snap.get('system_status', '?')}
- state-audit: {"CLEAN" if snap.get('current_tasks', 0) == 0 and snap.get('recent_errors', 0) == 0 else "要確認"}
- current_tasks: {snap.get('current_tasks', 0)}件
- 本日エラー: {snap.get('recent_errors', 0)}件

# DAILY HANDOFF（要約）
- Company Direction: {co_dir}
- 楽天ROOM: {prog.get('rakuten', '不明')}
- コイン: {prog.get('coin', '不明')}
- eBay: {prog.get('ebay', '不明')}
- 定時チェック(07:30/12:30/18:30):
{slots_str}

## Current Issues
{bt_issues}

## Next Priority
{bt_prio}

## Decision Required
{bt_decs}

# IMPORTANT FILES
- state/system_state.json          ← 唯一の真実
- {files_d.get('daily_check_latest', '~/.slack_bridge/daily_check_latest.json')}
- {files_d.get('rakuten_status_latest', '~/.slack_bridge/rakuten_status_latest.json')}
- {files_d.get('coin_status_latest', '~/.slack_bridge/coin_status_latest.json')}
- {files_d.get('handoff', '~/.slack_bridge/daily_handoff.json')}
- {str(GPT_MOUSIOKURI_DIR / 'gpt_handoff_latest.md')}

# RULES
- 会話履歴に依存しない
- state を唯一の真実とする
- Slack は要約のみ（長文禁止・2500字制限）
- 自動化は「止まらない設計」を優先
- 実装は slack_bridge.py のみ変更（新ファイル原則不可）

# SYSTEM STRUCTURE
- CEO → CAP（代表COO）のみ指示
- CAP → cyber / 各ツール実行
- cyber → 実処理（楽天ROOM BOT / coin ebay-search）
- CAP → CEO へ #ceo-room 集約報告

# KEY COMMANDS
- python slack_bridge.py watch           ← 常時監視（cap/cyber両方）
- python slack_bridge.py daily-check     ← 全事業チェック（手動トリガー）
- python slack_bridge.py rakuten-status  ← 楽天ROOM状態確認
- python slack_bridge.py coin-status     ← コイン状態確認
- python slack_bridge.py daily-handoff   ← 申し送り生成・更新
- python slack_bridge.py state-audit     ← 整合性確認
- python slack_bridge.py approve --task ebay-review --by cap  ← eBay承認

# NEXT ACTION
以下の優先順位で状況を確認・対応してください：
{bt_prio}

---
生成日時: {gen_at}
"""
    _atomic_write_text(GPT_MOUSIOKURI_DIR / "gpt_bootstrap.txt", bootstrap)

    logger.info(f"GPT申し送りファイル生成完了: {GPT_MOUSIOKURI_DIR}")
    return True


def _atomic_write_json(path: Path, data: dict):
    """JSON をatomic write"""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"atomic_write_json 失敗 {path}: {e}")


def _atomic_write_text(path: Path, text: str):
    """テキストをatomic write"""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"atomic_write_text 失敗 {path}: {e}")


def cmd_daily_handoff():
    """
    日次申し送り生成・保存・#ceo-room投稿。
    capのローカル実行のみ。cyberへの送信なし。
    """
    logger.info("daily-handoff 生成開始")
    handoff = _generate_daily_handoff()

    # JSON保存 (atomic write)
    _atomic_write_json(HANDOFF_FILE, handoff)
    logger.info(f"daily_handoff.json 保存完了: {HANDOFF_FILE}")

    # GPT申し送りファイル生成 (gpt_mousiokuri/)
    try:
        _write_gpt_handoff_files(handoff)
    except Exception as e:
        logger.warning(f"GPT申し送りファイル生成失敗 (継続): {e}")

    # Slack投稿
    summary = _format_handoff_summary(handoff)
    success = send_message(summary, channel=CEO_ROOM_CHANNEL)
    if success:
        logger.info("daily-handoff #ceo-room 投稿完了")
    else:
        logger.warning("daily-handoff #ceo-room 投稿失敗 (ファイルは保存済み)")

    # ターミナル表示 (CP932環境でのUnicodeエラーを回避: buffer直接書き込み)
    try:
        print(summary)
    except UnicodeEncodeError:
        import sys as _sys
        _sys.stdout.buffer.write((summary + "\n").encode("utf-8", errors="replace"))
        _sys.stdout.buffer.flush()
    try:
        print(f"\n詳細: {HANDOFF_FILE}")
        print(f"decisions: {len(handoff.get('decision_required', []))}件")
    except UnicodeEncodeError:
        pass


HANDLERS = {
    "test-ping":      handle_test_ping,
    "ebay-search":    handle_ebay_search,
    "ebay-review":    handle_ebay_review,
    "git-pull":       handle_git_pull,
    "report":         handle_report,
    "set-env":        handle_set_env,
    "ceo-report":     handle_ceo_report,
    "rakuten-status": handle_rakuten_status,
    "rakuten-report": handle_rakuten_report,
    "daily-check":    handle_daily_check,
    "daily-report":   handle_daily_report,
    "coin-status":    handle_coin_status,
    "coin-report":    handle_coin_report,
}


# ============================================================
# タスク処理（subprocess分離）
# ============================================================
def dispatch_task(msg_data: dict):
    """ハンドラをバックグラウンドスレッドで実行し、DONE/ERRORを返送"""
    task_name = msg_data.get("task", "")
    task_id   = msg_data.get("task_id", "")

    # --- 依存チェック ---
    can_run, blocked_reason = state_mgr.check_dependency(task_name)
    if not can_run:
        logger.warning(f"依存タスク未完了でブロック: {task_name} ({blocked_reason})")
        state_mgr.task_blocked(task_id, blocked_reason or "dependency not met")
        # ERROR ではなく BLOCKED タイプで返送（失敗ではなく待機状態）
        blocked_msg = make_response_msg(msg_data, "BLOCKED",
                                        {"reason": blocked_reason,
                                         "error_type": ErrorType.DEPENDENCY_FAILED,
                                         "wait_for": TASK_FLOW.get(task_name, {}).get("requires")})
        send_bridge_msg(blocked_msg)
        save_task_registry_entry(task_id, "BLOCKED", {"task": task_name, "reason": blocked_reason})
        log_event("blocked", {"task_id": task_id, "task": task_name, "reason": blocked_reason})
        return

    handler = HANDLERS.get(task_name)
    if handler is None:
        logger.warning(f"未知のタスク: {task_name}")
        err_msg = make_response_msg(msg_data, "ERROR", {"error": f"Unknown task: {task_name}"})
        send_bridge_msg(err_msg)
        save_task_registry_entry(task_id, "ERROR", {"task": task_name})
        state_mgr.task_error(task_id, f"Unknown task: {task_name}",
                             error_type=ErrorType.CONFIG_MISSING)
        return

    def _run():
        try:
            logger.info(f"タスク実行開始: {task_name} (id={task_id[:8]})")
            state_mgr.task_running(task_id)
            result = handler(msg_data)

            # ハンドラが _post_send_msg を返した場合、DONE送信後に送る（競合回避）
            post_send_msg = result.pop("_post_send_msg", None) if isinstance(result, dict) else None

            done_msg = make_response_msg(msg_data, "DONE", result)
            send_bridge_msg(done_msg)
            save_task_registry_entry(task_id, "DONE", {"task": task_name})
            state_mgr.task_done(task_id, f"{task_name} completed")
            logger.info(f"タスク完了: {task_name} (id={task_id[:8]})")
            log_event("done", {"task_id": task_id, "task": task_name})

            # DONE送信後に後続メッセージ送信（依存チェックが正しく動くよう順序保証）
            if post_send_msg:
                send_bridge_msg(post_send_msg)
                add_pending_task(post_send_msg)
                pt_name = post_send_msg.get("task", "")
                pt_id   = post_send_msg.get("task_id", "")
                state_mgr.task_queued(pt_id, pt_name, get_sender(),
                                      post_send_msg.get("to", ""),
                                      depends_on=task_name,
                                      workflow_id=post_send_msg.get("workflow_id"))
                logger.info(f"[post-DONE] {pt_name} 送信: id={pt_id[:8]}")
                log_event("post_send", {"task": pt_name, "task_id": pt_id, "after": task_name})

            # --- 自動 enqueue: 次タスクを state に積んで Slack 送信 ---
            parent_wf_id = msg_data.get("workflow_id")   # 親コンテキストから直接取得
            next_task_id = state_mgr.enqueue_next(task_name, task_id,
                                                  parent_workflow_id=parent_wf_id)
            if next_task_id:
                flow = TASK_FLOW.get(task_name, {})
                next_name = flow.get("next")
                to_id     = flow.get("to", "cyber")
                auto_msg  = make_task_msg(
                    from_id=get_sender(), to_id=to_id,
                    task=next_name, task_id=next_task_id,
                    source=SOURCE_AUTO,
                    workflow_id=parent_wf_id,
                    payload={"auto_triggered_by": task_name, "parent_id": task_id},
                )
                send_bridge_msg(auto_msg)
                add_pending_task(auto_msg)
                logger.info(f"自動enqueue: {next_name} -> {to_id} (id={next_task_id[:8]})")
                log_event("auto_enqueue", {"task": next_name, "task_id": next_task_id, "to": to_id})

        except Exception as e:
            logger.error(f"タスク実行エラー: {task_name} - {e}")
            err_msg = make_response_msg(msg_data, "ERROR", {"error": str(e)})
            send_bridge_msg(err_msg)
            save_task_registry_entry(task_id, "ERROR", {"task": task_name, "error": str(e)})
            state_mgr.task_error(task_id, str(e))
            log_event("error", {"task_id": task_id, "task": task_name, "error": str(e)})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


# ============================================================
# リトライ/エスカレーション チェック
# ============================================================
def check_pending_tasks():
    """pending_tasksを確認し、タイムアウト時にリトライまたはエスカレーション"""
    tasks = load_pending_tasks()
    now = datetime.now(timezone.utc)
    changed = False

    for t in tasks:
        task_id = t["task_id"]
        status = t.get("status", "sent")
        sent_at = datetime.fromisoformat(t["sent_at"])

        if status == "sent":
            # ACK待ち: 3分タイムアウト
            elapsed = (now - sent_at).total_seconds()
            if elapsed > ACK_TIMEOUT_SEC:
                retry_count = t.get("retry_count", 0)
                if retry_count < MAX_RETRIES:
                    # 再送
                    logger.warning(f"ACKタイムアウト。再送 ({retry_count + 1}/{MAX_RETRIES}): {task_id[:8]}")
                    t["retry_count"] = retry_count + 1
                    t["sent_at"] = now.isoformat()
                    send_bridge_msg(t["msg_data"])
                    state_mgr.task_retry(task_id)
                    log_event("retry", {"task_id": task_id, "retry_count": t["retry_count"]})
                    changed = True
                else:
                    # エスカレーション
                    logger.error(f"3回失敗。エスカレーション: {task_id[:8]}")
                    escalate_msg = make_task_msg(
                        from_id=get_sender(),
                        to_id="ceo",
                        task=t.get("task", "unknown"),
                        msg_type="ESCALATE",
                        task_id=task_id,
                        correlation_id=t["msg_data"].get("correlation_id", ""),
                        payload={"reason": "ACK timeout after 3 attempts", "original_to": t.get("to", "")},
                    )
                    send_bridge_msg(escalate_msg, channel=CEO_ROOM_CHANNEL)
                    t["status"] = "escalated"
                    save_task_registry_entry(task_id, "ESCALATED", {"reason": "ACK timeout"})
                    state_mgr.task_error(task_id, "ACK timeout after 3 attempts",
                                         error_type=ErrorType.ACK_TIMEOUT)
                    log_event("escalate", {"task_id": task_id, "reason": "ACK timeout"})
                    changed = True

        elif status == "acked":
            # DONE待ち: 30分タイムアウト
            ack_at = datetime.fromisoformat(t["ack_at"])
            elapsed = (now - ack_at).total_seconds()
            if elapsed > DONE_TIMEOUT_SEC:
                logger.error(f"DONE タイムアウト(30分)。エスカレーション: {task_id[:8]}")
                escalate_msg = make_task_msg(
                    from_id=get_sender(),
                    to_id="ceo",
                    task=t.get("task", "unknown"),
                    msg_type="ESCALATE",
                    task_id=task_id,
                    correlation_id=t["msg_data"].get("correlation_id", ""),
                    payload={"reason": "DONE timeout after 30min", "original_to": t.get("to", "")},
                )
                send_bridge_msg(escalate_msg, channel=CEO_ROOM_CHANNEL)
                t["status"] = "escalated"
                save_task_registry_entry(task_id, "ESCALATED", {"reason": "DONE timeout"})
                state_mgr.task_error(task_id, "DONE timeout after 30min",
                                     error_type=ErrorType.DONE_TIMEOUT)
                log_event("escalate", {"task_id": task_id, "reason": "DONE timeout"})
                changed = True

    if changed:
        # escalated/completedを除去
        tasks = [t for t in tasks if t.get("status") not in ("escalated", "completed")]
        save_pending_tasks(tasks)


# ============================================================
# 監視ループ（watch）
# ============================================================
def watch_channel(interval: int = 5):
    """ai-bridgeチャンネルを定期ポーリングで監視"""
    logger.info(f"Slack監視開始 (間隔: {interval}秒, sender: {get_sender()})")

    # --- 起動時 state 復元 ---
    state = state_mgr.load()
    stale = [t for t in state.get("current_tasks", [])
             if t.get("status") in ("running", "received", "acknowledged")]
    if stale:
        logger.warning(f"前回セッションの未完了タスク {len(stale)}件を検出:")
        for t in stale:
            logger.warning(f"  [{t.get('status')}] {t.get('task_name')} id={t.get('task_id','?')[:8]}")
            # running/acknowledged → error（前セッションで中断されたため）
            state_mgr.task_error(t["task_id"], "interrupted: watch restarted")
        logger.warning("これらのタスクはerrorに移行しました。必要に応じてリトライしてください。")

    last_seen_ts = get_last_seen_ts()
    my_sender = get_sender()
    last_retry_check = time.time()
    last_schedule_check = time.time()

    while True:
        try:
            # --- 1回のconversations.history ---
            messages = receive_messages(limit=50)

            if messages:
                # messagesは新しい順。逆順にして古い方から処理
                new_msgs = []
                for msg in reversed(messages):
                    if msg["ts"] > last_seen_ts:
                        new_msgs.append(msg)

                for msg in new_msgs:
                    text = msg["text"]

                    # 自分が送ったメッセージはスキップ
                    if f"[{my_sender}]" in text:
                        pass
                    else:
                        # ブリッジメッセージのパース
                        bridge_data = parse_bridge_message(text)

                        if bridge_data is not None:
                            _handle_bridge_msg(bridge_data, my_sender)
                        else:
                            # 旧形式メッセージ → latest_msg.txtに保存
                            LATEST_MSG_FILE.write_text(text, encoding="utf-8")
                            logger.info(f"旧形式メッセージ: {text[:80]}")
                            log_event("receive_legacy", {"text": text[:200]})

                # last_seen更新（最新のts）
                newest_ts = messages[0]["ts"]
                if newest_ts > last_seen_ts:
                    last_seen_ts = newest_ts
                    save_last_seen_ts(last_seen_ts)

            # --- リトライチェック（30秒ごと） ---
            now = time.time()
            if now - last_retry_check >= RETRY_CHECK_INTERVAL:
                check_pending_tasks()
                last_retry_check = now

            # --- 定時スケジュールチェック（60秒ごと、cap のみ発火） ---
            if now - last_schedule_check >= SCHEDULE_CHECK_INTERVAL:
                if my_sender == "cap":
                    try:
                        _check_and_fire_schedules()
                    except Exception as se:
                        logger.error(f"定時スケジュールチェックエラー: {se}")
                last_schedule_check = now

        except Exception as e:
            logger.error(f"監視ループエラー: {e}")

        time.sleep(interval)


def _handle_bridge_msg(bridge_data: dict, my_sender: str):
    """受信したブリッジメッセージを処理"""
    msg_type = bridge_data.get("type", "")
    task_id = bridge_data.get("task_id", "")
    from_id = bridge_data.get("from", "")
    to_id = bridge_data.get("to", "")
    task_name = bridge_data.get("task", "")

    logger.info(f"受信: type={msg_type} task={task_name} from={from_id} id={task_id[:8]}")
    log_event("receive", {"type": msg_type, "task": task_name, "task_id": task_id, "from": from_id})

    # 自分宛てでなければスキップ
    if to_id and to_id != my_sender:
        logger.debug(f"自分宛てではない (to={to_id}). スキップ")
        return

    if msg_type == "TASK":
        # 重複チェック
        if is_task_processed(task_id):
            logger.info(f"重複タスク。スキップ: {task_id[:8]}")
            return

        # state: received（workflow_id をメッセージから引き継ぐ）
        wf_id = bridge_data.get("workflow_id")
        state_mgr.task_received(task_id, task_name, my_sender, workflow_id=wf_id)

        # ACK返送（ループ防止: TASKのみACKを返す）
        ack_msg = make_response_msg(bridge_data, "ACK")
        send_bridge_msg(ack_msg)
        save_task_registry_entry(task_id, "ACK", {"task": task_name})
        log_event("ack_sent", {"task_id": task_id, "task": task_name})
        logger.info(f"ACK送信: {task_name} (id={task_id[:8]})")

        # state: acknowledged
        state_mgr.task_acknowledged(task_id)

        # ハンドラディスパッチ（スレッドで非同期実行）
        dispatch_task(bridge_data)

    elif msg_type == "ACK":
        # 自分が送ったタスクへのACK
        logger.info(f"ACK受信: {task_name} (id={task_id[:8]})")
        update_pending_task(task_id, {
            "status": "acked",
            "ack_at": datetime.now(timezone.utc).isoformat(),
        })
        state_mgr.task_acknowledged(task_id)
        log_event("ack_received", {"task_id": task_id})

    elif msg_type == "DONE":
        logger.info(f"DONE受信: {task_name} (id={task_id[:8]})")
        remove_pending_task(task_id)
        save_task_registry_entry(task_id, "DONE_RECEIVED", {"task": task_name})
        state_mgr.task_done(task_id, f"DONE received for {task_name}")
        log_event("done_received", {"task_id": task_id, "payload": bridge_data.get("payload", {})})

    elif msg_type == "ERROR":
        logger.warning(f"ERROR受信: {task_name} (id={task_id[:8]})")
        remove_pending_task(task_id)
        save_task_registry_entry(task_id, "ERROR_RECEIVED", {"task": task_name})
        error_detail = bridge_data.get("payload", {}).get("error", "unknown error")
        state_mgr.task_error(task_id, error_detail)
        log_event("error_received", {"task_id": task_id, "payload": bridge_data.get("payload", {})})

    elif msg_type == "BLOCKED":
        # 自分が送ったタスクがブロックされた（待機状態。エラーではない）
        reason = bridge_data.get("payload", {}).get("reason", "dependency not met")
        wait_for = bridge_data.get("payload", {}).get("wait_for", "")
        logger.info(f"BLOCKED受信: {task_name} (id={task_id[:8]}) reason={reason}")
        # pending から除去しない（wait_for 完了後にリトライ可能）
        update_pending_task(task_id, {"status": "blocked", "blocked_reason": reason})
        state_mgr.task_blocked(task_id, reason)
        log_event("blocked_received", {"task_id": task_id, "reason": reason, "wait_for": wait_for})

    elif msg_type == "ESCALATE":
        logger.error(f"ESCALATE受信: {task_name} (id={task_id[:8]})")
        log_event("escalate_received", {"task_id": task_id})

    # ACK/DONE/ERROR/BLOCKED/ESCALATEに対してACKは返さない（ループ防止）


# ============================================================
# CLIコマンド
# ============================================================
def cmd_send_task(task: str, to: str, payload: dict = None,
                  source: str = SOURCE_MANUAL):
    """タスク送信。source=manual（デフォルト）または source=auto"""
    sender = get_sender()
    msg = make_task_msg(
        from_id=sender,
        to_id=to,
        task=task,
        source=source,
        payload=payload or {},
    )
    success = send_bridge_msg(msg)
    if success:
        add_pending_task(msg)
        state_mgr.task_queued(msg["task_id"], task, sender, to,
                              workflow_id=msg.get("workflow_id"))
        logger.info(f"タスク送信完了 [{source}]: {task} -> {to} (id={msg['task_id'][:8]})")
        log_event("task_sent", {"task_id": msg["task_id"], "task": task, "to": to,
                                "source": source})
    return success


def cmd_retry_pending():
    """未完了タスクを手動リトライ"""
    tasks = load_pending_tasks()
    if not tasks:
        logger.info("未完了タスクなし")
        return

    for t in tasks:
        logger.info(f"リトライ: {t['task']} (id={t['task_id'][:8]}, status={t['status']})")
        t["retry_count"] = 0
        t["sent_at"] = datetime.now(timezone.utc).isoformat()
        t["status"] = "sent"
        send_bridge_msg(t["msg_data"])
        log_event("manual_retry", {"task_id": t["task_id"]})

    save_pending_tasks(tasks)
    logger.info(f"{len(tasks)}件リトライ実行")


def cmd_status():
    """タスク状態一覧表示"""
    # Pending tasks
    pending = load_pending_tasks()
    print("\n=== Pending Tasks ===")
    if not pending:
        print("  なし")
    else:
        for t in pending:
            print(f"  [{t['status']}] {t['task']} -> {t.get('to','')} "
                  f"(id={t['task_id'][:8]}, retry={t.get('retry_count',0)})")

    # Recent registry entries (last 20)
    print("\n=== Recent Registry (last 20) ===")
    registry_entries = []
    if TASK_REGISTRY_FILE.exists():
        with open(TASK_REGISTRY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        registry_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    if not registry_entries:
        print("  なし")
    else:
        for entry in registry_entries[-20:]:
            print(f"  [{entry.get('status','')}] {entry.get('task','')} "
                  f"(id={entry['task_id'][:8]}, {entry.get('timestamp','')})")

    print()


# ============================================================
# 旧互換コマンド
# ============================================================
def cmd_ceo_report(file_path: str = None, task_id: str = None):
    """ebay_review_candidates.jsonを読み込み、#ceo-roomに承認済み候補を報告"""
    if file_path:
        candidates_file = Path(file_path)
    else:
        candidates_file = DATA_DIR / "ebay_review_candidates.json"

    if not candidates_file.exists():
        logger.error(f"候補ファイルが見つかりません: {candidates_file}")
        print(f"Error: {candidates_file} not found")
        return False

    try:
        with open(candidates_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"ファイル読み込みエラー: {e}")
        print(f"Error: {e}")
        return False

    # candidates はデータ構造に応じてリストまたはdict内のリスト
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        candidates = data.get("candidates", [])
    else:
        print("Error: unexpected data format")
        return False

    if not candidates:
        print("候補なし。報告不要。")
        return True

    # CEO報告メッセージ構築
    lines = []
    lines.append("eBay仕入れ候補（キャップ確認済み）")
    lines.append("")
    for i, c in enumerate(candidates, 1):
        mgmt = c.get("mgmt_no", "???")
        line1 = c.get("db_line1", "")
        grader = c.get("db_grader", "")
        grade = c.get("db_grade", "")
        limit_usd = c.get("ebay_limit_usd", 0)
        limit_jpy = c.get("ebay_limit_jpy", 0)
        bids = c.get("bid_count", 0)
        url = c.get("ebay_url", "")

        lines.append(f"{i}. #{mgmt} | {line1} {grader} {grade}")
        lines.append(f"   仕入上限: USD{limit_usd:,} ({limit_jpy:,}円)")
        lines.append(f"   入札: {bids}件")
        lines.append(f"   URL: {url}")
        lines.append("")

    report_text = "\n".join(lines)

    # #ceo-roomに送信
    success = send_message(report_text, sender=get_sender(), channel=CEO_ROOM_CHANNEL)
    if success:
        logger.info(f"CEO報告送信完了: {len(candidates)}件")
        print(f"CEO報告を#ceo-roomに送信しました（{len(candidates)}件）")
        if task_id:
            state_mgr.task_report_sent(task_id, CEO_ROOM_CHANNEL, status="sent")
    else:
        logger.error("CEO報告送信失敗")
        print("Error: CEO報告送信失敗")
        if task_id:
            state_mgr.task_report_sent(task_id, CEO_ROOM_CHANNEL, status="failed")
    return success


def _fmt_ts(iso: str | None) -> str:
    """ISOタイムスタンプを JST HH:MM:SS 形式に変換。Noneや不正な値は '-' を返す"""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
        dt_jst = dt.astimezone(JST)
        return dt_jst.strftime("%m/%d %H:%M:%S")
    except ValueError:
        return iso[:19]


def _status_label(status: str) -> str:
    """ステータスを固定幅ラベルに変換"""
    labels = {
        TaskStatus.QUEUED:         "QUEUED  ",
        TaskStatus.WAITING_ACK:    "WAIT_ACK",
        TaskStatus.RECEIVED:       "RECV    ",
        TaskStatus.ACKNOWLEDGED:   "ACK     ",
        TaskStatus.RUNNING:        "RUNNING ",
        TaskStatus.WAITING_DONE:   "WAIT_DON",
        TaskStatus.WAITING_MANUAL: "MANUAL! ",
        TaskStatus.BLOCKED:        "BLOCKED ",
        TaskStatus.DONE:           "DONE    ",
        TaskStatus.ERROR:          "ERROR   ",
    }
    return labels.get(status, f"{status:<8}")


def cmd_approve(task_name: str, approved_by: str):
    """
    タスクの review_status を approved に設定し、ceo-report 自動起動の条件を満たす。
    python slack_bridge.py approve --task ebay-review --by cap
    """
    ok = state_mgr.approve_task(task_name, approved_by)
    if ok:
        print(f"[OK] {task_name} approved by {approved_by}")
        print(f"     ceo-report は次回の自動 enqueue で起動されます。")

        # 承認直後に ceo-report を自動 enqueue できるか確認
        can_run, reason = state_mgr.check_dependency("ceo-report")
        if can_run:
            flow    = TASK_FLOW.get("ebay-review", {})
            next_id = state_mgr.enqueue_next("ebay-review", "approved")
            if next_id:
                to_id    = TASK_FLOW.get("ceo-report", {}).get("to", "cap")
                # ebay-review の workflow_id を引き継ぐ
                parent_wf_id = None
                _s = state_mgr.load()
                for _t in _s.get("recent_history", []):
                    if _t.get("task_name") == "ebay-review" and _t.get("status") == TaskStatus.DONE:
                        parent_wf_id = _t.get("workflow_id")
                        break
                auto_msg = make_task_msg(
                    from_id=get_sender(), to_id=to_id,
                    task="ceo-report", task_id=next_id,
                    source=SOURCE_AUTO,
                    workflow_id=parent_wf_id,
                    payload={"auto_triggered_by": "ebay-review",
                             "approved_by": approved_by},
                )
                success = send_bridge_msg(auto_msg)
                if success:
                    add_pending_task(auto_msg)
                    print(f"[AUTO] ceo-report -> {to_id} (id={next_id[:8]})")
                    log_event("auto_enqueue_after_approve",
                              {"task": "ceo-report", "task_id": next_id, "to": to_id})
                else:
                    print(f"[WARN] ceo-report enqueue failed (Slack送信エラー)")
            else:
                print(f"[INFO] ceo-report は既に進行中のためスキップ")
        else:
            print(f"[WAIT] ceo-report 実行条件未達: {reason}")
    else:
        print(f"[FAIL] {task_name} の done エントリが見つかりません。")
        print(f"       ebay-review が完了してから承認してください。")


def cmd_state_audit():
    """
    state と Slack ログの整合性を確認する簡易監査コマンド。
    python slack_bridge.py state-audit
    """
    W = 64
    print(f"\n{'='*W}")
    print(f"  State Audit")
    print(f"  {_fmt_ts(datetime.now(JST).isoformat())}")
    print(f"{'='*W}")

    # 1. StateManager 内部チェック
    issues = state_mgr.audit()
    print(f"\n  [1] Internal consistency ({len(issues)} issue(s))")
    if not issues:
        print("    OK - no issues found")
    for iss in issues:
        lvl  = iss.get("level", "WARN")
        name = iss.get("task_name", "?")
        tid  = iss.get("task_id", "?")[:8]
        msg  = iss.get("issue", "")
        print(f"    [{lvl}] {name} id={tid}: {msg}")

    # 2. current_tasks に DONE が残っていないか
    state = state_mgr.load()
    stuck_done = [t for t in state.get("current_tasks", [])
                  if t.get("status") == TaskStatus.DONE]
    print(f"\n  [2] DONE tasks stuck in current_tasks: {len(stuck_done)}")
    if stuck_done:
        for t in stuck_done:
            print(f"    WARN: {t.get('task_name')} id={t.get('task_id','?')[:8]} "
                  f"should be in history")
    else:
        print("    OK")

    # 3. error タスクで retry_count < max_retries なのに history に放置されていないか
    unretried_errors = [
        t for t in state.get("recent_history", [])
        if t.get("status") == TaskStatus.ERROR
        and t.get("retry_count", 0) < t.get("max_retries", 3)
        and t.get("error_type") not in (
            ErrorType.MANUAL_REQUIRED, ErrorType.DEPENDENCY_FAILED)
    ]
    print(f"\n  [3] Retriable errors in history not retried: {len(unretried_errors)}")
    if unretried_errors:
        for t in unretried_errors[:5]:
            etype = t.get("error_type", ErrorType.UNKNOWN)
            print(f"    WARN: {t.get('task_name')} id={t.get('task_id','?')[:8]} "
                  f"[{etype}] retry={t.get('retry_count',0)}/{t.get('max_retries',3)}")
        print(f"    -> run: python slack_bridge.py retry-pending")
    else:
        print("    OK")

    # 4. Slack の直近レジストリと state の整合チェック
    registry = load_task_registry()
    state_ids_current  = {t["task_id"] for t in state.get("current_tasks", [])}
    state_ids_history  = {t["task_id"] for t in state.get("recent_history", [])}
    all_state_ids = state_ids_current | state_ids_history

    slack_done_not_in_state = [
        tid for tid, entry in registry.items()
        if entry.get("status") in ("DONE", "DONE_RECEIVED")
        and tid not in all_state_ids
    ]
    print(f"\n  [4] Slack DONE tasks not reflected in state: {len(slack_done_not_in_state)}")
    if slack_done_not_in_state:
        for tid in slack_done_not_in_state[:5]:
            entry = registry[tid]
            print(f"    INFO: task={entry.get('task','?')} id={tid[:8]} "
                  f"(may be from older session)")
    else:
        print("    OK")

    # 5. blocked タスクで依存が解消されているのに放置されていないか
    blocked_tasks = [t for t in state.get("current_tasks", [])
                     if t.get("status") == TaskStatus.BLOCKED]
    resolvable = []
    for t in blocked_tasks:
        dep = t.get("depends_on")
        if dep:
            can_run, _ = state_mgr.check_dependency(t.get("task_name", ""))
            if can_run:
                resolvable.append(t)
    print(f"\n  [5] Blocked tasks now resolvable: {len(resolvable)}")
    if resolvable:
        for t in resolvable:
            print(f"    ACTION: {t.get('task_name')} id={t.get('task_id','?')[:8]} "
                  f"-> dependency resolved, ready to retry")
        print(f"    -> run: python slack_bridge.py retry-pending")
    else:
        print("    OK")

    # 6. 自動enqueue の重複チェック（同名タスクが current_tasks に複数存在）
    state2 = state_mgr.load()
    from collections import Counter
    auto_counts = Counter(
        t.get("task_name") for t in state2.get("current_tasks", [])
        if t.get("source") == SOURCE_AUTO
    )
    dup_autos = {name: cnt for name, cnt in auto_counts.items() if cnt > 1}
    print(f"\n  [6] Duplicate auto-enqueue in current_tasks: {len(dup_autos)}")
    if dup_autos:
        for name, cnt in dup_autos.items():
            print(f"    WARN: {name} appears {cnt} times (source=auto)")
    else:
        print("    OK")

    total_issues = (len(issues) + len(stuck_done) + len(unretried_errors)
                    + len(resolvable) + len(dup_autos))
    print(f"\n  {'='*60}")
    verdict = "CLEAN" if total_issues == 0 else f"NEEDS ATTENTION ({total_issues} items)"
    print(f"  Result: {verdict}")
    print(f"  State file: {STATE_FILE}\n")


def cmd_state_summary():
    """system_state.json の内容をCEO/cap/cyber共通で見やすく表示"""
    state = state_mgr.load()
    W = 64

    # ヘッダー
    sys_status = state.get("system_status", "?").upper()
    updated    = _fmt_ts(state.get("updated_at"))
    print(f"\n{'='*W}")
    print(f"  SolarWorks AI  |  System State  |  {sys_status}")
    print(f"  Updated: {updated}   (v{state.get('version','?')})")
    print(f"{'='*W}")

    # 次アクション（グローバル）
    next_act = state.get("next_action")
    if next_act:
        print(f"\n  >> NEXT ACTION: {next_act}")

    def _print_task_row(t: dict):
        tid      = t.get("task_id", "?")[:8]
        name     = t.get("task_name", "?")
        owner    = t.get("owner", "?")
        status   = t.get("status", "?")
        retries  = t.get("retry_count", 0)
        maxr     = t.get("max_retries", 3)
        timeout  = _fmt_ts(t.get("timeout_at"))
        src      = t.get("source", SOURCE_MANUAL)
        src_mark = "[A]" if src == SOURCE_AUTO else "   "
        dep_next = t.get("depends_on_next")
        rev_st   = t.get("review_status")
        print(f"    {src_mark}[{_status_label(status)}] {name:<18} owner={owner}  id={tid}")
        if t.get("waiting_for"):
            print(f"        waiting : {t['waiting_for']}")
        if dep_next:
            print(f"        next dep: {dep_next}")
        print(f"        retry   : {retries}/{maxr}   timeout: {timeout}")
        if t.get("next_action"):
            print(f"        action  : {t['next_action']}")
        if status == TaskStatus.BLOCKED:
            print(f"        BLOCKED : {t.get('last_error','')}")
        elif t.get("last_error"):
            etype = t.get("error_type", ErrorType.UNKNOWN)
            print(f"        ERROR   : [{etype}] {t['last_error']}")
        if rev_st:
            appr_by = t.get("approved_by", "-")
            appr_at = _fmt_ts(t.get("approved_at"))
            print(f"        review  : {rev_st}  by={appr_by}  at={appr_at}")

    # 進行中タスク（blocked / error / その他 を分けて表示）
    current = state.get("current_tasks", [])
    active  = [t for t in current if t.get("status") not in
               (TaskStatus.BLOCKED, TaskStatus.ERROR, TaskStatus.WAITING_MANUAL)]
    blocked = [t for t in current if t.get("status") == TaskStatus.BLOCKED]
    errored = [t for t in current if t.get("status") == TaskStatus.ERROR]
    waiting_m = [t for t in current if t.get("status") == TaskStatus.WAITING_MANUAL]

    print(f"\n  [ Active Tasks: {len(active)} ]")
    print(f"  {'-'*60}")
    if not active:
        print("    (none - system idle)")
    for t in active:
        _print_task_row(t)
    print(f"  {'-'*60}")

    if blocked:
        print(f"\n  [ Blocked Tasks (waiting dependency): {len(blocked)} ]")
        print(f"  {'-'*60}")
        for t in blocked:
            _print_task_row(t)
        print(f"  {'-'*60}")

    if waiting_m:
        print(f"\n  [ Waiting Manual Approval: {len(waiting_m)} ]")
        print(f"  {'-'*60}")
        for t in waiting_m:
            _print_task_row(t)
        print(f"  {'-'*60}")

    if errored:
        print(f"\n  [ Error Tasks (in current): {len(errored)} ]")
        print(f"  {'-'*60}")
        for t in errored:
            _print_task_row(t)
        print(f"  {'-'*60}")

    # 直近履歴（done / error / blocked を区別して色分け）
    history = state.get("recent_history", [])
    hist_done    = [t for t in history if t.get("status") == TaskStatus.DONE]
    hist_error   = [t for t in history if t.get("status") == TaskStatus.ERROR]
    hist_blocked = [t for t in history if t.get("status") == TaskStatus.BLOCKED]

    print(f"\n  [ Recent History: {len(history)} total  "
          f"(done={len(hist_done)}  error={len(hist_error)}  blocked={len(hist_blocked)}) ]")
    print(f"  {'-'*60}")
    if not history:
        print("    (none)")
    for t in history[:10]:
        tid    = t.get("task_id", "?")[:8]
        name   = t.get("task_name", "?")
        owner  = t.get("owner", "?")
        status = t.get("status", "?")
        ts     = _fmt_ts(t.get("updated_at"))
        src    = t.get("source", SOURCE_MANUAL)
        src_m  = "[A]" if src == SOURCE_AUTO else "   "
        line   = f"    {src_m}[{_status_label(status)}] {name:<18} owner={owner}  id={tid}  {ts}"
        if status == TaskStatus.ERROR:
            etype = t.get("error_type", ErrorType.UNKNOWN)
            line += f"\n        ERROR: [{etype}] {t.get('last_error','')}"
        elif status == TaskStatus.BLOCKED:
            line += f"\n        BLOCKED: {t.get('last_error','')}"
        elif status == TaskStatus.DONE and t.get("next_action"):
            line += f"\n        next: {t['next_action']}"
        print(line)
    print(f"  {'-'*60}")
    print(f"\n  [A]=auto-triggered   State file: {STATE_FILE}\n")


def read_latest():
    """最新メッセージファイルを読む（旧互換）"""
    if LATEST_MSG_FILE.exists():
        content = LATEST_MSG_FILE.read_text(encoding="utf-8")
        print(content)
        return content
    else:
        print("まだメッセージがありません")
        return ""


# ============================================================
# main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="AI間Slack連携ブリッジ v2.0")
    subparsers = parser.add_subparsers(dest="command")

    # watch
    watch_p = subparsers.add_parser("watch", help="常時監視モード")
    watch_p.add_argument("--interval", type=int, default=5, help="監視間隔(秒)")

    # send-task
    st_p = subparsers.add_parser("send-task", help="タスク送信")
    st_p.add_argument("--task", required=True, help="タスク名")
    st_p.add_argument("--to", required=True, help="送信先ID (cap/cyber)")
    st_p.add_argument("--payload", default=None, help="JSON payload")

    # retry-pending
    subparsers.add_parser("retry-pending", help="未完了タスクのリトライ")

    # status
    subparsers.add_parser("status", help="タスク状態一覧")

    # set-sender
    ss_p = subparsers.add_parser("set-sender", help="送信者IDを設定")
    ss_p.add_argument("sender_id", help="送信者ID (cap/cyber)")

    # state-summary
    subparsers.add_parser("state-summary", help="system_state.json の状態サマリー表示")

    # state-audit
    subparsers.add_parser("state-audit", help="state と Slack ログの整合性チェック")

    # approve
    ap_p = subparsers.add_parser("approve", help="タスクに cap 承認フラグを付与")
    ap_p.add_argument("--task", required=True, help="承認するタスク名 (例: ebay-review)")
    ap_p.add_argument("--by",   required=True, help="承認者ID (例: cap)")

    # ceo-report
    ceo_p = subparsers.add_parser("ceo-report", help="eBay候補をCEO報告")
    ceo_p.add_argument("--file", default=None, help="候補JSONファイルパス（省略時はデフォルト）")

    # rakuten-status (ショートカット)
    subparsers.add_parser("rakuten-status", help="楽天ROOM botのステータスをcyberへ問い合わせ (#ceo-room報告)")

    # daily-check (ショートカット)
    subparsers.add_parser("daily-check", help="全事業定時チェックをcyberへ送信 (#ceo-room報告)")

    # coin-status (ショートカット)
    subparsers.add_parser("coin-status", help="コイン事業ステータスをcyberへ問い合わせ (#ceo-room報告)")

    # daily-handoff (cap-local)
    subparsers.add_parser("daily-handoff", help="日次申し送り生成・保存・#ceo-room投稿 (cap-local)")

    # --- 旧互換コマンド ---
    send_p = subparsers.add_parser("send", help="メッセージ送信（旧互換）")
    send_p.add_argument("message", help="送信するメッセージ")
    send_p.add_argument("--sender", help="送信者ID")

    subparsers.add_parser("receive", help="最新メッセージ取得（旧互換）")
    subparsers.add_parser("read", help="最新メッセージファイルを読む（旧互換）")

    args = parser.parse_args()

    if args.command == "watch":
        watch_channel(interval=args.interval)
    elif args.command == "send-task":
        payload = json.loads(args.payload) if args.payload else None
        cmd_send_task(task=args.task, to=args.to, payload=payload)
    elif args.command == "retry-pending":
        cmd_retry_pending()
    elif args.command == "status":
        cmd_status()
    elif args.command == "set-sender":
        set_sender(args.sender_id)
    elif args.command == "state-summary":
        cmd_state_summary()
    elif args.command == "state-audit":
        cmd_state_audit()
    elif args.command == "approve":
        cmd_approve(task_name=args.task, approved_by=args.by)
    elif args.command == "ceo-report":
        cmd_ceo_report(file_path=args.file)
    elif args.command == "rakuten-status":
        # ショートカット: rakuten-statusをcyberに送信
        cmd_send_task(task="rakuten-status", to="cyber")
    elif args.command == "daily-check":
        # ショートカット: daily-checkをcyberに送信（手動トリガー）
        cmd_send_task(task="daily-check", to="cyber")
    elif args.command == "coin-status":
        # ショートカット: coin-statusをcyberに送信
        cmd_send_task(task="coin-status", to="cyber")
    elif args.command == "daily-handoff":
        # cap-local: 日次申し送り生成
        cmd_daily_handoff()
    elif args.command == "send":
        send_message(args.message, sender=args.sender)
    elif args.command == "receive":
        messages = receive_messages()
        for msg in messages:
            print(f"  {msg['text'][:100]}")
    elif args.command == "read":
        read_latest()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
