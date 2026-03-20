"""ROOM BOT v6.0 - CEOコマンド制御

CEOがiPhoneから簡単なコマンドでROOM運用を管理する。
全チャネル（CLI / Slack / スマホ）がこのクラスを呼ぶ設計。

コマンド:
  room on                    # 定常運用ON
  room off                   # 定常運用OFF（room plusは実行可能）
  room status                # 状態確認
  room plus post N            # 臨時投稿N件
  room plus like N            # 臨時いいねN件
  room plus follow N          # 臨時フォローN件
  room generate-month         # 月間スケジュール生成
"""

import json
import random
import sys
from calendar import monthrange
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()


class RoomController:
    """ROOM BOT 運用制御クラス"""

    # ================================================================
    # ON / OFF 制御
    # ================================================================

    @staticmethod
    def set_enabled(enabled: bool, changed_by: str = "cli") -> dict:
        """運用状態を変更する"""
        state = {
            "enabled": enabled,
            "changed_at": datetime.now().isoformat(),
            "changed_by": changed_by,
        }
        config.ROOM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.ROOM_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        label = "ON" if enabled else "OFF"
        logger.info(f"ROOM運用状態: {label} (by {changed_by})")
        return state

    @staticmethod
    def is_enabled() -> bool:
        """運用がONかどうか（ファイルなし→False = 安全側）"""
        if not config.ROOM_STATE_PATH.exists():
            return False
        try:
            with open(config.ROOM_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            return state.get("enabled", False)
        except Exception:
            return False

    @staticmethod
    def get_state() -> dict:
        """運用状態を返す"""
        if not config.ROOM_STATE_PATH.exists():
            return {"enabled": False, "changed_at": None, "changed_by": None}
        try:
            with open(config.ROOM_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"enabled": False, "changed_at": None, "changed_by": None}

    # ================================================================
    # STATUS 表示
    # ================================================================

    @classmethod
    def get_status(cls) -> str:
        """全情報を含むstatus文字列を返す"""
        state = cls.get_state()
        today_str = datetime.now().strftime("%Y-%m-%d")

        # 運用状態
        if state["enabled"]:
            changed = state.get("changed_at", "")[:16].replace("T", " ")
            header = f"ROOM運用状態: ON ({changed} から稼働中)"
        else:
            header = "ROOM運用状態: OFF"

        # daily_plan からday_type取得
        plan = cls._load_daily_plan()
        if plan and plan.get("date") == today_str:
            day_type = plan.get("day_type", "normal")
            post_target = plan.get("post", {}).get("total", 0)
            like_target = plan.get("like", {}).get("total", 0)
            follow_target = plan.get("follow", {}).get("total", 0)
        else:
            day_type = "?"
            post_target = 0
            like_target = 0
            follow_target = 0

        # 今日の実績を取得
        post_normal, post_plus = cls._count_today_posts(today_str)
        like_normal, like_plus = cls._count_today_likes(today_str)
        follow_normal, follow_plus = cls._count_today_follows(today_str)

        # 次回予定
        next_schedule = cls._get_next_schedule(plan, today_str)

        # 月間集計
        month_summary = cls._get_month_summary(today_str)

        # 臨時業務ログ
        plus_log = cls._get_today_plus_log(today_str)

        lines = [
            f"\n{'=' * 60}",
            header,
            f"{'=' * 60}",
            f"日付: {today_str} ({day_type} day)",
            f"",
            f"  [投稿]     {post_normal + post_plus:3d} / {post_target}件"
            f"  (通常: {post_normal}, 臨時: {post_plus})",
            f"  [いいね]    {like_normal + like_plus:3d} / {like_target}件"
            f"  (通常: {like_normal}, 臨時: {like_plus})",
            f"  [フォロー]  {follow_normal + follow_plus:3d} / {follow_target}件"
            f"  (通常: {follow_normal}, 臨時: {follow_plus})",
        ]

        # 臨時業務
        if plus_log:
            lines.append(f"")
            lines.append(f"  臨時業務:")
            for entry in plus_log:
                action = entry.get("action", "?")
                req = entry.get("requested", 0)
                comp = entry.get("completed", 0)
                lines.append(f"    {action}: {comp}/{req}件")
        else:
            lines.append(f"")
            lines.append(f"  臨時業務: (なし)")

        # 次回予定
        if next_schedule:
            lines.append(f"")
            lines.append(f"  次回予定:")
            for ns in next_schedule:
                lines.append(f"    {ns}")

        # 月間集計
        lines.append(f"")
        lines.append(f"  --- 今月集計 ({month_summary['month']}) ---")
        lines.append(
            f"  投稿: {month_summary['total_posts']}件  "
            f"いいね: {month_summary['total_likes']}件  "
            f"フォロー: {month_summary['total_follows']}件"
        )

        # 休み情報
        monthly_sched = cls._load_monthly_schedule()
        if monthly_sched:
            off_days = monthly_sched.get("off_days", [])
            light_days = monthly_sched.get("light_days", [])
            if off_days:
                off_short = [d.split("-")[2].lstrip("0") for d in off_days]
                lines.append(f"  off日: {', '.join(off_short)}日 ({len(off_days)}日)")
            if light_days:
                light_short = [d.split("-")[2].lstrip("0") for d in light_days]
                lines.append(f"  light日: {', '.join(light_short)}日 ({len(light_days)}日)")

        lines.append(f"{'=' * 60}")
        return "\n".join(lines)

    # ================================================================
    # 月間スケジュール生成
    # ================================================================

    @classmethod
    def generate_monthly_schedule(cls, target_month: str = None) -> dict:
        """月間スケジュール（off/light/normal日）を生成する

        Args:
            target_month: "YYYY-MM" 形式。省略時は今月。

        Returns:
            月間スケジュール dict
        """
        if target_month is None:
            target_month = datetime.now().strftime("%Y-%m")

        year, month = map(int, target_month.split("-"))
        _, num_days = monthrange(year, month)

        logger.info(f"=== 月間スケジュール生成: {target_month} ({num_days}日) ===")

        # off日数・light日数を決定
        num_off = random.randint(
            config.MONTHLY_OFF_DAYS_MIN, config.MONTHLY_OFF_DAYS_MAX
        )
        num_light = random.randint(
            config.MONTHLY_LIGHT_DAYS_MIN, config.MONTHLY_LIGHT_DAYS_MAX
        )

        all_days = list(range(1, num_days + 1))

        # off日を前半/後半に分散配置
        half = num_days // 2
        first_half = list(range(1, half + 1))
        second_half = list(range(half + 1, num_days + 1))

        # 前半から ceil(num_off/2)、後半から残りを選択
        off_first = min(len(first_half), (num_off + 1) // 2)
        off_second = num_off - off_first

        random.shuffle(first_half)
        random.shuffle(second_half)

        off_candidates = first_half[:off_first] + second_half[:off_second]

        # 連続off制限（最大2日連続）
        off_days = cls._apply_consecutive_limit(
            sorted(off_candidates), num_days, config.MAX_CONSECUTIVE_OFF
        )

        # light日（off日と被らないように）
        remaining = [d for d in all_days if d not in off_days]
        random.shuffle(remaining)
        light_days = sorted(remaining[:num_light])

        # days dict を構築
        days = {}
        off_dates = []
        light_dates = []

        for d in range(1, num_days + 1):
            date_str = f"{year:04d}-{month:02d}-{d:02d}"
            if d in off_days:
                days[date_str] = "off"
                off_dates.append(date_str)
            elif d in light_days:
                days[date_str] = "light"
                light_dates.append(date_str)
            else:
                days[date_str] = "normal"

        schedule = {
            "month": target_month,
            "generated_at": datetime.now().isoformat(),
            "days": days,
            "off_days": off_dates,
            "light_days": light_dates,
        }

        # 保存
        config.MONTHLY_SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.MONTHLY_SCHEDULE_PATH, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)

        logger.info(
            f"  off日: {len(off_dates)}日 {off_dates}"
        )
        logger.info(
            f"  light日: {len(light_dates)}日 {light_dates}"
        )
        logger.info(f"  保存先: {config.MONTHLY_SCHEDULE_PATH}")

        return schedule

    @staticmethod
    def _apply_consecutive_limit(
        days: list[int], num_days: int, max_consecutive: int
    ) -> list[int]:
        """連続off日が上限を超えないように調整する"""
        result = []
        for d in days:
            # 追加した場合の連続数をチェック
            consecutive = 1
            for prev in result:
                if d - prev <= consecutive:
                    consecutive += 1
                else:
                    break

            if consecutive <= max_consecutive:
                result.append(d)
            # else: このoff日をスキップ（normalに戻す）

        return result

    # ================================================================
    # ROOM PLUS（臨時業務）
    # ================================================================

    @classmethod
    def exec_plus(
        cls, action: str, count: int, tone: str = "pickup", force: bool = False
    ) -> dict:
        """臨時業務を実行する

        Args:
            action: "post" | "like" | "follow"
            count: 件数
            tone: 投稿トーン（"normal" | "pickup"）postのみ
            force: OFF中でも強制実行するか

        Returns:
            実行結果 dict
        """
        # 安全上限チェック
        max_limits = {
            "post": config.ROOM_PLUS_MAX_POST,
            "like": config.ROOM_PLUS_MAX_LIKE,
            "follow": config.ROOM_PLUS_MAX_FOLLOW,
        }
        max_limit = max_limits.get(action, 30)
        if count > max_limit:
            print(f"安全上限超過: {action}は1回最大{max_limit}件です。{count}→{max_limit}に制限します。")
            logger.warning(f"room plus 安全上限: {action} {count}→{max_limit}")
            count = max_limit

        # OFF時チェック（room plusはOFF中でも実行可能: CEO明示コマンドのため）
        # ただしログには記録
        if not cls.is_enabled() and not force:
            logger.info(f"room plus {action} {count}: 運用OFF中だがCEO明示コマンドのため実行")

        # ログエントリ作成
        log_entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "action": action,
            "requested": count,
            "completed": 0,
            "failed": 0,
            "tone": tone if action == "post" else None,
            "source": "room_plus",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }

        result = {}
        try:
            if action == "like":
                result = cls._exec_plus_like(count)
            elif action == "follow":
                result = cls._exec_plus_follow(count)
            elif action == "post":
                result = cls._exec_plus_post(count, tone)
            else:
                print(f"未知のアクション: {action}")
                return log_entry

            log_entry["completed"] = result.get("completed", 0)
            log_entry["failed"] = result.get("failed", 0)

        except Exception as e:
            logger.error(f"room plus {action} エラー: {e}")
            log_entry["failed"] = count

        log_entry["finished_at"] = datetime.now().isoformat()

        # ログ保存
        cls._save_plus_log(log_entry)

        return log_entry

    @staticmethod
    def _exec_plus_like(count: int) -> dict:
        """臨時いいね実行"""
        from executor.like_executor import LikeExecutor

        executor = LikeExecutor(limit=count, source="room_plus")
        summary = executor.run()
        return {
            "completed": summary.get("liked", 0),
            "failed": summary.get("failed", 0),
        }

    @staticmethod
    def _exec_plus_follow(count: int) -> dict:
        """臨時フォロー実行"""
        from executor.follow_executor import FollowExecutor

        executor = FollowExecutor(limit=count, source="room_plus")
        summary = executor.run()
        return {
            "completed": summary.get("followed", 0),
            "failed": summary.get("failed", 0),
        }

    @staticmethod
    def _exec_plus_post(count: int, tone: str) -> dict:
        """臨時投稿実行 - 既存キューからlimit件を実行する

        既存のQueueExecutorを再利用し、キューに残っているqueued状態の
        アイテムをcount件まで投稿する。tone="pickup"の場合はコメント
        を軽いトーンで自動生成する。

        前提: python run.py plan で事前にキューが生成されていること
        """
        from planner.queue_executor import QueueExecutor
        from planner.queue_manager import QueueManager

        today_str = datetime.now().strftime("%Y-%m-%d")

        # キューに残りがあるか確認
        try:
            qm = QueueManager()
            pending = qm.get_pending(today_str)
            qm.close()
        except Exception as e:
            logger.error(f"キュー確認エラー: {e}")
            print(f"キュー確認エラー: {e}")
            return {"completed": 0, "failed": 0}

        if not pending:
            print(f"キューに未実行アイテムがありません。")
            print(f"  → 先に python run.py plan でキューを生成してください。")
            logger.info(f"room plus post: キュー空 (count={count}, tone={tone})")
            return {"completed": 0, "failed": 0}

        available = len(pending)
        actual_count = min(count, available)
        if actual_count < count:
            print(f"キュー残り{available}件のため、{actual_count}件を実行します。")

        logger.info(f"room plus post: {actual_count}件実行開始 (tone={tone})")
        print(f"臨時投稿: {actual_count}件を {tone} トーンで実行します...")

        # QueueExecutor で実行（tone付き + 高速間隔）
        executor = QueueExecutor(
            queue_date=today_str,
            limit=actual_count,
            min_wait=config.ROOM_PLUS_POST_INTERVAL_MIN,
            max_wait=config.ROOM_PLUS_POST_INTERVAL_MAX,
            tone=tone,
        )
        summary = executor.run()

        return {
            "completed": summary.get("posted", 0),
            "failed": summary.get("failed", 0) + summary.get("skipped", 0),
        }

    @staticmethod
    def _save_plus_log(entry: dict) -> None:
        """臨時業務ログを保存する"""
        history = []
        if config.ROOM_PLUS_LOG_PATH.exists():
            try:
                with open(config.ROOM_PLUS_LOG_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append(entry)

        config.ROOM_PLUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.ROOM_PLUS_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    # ================================================================
    # 内部ヘルパー
    # ================================================================

    @staticmethod
    def _load_daily_plan() -> dict | None:
        """daily_plan.json を読み込む"""
        plan_path = config.DATA_DIR / "daily_plan.json"
        if not plan_path.exists():
            return None
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _load_monthly_schedule() -> dict | None:
        """monthly_schedule.json を読み込む"""
        if not config.MONTHLY_SCHEDULE_PATH.exists():
            return None
        try:
            with open(config.MONTHLY_SCHEDULE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _count_today_posts(today_str: str) -> tuple[int, int]:
        """今日の投稿件数を (通常, 臨時) で返す"""
        normal = 0
        plus = 0

        # SQLiteから通常投稿を取得
        try:
            from planner.queue_manager import QueueManager
            qm = QueueManager()
            items = qm.get_by_date(today_str)
            normal = sum(1 for item in items if item.get("status") == "posted")
            qm.close()
        except Exception:
            pass

        # room_plus_logから臨時投稿を取得
        try:
            if config.ROOM_PLUS_LOG_PATH.exists():
                with open(config.ROOM_PLUS_LOG_PATH, "r", encoding="utf-8") as f:
                    log = json.load(f)
                for entry in log:
                    if entry.get("date") == today_str and entry.get("action") == "post":
                        plus += entry.get("completed", 0)
        except Exception:
            pass

        return normal, plus

    @staticmethod
    def _count_today_likes(today_str: str) -> tuple[int, int]:
        """今日のいいね件数を (通常, 臨時) で返す"""
        normal = 0
        plus = 0

        if config.LIKE_HISTORY_PATH.exists():
            try:
                with open(config.LIKE_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    liked_at = entry.get("liked_at", "")
                    if liked_at.startswith(today_str):
                        source = entry.get("source", "daily_plan")
                        if source == "room_plus":
                            plus += 1
                        else:
                            normal += 1
            except Exception:
                pass

        return normal, plus

    @staticmethod
    def _count_today_follows(today_str: str) -> tuple[int, int]:
        """今日のフォロー件数を (通常, 臨時) で返す"""
        normal = 0
        plus = 0

        follow_path = config.DATA_DIR / "follow_history.json"
        if follow_path.exists():
            try:
                with open(follow_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    followed_at = entry.get("followed_at", "")
                    if followed_at.startswith(today_str):
                        source = entry.get("source", "daily_plan")
                        if source == "room_plus":
                            plus += 1
                        else:
                            normal += 1
            except Exception:
                pass

        return normal, plus

    @staticmethod
    def _get_next_schedule(plan: dict | None, today_str: str) -> list[str]:
        """次回予定を文字列リストで返す"""
        if not plan or plan.get("date") != today_str:
            return []

        now = datetime.now()
        results = []

        # 投稿バッチ
        for batch in plan.get("post", {}).get("batches", []):
            if batch.get("status") == "pending":
                results.append(f"投稿 {batch['id']}: {batch['start']} 開始予定 ({batch['count']}件)")

        return results

    @staticmethod
    def _get_today_plus_log(today_str: str) -> list[dict]:
        """今日の臨時業務ログを返す"""
        if not config.ROOM_PLUS_LOG_PATH.exists():
            return []
        try:
            with open(config.ROOM_PLUS_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
            return [e for e in log if e.get("date") == today_str]
        except Exception:
            return []

    @staticmethod
    def _get_month_summary(today_str: str) -> dict:
        """今月の累計を返す"""
        month_prefix = today_str[:7]  # "2026-03"
        total_posts = 0
        total_likes = 0
        total_follows = 0

        # 投稿: SQLiteから
        try:
            from planner.queue_manager import QueueManager
            qm = QueueManager()
            # 簡易: 今月分のpostedを集計
            import sqlite3
            cursor = qm.conn.execute(
                "SELECT COUNT(*) FROM post_queue WHERE status='posted' AND queue_date LIKE ?",
                (f"{month_prefix}%",)
            )
            total_posts = cursor.fetchone()[0]
            qm.close()
        except Exception:
            pass

        # 臨時投稿分も加算
        try:
            if config.ROOM_PLUS_LOG_PATH.exists():
                with open(config.ROOM_PLUS_LOG_PATH, "r", encoding="utf-8") as f:
                    log = json.load(f)
                for entry in log:
                    if entry.get("date", "").startswith(month_prefix):
                        if entry.get("action") == "post":
                            total_posts += entry.get("completed", 0)
                        elif entry.get("action") == "like":
                            total_likes += entry.get("completed", 0)
                        elif entry.get("action") == "follow":
                            total_follows += entry.get("completed", 0)
        except Exception:
            pass

        # いいね: like_history.json
        if config.LIKE_HISTORY_PATH.exists():
            try:
                with open(config.LIKE_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    if entry.get("liked_at", "").startswith(month_prefix):
                        total_likes += 1
            except Exception:
                pass

        # フォロー: follow_history.json
        follow_path = config.DATA_DIR / "follow_history.json"
        if follow_path.exists():
            try:
                with open(follow_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    if entry.get("followed_at", "").startswith(month_prefix):
                        total_follows += 1
            except Exception:
                pass

        return {
            "month": month_prefix,
            "total_posts": total_posts,
            "total_likes": total_likes,
            "total_follows": total_follows,
        }

    # ================================================================
    # フォーマット（月間スケジュール表示用）
    # ================================================================

    @classmethod
    def format_monthly_schedule(cls, schedule: dict) -> str:
        """月間スケジュールを表示用に整形"""
        lines = [
            f"\n{'=' * 60}",
            f"月間スケジュール: {schedule['month']}",
            f"生成日時: {schedule['generated_at'][:19]}",
            f"{'=' * 60}",
        ]

        off_days = schedule.get("off_days", [])
        light_days = schedule.get("light_days", [])

        lines.append(f"  off日:   {len(off_days)}日  {off_days}")
        lines.append(f"  light日: {len(light_days)}日  {light_days}")
        lines.append(f"  normal日: {len(schedule.get('days', {})) - len(off_days) - len(light_days)}日")
        lines.append(f"")

        # カレンダー形式で表示
        days = schedule.get("days", {})
        for date_str, day_type in sorted(days.items()):
            icon = {"off": "[休]", "light": "[軽]", "normal": "    "}.get(day_type, "    ")
            # 曜日を取得
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            if day_type != "normal":
                lines.append(f"  {date_str} ({weekday}) {icon}")

        lines.append(f"{'=' * 60}")
        return "\n".join(lines)
