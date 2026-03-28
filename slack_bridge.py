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
    "test-ping":   {"requires": None,           "next": None,          "to": "cyber"},
    "report":      {"requires": None,           "next": None,          "to": "cyber"},
    "set-env":     {"requires": None,           "next": None,          "to": "cyber"},
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
    post_msg = None
    if new_matches:
        # Slack message size limit ~4000 chars: slim candidates to essential display fields only
        _KEEP = {"mgmt_no", "db_line1", "db_grader", "db_grade",
                 "ebay_limit_usd", "ebay_limit_jpy", "bid_count", "ebay_url", "is_new"}
        slim_matches = [{k: v for k, v in m.items() if k in _KEEP}
                        for m in new_matches[:10]]  # top 10 fits in <4000 chars
        post_msg = make_task_msg(
            from_id=get_sender(),
            to_id="cap",
            task="ebay-review",
            workflow_id=msg_data.get("workflow_id"),   # 親チェーンのID引き継ぎ
            payload={
                "candidates": slim_matches,
                "count": len(new_matches),       # actual total (may be >15)
                "total_matches": len(matches),
                "searched_at": data.get("searched_at", ""),
            },
        )
        logger.info(f"ebay-review TASK準備完了: 新規{len(new_matches)}件 (全{len(matches)}マッチ) - DONE後送信")

    return {
        "total_searched": data.get("total_searched", 0),
        "match_count": len(matches),
        "new_count": len(new_matches),
        "review_sent": len(new_matches) > 0,
        "_post_send_msg": post_msg,   # dispatch_task._run がDONE後に送信
    }


def handle_ebay_review(msg_data: dict) -> dict:
    """候補リストを受け取り、ファイルに保存 + コンソール表示"""
    payload = msg_data.get("payload", {})
    candidates = payload.get("candidates", [])
    searched_at = payload.get("searched_at", "")

    # ファイル保存
    review_data = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "searched_at": searched_at,
        "count": len(candidates),
        "candidates": candidates,
    }
    outfile = DATA_DIR / "ebay_review_candidates.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    # コンソールに候補一覧表示
    print(f"\n{'='*60}")
    print(f"eBay仕入れ候補（新規 {len(candidates)}件）")
    print(f"{'='*60}")
    for i, c in enumerate(candidates, 1):
        print(f"\n{i}. #{c.get('mgmt_no','')} | {c.get('db_line1','')} {c.get('db_grader','')} {c.get('db_grade','')}")
        print(f"   仕入上限: USD{c.get('ebay_limit_usd',0):,} ({c.get('ebay_limit_jpy',0):,}円)")
        print(f"   入札: {c.get('bid_count',0)}件")
        print(f"   URL: {c.get('ebay_url','')}")
    print(f"\n{'='*60}")
    print(f"保存先: {outfile}")
    print(f"[!] ceo-report を起動するには cap の承認が必要です:")
    print(f"    python slack_bridge.py approve --task ebay-review --by cap")
    print(f"{'='*60}\n")

    return {"saved_to": str(outfile), "count": len(candidates),
            "review_status": "pending",
            "note": "cap approval required before ceo-report auto-trigger"}


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


HANDLERS = {
    "test-ping":   handle_test_ping,
    "ebay-search": handle_ebay_search,
    "ebay-review": handle_ebay_review,
    "git-pull":    handle_git_pull,
    "report":      handle_report,
    "set-env":     handle_set_env,
    "ceo-report":  handle_ceo_report,
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
