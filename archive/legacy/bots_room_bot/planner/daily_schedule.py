"""ROOM BOT v6.0 - 日次スケジュール生成

毎日23:50に翌日の投稿スケジュール（3バッチの開始時刻・件数・間隔）を
ランダムに決定し、daily_plan.json に保存する。

v6.0: day_type (normal/light/off) 対応 + like/follow件数統合
"""

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()

DAILY_PLAN_PATH = config.DATA_DIR / "daily_plan.json"


def get_day_type(target_date: str) -> str:
    """monthly_schedule.json から当日のday_typeを取得する

    Args:
        target_date: "YYYY-MM-DD"

    Returns:
        "normal" | "light" | "off"
    """
    if not config.MONTHLY_SCHEDULE_PATH.exists():
        return "normal"  # 月間スケジュール未生成時は安全デフォルト

    try:
        with open(config.MONTHLY_SCHEDULE_PATH, "r", encoding="utf-8") as f:
            schedule = json.load(f)

        # 月が一致するか確認
        month_prefix = target_date[:7]
        if schedule.get("month") != month_prefix:
            logger.info(f"月間スケジュール不一致: {schedule.get('month')} != {month_prefix} → normal")
            return "normal"

        return schedule.get("days", {}).get(target_date, "normal")
    except Exception as e:
        logger.warning(f"月間スケジュール読み込みエラー: {e} → normal")
        return "normal"


def generate_daily_schedule(target_date: str = None) -> dict:
    """翌日（または指定日）の投稿スケジュールを生成する

    Args:
        target_date: 対象日 (YYYY-MM-DD)。省略時は翌日。

    Returns:
        dict: 日次プラン
    """
    if target_date is None:
        # 23:50に呼ばれる想定 → 翌日分を生成
        tomorrow = datetime.now() + timedelta(days=1)
        target_date = tomorrow.strftime("%Y-%m-%d")

    logger.info(f"=== 日次スケジュール生成: {target_date} ===")

    # 1. day_type を決定
    day_type = get_day_type(target_date)
    logger.info(f"  day_type: {day_type}")

    # 2. day_typeに応じた目標件数を決定
    targets = config.get_day_type_targets(day_type)
    total_post = targets["post"]
    total_like = targets["like"]
    total_follow = targets["follow"]

    logger.info(f"  目標: 投稿{total_post}, いいね{total_like}, フォロー{total_follow}")

    # 3. off日は空プランを返す
    if day_type == "off":
        plan = {
            "date": target_date,
            "generated_at": datetime.now().isoformat(),
            "day_type": "off",
            "post": {"total": 0, "daily_jitter_min": 0, "batches": []},
            "like": {"total": 0},
            "follow": {"total": 0},
        }
        _save_plan(plan)
        logger.info("  off日: 全件0のプランを生成")
        return plan

    # 4. 日次ジッター（全バッチに共通で適用される追加揺らぎ）
    daily_jitter = random.randint(config.DAILY_JITTER_MIN, config.DAILY_JITTER_MAX)
    jitter_direction = random.choice([-1, 1])
    effective_jitter = daily_jitter * jitter_direction
    logger.info(f"  日次ジッター: {effective_jitter:+d}分")

    # 5. 投稿バッチを生成
    batches = _generate_post_batches(total_post, effective_jitter, day_type)

    # 6. プラン構築
    plan = {
        "date": target_date,
        "generated_at": datetime.now().isoformat(),
        "day_type": day_type,
        "post": {
            "total": total_post,
            "daily_jitter_min": effective_jitter,
            "batches": batches,
        },
        "like": {
            "total": total_like,
        },
        "follow": {
            "total": total_follow,
        },
    }

    _save_plan(plan)
    logger.info(f"=== 日次スケジュール生成完了 ===")

    return plan


def _generate_post_batches(total: int, effective_jitter: int, day_type: str) -> list:
    """投稿バッチを生成する"""
    if total == 0:
        return []

    batches = []
    remaining = total

    # light日はバッチ構成を簡略化（1-2バッチ）
    if day_type == "light":
        batch_configs = ["lunch", "evening"]
    else:
        batch_configs = ["night", "lunch", "evening"]

    for batch_name in batch_configs:
        batch_cfg = config.POST_BATCHES[batch_name]

        # 件数決定
        if batch_cfg["count_min"] is not None:
            if day_type == "light":
                # light日は小さめの件数に
                count = min(remaining, random.randint(10, 20))
            else:
                count = random.randint(batch_cfg["count_min"], batch_cfg["count_max"])
                count = min(count, remaining)
        else:
            # evening: 残り全件
            count = remaining

        if count <= 0:
            continue

        remaining -= count

        # 開始時刻決定
        base_hour = batch_cfg["start_hour"]
        minute_min = batch_cfg["start_minute_min"]
        minute_max = batch_cfg["start_minute_max"]

        random_minutes = random.randint(minute_min, minute_max)
        total_minutes = random_minutes + effective_jitter

        start_hour = base_hour + (total_minutes // 60)
        start_minute = total_minutes % 60

        start_hour = max(0, min(23, start_hour))
        start_minute = max(0, min(59, start_minute))
        start_time = f"{start_hour:02d}:{start_minute:02d}"

        # 間隔のランダム微調整
        interval_factor = random.uniform(0.85, 1.15)
        interval_min = max(10, int(batch_cfg["interval_min"] * interval_factor))
        interval_max = max(interval_min + 5, int(batch_cfg["interval_max"] * interval_factor))

        # 予想所要時間
        avg_interval = (interval_min + interval_max) / 2
        estimated_duration_sec = int(count * avg_interval)
        estimated_duration_min = estimated_duration_sec // 60

        batch_info = {
            "id": batch_name,
            "start": start_time,
            "count": count,
            "interval_min": interval_min,
            "interval_max": interval_max,
            "estimated_duration_min": estimated_duration_min,
            "status": "pending",
        }
        batches.append(batch_info)

        logger.info(
            f"  {batch_name}: {start_time} 開始, {count}件, "
            f"間隔{interval_min}-{interval_max}秒, 予想{estimated_duration_min}分"
        )

    return batches


def _save_plan(plan: dict) -> None:
    """daily_plan.json に保存する"""
    DAILY_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DAILY_PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    logger.info(f"  保存先: {DAILY_PLAN_PATH}")


def load_daily_plan() -> dict | None:
    """daily_plan.json を読み込む"""
    if not DAILY_PLAN_PATH.exists():
        return None
    try:
        with open(DAILY_PLAN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"daily_plan.json 読み込みエラー: {e}")
        return None


def update_batch_status(batch_id: str, status: str, result: dict = None) -> None:
    """バッチのステータスを更新する"""
    plan = load_daily_plan()
    if not plan:
        return

    for batch in plan.get("post", {}).get("batches", []):
        if batch["id"] == batch_id:
            batch["status"] = status
            if result:
                batch["result"] = result
            batch["updated_at"] = datetime.now().isoformat()
            break

    with open(DAILY_PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def format_schedule_report(plan: dict) -> str:
    """スケジュールを人間が読みやすい形式で返す"""
    day_type = plan.get("day_type", "normal")

    lines = [
        f"\n{'=' * 60}",
        f"日次スケジュール: {plan['date']} ({day_type} day)",
        f"生成日時: {plan['generated_at'][:19]}",
        f"{'=' * 60}",
    ]

    if day_type == "off":
        lines.append(f"  休み日: 全BOT停止")
        lines.append(f"{'=' * 60}")
        return "\n".join(lines)

    post = plan.get("post", {})
    like = plan.get("like", {})
    follow = plan.get("follow", {})

    lines.extend([
        f"  投稿:     {post.get('total', 0)}件",
        f"  いいね:   {like.get('total', 0)}件",
        f"  フォロー: {follow.get('total', 0)}件",
        f"  日次ジッター: {post.get('daily_jitter_min', 0):+d}分",
        f"",
        f"  --- 投稿バッチ詳細 ---",
    ])

    for batch in post.get("batches", []):
        status_icon = {
            "pending": "[ ]",
            "running": "[>]",
            "completed": "[v]",
            "failed": "[x]",
        }.get(batch["status"], "[?]")

        lines.append(
            f"  {status_icon} {batch['id']:8s} "
            f"{batch['start']} 開始 | "
            f"{batch['count']:3d}件 | "
            f"間隔{batch['interval_min']}-{batch['interval_max']}秒 | "
            f"予想{batch['estimated_duration_min']}分"
        )
        if "result" in batch:
            r = batch["result"]
            lines.append(
                f"           -> posted={r.get('posted', 0)} "
                f"failed={r.get('failed', 0)} "
                f"skipped={r.get('skipped', 0)}"
            )

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


# === CLI テスト用 ===
if __name__ == "__main__":
    import io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = None

    plan = generate_daily_schedule(date)
    print(format_schedule_report(plan))
