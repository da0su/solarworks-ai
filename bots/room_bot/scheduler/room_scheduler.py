"""ROOM BOT Scheduler - 時刻ベース自動実行エンジン

daily_plan.json を読み込み、毎分チェックして一致タスクを実行する。
slack_room_bot.py のスレッドとして統合される。

安全設計:
  - CommandGuard で Slack手動コマンドとの排他制御
  - 失敗してもscheduler全体は止まらない
  - 日付変更時に翌日plan を自動再生成
  - schedule_log.json に全実行結果を記録
  - Slack に結果通知
"""

import json
import logging
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.plan_generator import (
    generate_daily_plan,
    load_plan,
    format_plan,
    SCHEDULER_DATA_DIR,
)

logger = logging.getLogger("slack_room_bot")

# 実行済みタスクの追跡用キー
_EXECUTED_KEY_FORMAT = "{date}_{time}_{action}"

# ロック取得リトライ
LOCK_RETRY_INTERVAL = 180  # 3分
LOCK_MAX_RETRIES = 3

# executor タイムアウト（分）
EXECUTOR_TIMEOUT_MIN = 30


class RoomScheduler:
    """daily_plan.json に基づく自動実行スケジューラ"""

    def __init__(self, guard, slack_say_fn=None):
        """
        Args:
            guard: CommandGuard インスタンス（排他制御共有）
            slack_say_fn: Slack通知用関数 (channel, text) -> None
        """
        self._guard = guard
        self._slack_say = slack_say_fn
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_date: str = ""
        self._executed_tasks: set[str] = set()
        self._plan: dict | None = None

    def start(self):
        """スケジューラスレッドを開始する"""
        if self._thread and self._thread.is_alive():
            logger.warning("[scheduler] 既に起動中")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="room-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("[scheduler] スレッド起動")

    def stop(self):
        """スケジューラスレッドを停止する"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[scheduler] スレッド停止")

    def _run_loop(self):
        """メインループ: 毎分チェック"""
        logger.info("[scheduler] ループ開始")

        # 起動時に plan をロードまたは生成
        self._ensure_plan()

        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error(f"[scheduler] tick エラー: {e}")
                logger.error(traceback.format_exc())

            # 60秒待機（1秒刻みで stop チェック）
            for _ in range(60):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("[scheduler] ループ終了")

    def _tick(self):
        """毎分実行: 日付チェック + タスク実行"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        # ── 日付変更検知 ──
        if today != self._current_date:
            logger.info(f"[scheduler] 日付変更検知: {self._current_date} → {today}")

            # 前日のデイリーサマリーを送信
            if self._current_date and self._plan:
                self._send_daily_summary()

            # 新しい日のplanを生成
            self._current_date = today
            self._executed_tasks.clear()
            self._ensure_plan()

        if not self._plan:
            return

        # off日チェック
        if self._plan.get("day_type") == "off":
            return

        # ── 23:xx 前倒し禁止ガード ──
        # plan の日付と現在日付を比較
        plan_date = self._plan.get("date", "")
        if plan_date != today:
            return

        # ── タスクチェック ──
        for task in self._plan.get("tasks", []):
            if not task.get("enabled", True):
                continue

            task_time = task.get("time", "")
            task_action = task.get("action", "")
            task_count = task.get("count", 0)

            if not task_time or not task_action or task_count <= 0:
                continue

            # 実行済みチェック
            exec_key = _EXECUTED_KEY_FORMAT.format(
                date=today, time=task_time, action=task_action,
            )
            if exec_key in self._executed_tasks:
                continue

            # 時刻一致チェック
            if current_time != task_time:
                continue

            # ── 実行 ──
            logger.info(
                f"[scheduler] タスク実行開始: {task_time} {task_action} x{task_count}"
            )
            self._execute_task(task, exec_key)

    def _ensure_plan(self):
        """当日のplanがなければ生成する"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._current_date = today

        # 既存planを読み込み
        self._plan = load_plan(target_date=today)
        if self._plan:
            logger.info(f"[scheduler] 既存plan読み込み: {today}")
            self._notify(f"[scheduler] Daily Plan 読み込み完了\n{format_plan(self._plan)}")
            return

        # 生成
        logger.info(f"[scheduler] plan生成開始: {today}")
        try:
            self._plan = generate_daily_plan(target_date=today)
            msg = f"[scheduler] Daily Plan 自動生成完了\n{format_plan(self._plan)}"
            logger.info(msg)
            self._notify(msg)
        except Exception as e:
            logger.error(f"[scheduler] plan生成失敗: {e}")
            self._notify(f"[scheduler] Daily Plan 生成失敗: {e}")
            self._plan = None

    def _execute_task(self, task: dict, exec_key: str):
        """タスクを実行する（排他制御付き）"""
        action = task["action"]
        count = task["count"]
        task_time = task["time"]

        # アクション種別マッピング（CommandGuard と同じキー）
        action_type_map = {
            "like": "plus_like",
            "follow": "plus_follow",
            "post": "plus_post",
        }
        guard_action = action_type_map.get(action, action)

        # ── 排他ロック取得（リトライ付き） ──
        acquired = False
        for retry in range(LOCK_MAX_RETRIES):
            if self._guard.try_acquire_action(guard_action, f"scheduler:{action}:{count}"):
                acquired = True
                break

            running = self._guard.get_running_action(guard_action)
            logger.info(
                f"[scheduler] ロック取得失敗 ({action}), "
                f"実行中: {running}, リトライ {retry + 1}/{LOCK_MAX_RETRIES}"
            )

            if retry < LOCK_MAX_RETRIES - 1:
                # リトライ待機（stopイベントチェック付き）
                for _ in range(LOCK_RETRY_INTERVAL):
                    if self._stop_event.is_set():
                        return
                    time.sleep(1)

        if not acquired:
            logger.warning(f"[scheduler] ロック取得断念: {action} x{count}")
            self._notify(
                f"[scheduler] {task_time} {action} x{count} スキップ\n"
                f"  理由: 他の実行中タスクとの競合（{LOCK_MAX_RETRIES}回リトライ失敗）"
            )
            self._executed_tasks.add(exec_key)
            self._log_result(task, "skipped", "lock_conflict", 0)
            return

        # ── executor 実行 ──
        start_time = time.time()
        result = {}
        status = "success"
        error_msg = ""

        try:
            self._notify(f"[scheduler] {task_time} {action} x{count} 実行開始...")

            if action == "like":
                result = self._exec_like(count)
            elif action == "follow":
                result = self._exec_follow(count)
            elif action == "post":
                result = self._exec_post(count)
            else:
                logger.warning(f"[scheduler] 未知のaction: {action}")
                status = "error"
                error_msg = f"unknown action: {action}"

        except Exception as e:
            logger.error(f"[scheduler] 実行エラー: {action} - {e}")
            logger.error(traceback.format_exc())
            status = "error"
            error_msg = str(e)

        finally:
            self._guard.release_action(guard_action)

        elapsed = time.time() - start_time
        elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"

        # 実行済みマーク
        self._executed_tasks.add(exec_key)

        # 結果ログ
        completed = result.get("completed", 0)
        failed = result.get("failed", 0)

        self._log_result(task, status, error_msg, completed, failed, elapsed)

        # Slack通知
        if status == "success":
            self._notify(
                f"[scheduler] {task_time} {action} 完了\n"
                f"  結果: {completed}/{count}件成功"
                f"{'  (' + str(failed) + '件失敗)' if failed else ''}\n"
                f"  所要時間: {elapsed_str}"
            )
        else:
            self._notify(
                f"[scheduler] {task_time} {action} エラー\n"
                f"  {error_msg[:200]}\n"
                f"  所要時間: {elapsed_str}"
            )

    # ================================================================
    # Executor 呼び出し
    # ================================================================

    def _exec_like(self, count: int) -> dict:
        from executor.like_executor import LikeExecutor
        executor = LikeExecutor(limit=count, source="scheduler")
        summary = executor.run()
        return {
            "completed": summary.get("liked", 0),
            "failed": summary.get("failed", 0),
        }

    def _exec_follow(self, count: int) -> dict:
        from executor.follow_executor import FollowExecutor
        executor = FollowExecutor(limit=count, source="scheduler")
        summary = executor.run()
        return {
            "completed": summary.get("followed", 0),
            "failed": summary.get("failed", 0),
        }

    def _exec_post(self, count: int) -> dict:
        from planner.queue_executor import QueueExecutor
        from planner.queue_manager import QueueManager

        today = datetime.now().strftime("%Y-%m-%d")

        # キュー残チェック
        try:
            qm = QueueManager()
            pending = qm.get_pending(today)
            qm.close()
        except Exception as e:
            logger.error(f"[scheduler] キュー確認エラー: {e}")
            self._notify(f"[scheduler] post キュー確認エラー: {e}")
            return {"completed": 0, "failed": 0}

        if not pending:
            logger.info("[scheduler] post キュー空 → スキップ")
            self._notify(
                f"[scheduler] post x{count} スキップ: キューが空です\n"
                f"  → python run.py plan でキュー生成が必要です"
            )
            return {"completed": 0, "failed": 0}

        actual = min(count, len(pending))
        if actual < count:
            logger.info(f"[scheduler] キュー残{len(pending)}件 → {actual}件で実行")

        executor = QueueExecutor(queue_date=today, limit=actual)
        summary = executor.run()
        return {
            "completed": summary.get("posted", 0),
            "failed": summary.get("failed", 0) + summary.get("skipped", 0),
        }

    # ================================================================
    # ログ・通知
    # ================================================================

    def _log_result(self, task: dict, status: str, error: str,
                    completed: int, failed: int = 0, elapsed: float = 0):
        """schedule_log.json に結果を追記する"""
        log_path = SCHEDULER_DATA_DIR / "schedule_log.json"

        log_entries = []
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    log_entries = json.load(f)
            except (json.JSONDecodeError, Exception):
                log_entries = []

        log_entries.append({
            "date": self._current_date,
            "time": task.get("time", ""),
            "action": task.get("action", ""),
            "count_planned": task.get("count", 0),
            "count_completed": completed,
            "count_failed": failed,
            "status": status,
            "error": error,
            "elapsed_seconds": round(elapsed, 1),
            "executed_at": datetime.now().isoformat(),
        })

        # 直近7日分のみ保持
        cutoff = datetime.now().strftime("%Y-%m-%d")
        # 7日前
        from datetime import timedelta
        cutoff_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        log_entries = [
            e for e in log_entries
            if e.get("date", "") >= cutoff_date
        ]

        SCHEDULER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_entries, f, ensure_ascii=False, indent=2)

    def _send_daily_summary(self):
        """前日のデイリーサマリーをSlackに送信する"""
        if not self._plan:
            return

        targets = self._plan.get("targets", {})
        date = self._plan.get("date", "?")

        # schedule_log.json から当日分を集計
        log_path = SCHEDULER_DATA_DIR / "schedule_log.json"
        day_results = {"post": 0, "follow": 0, "like": 0}

        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    logs = json.load(f)
                for entry in logs:
                    if entry.get("date") == date:
                        action = entry.get("action", "")
                        if action in day_results:
                            day_results[action] += entry.get("count_completed", 0)
            except Exception:
                pass

        lines = [f"[scheduler] デイリーサマリー {date}"]
        for action in ("post", "follow", "like"):
            target = targets.get(action, 0)
            actual = day_results[action]
            mark = "OK" if actual >= target * 0.8 else "NG"
            lines.append(f"  {action:6s}: {actual:3d}/{target:3d}件 {mark}")

        self._notify("\n".join(lines))

    def _notify(self, text: str):
        """Slack通知（関数が設定されている場合）"""
        logger.info(text)
        if self._slack_say:
            try:
                self._slack_say(text)
            except Exception as e:
                logger.error(f"[scheduler] Slack通知エラー: {e}")

    # ================================================================
    # 外部API
    # ================================================================

    def get_status(self) -> str:
        """スケジューラの状態を返す"""
        if not self._plan:
            return "[scheduler] plan未読み込み"

        today = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M")

        tasks = self._plan.get("tasks", [])
        total = len(tasks)
        done = len(self._executed_tasks)

        # 次の未実行タスク
        next_task = None
        for t in tasks:
            if not t.get("enabled", True):
                continue
            exec_key = _EXECUTED_KEY_FORMAT.format(
                date=today, time=t["time"], action=t["action"],
            )
            if exec_key not in self._executed_tasks and t["time"] >= now_time:
                next_task = t
                break

        lines = [
            f"[scheduler] {today} (Day{self._plan.get('day_num', '?')})",
            f"  進捗: {done}/{total} タスク完了",
        ]
        if next_task:
            lines.append(
                f"  次回: {next_task['time']} {next_task['action']} x{next_task['count']}"
            )
        else:
            lines.append("  次回: (本日のタスクは全て完了)")

        return "\n".join(lines)

    def reload_plan(self) -> str:
        """planを再読み込みする（CLI/Slack用）"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._plan = load_plan(target_date=today)
        if self._plan:
            return f"[scheduler] plan再読込完了\n{format_plan(self._plan)}"
        else:
            return "[scheduler] 当日のplanが見つかりません"

    def regenerate_plan(self) -> str:
        """planを再生成する（CLI/Slack用）"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._plan = generate_daily_plan(target_date=today)
        self._executed_tasks.clear()
        return f"[scheduler] plan再生成完了\n{format_plan(self._plan)}"
