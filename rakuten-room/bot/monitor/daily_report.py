"""ROOM BOT v5.0 - 日次レポート生成

毎朝9時レポート + 23時日報・計画 を生成する。
Slack通知はslack_notifier.pyに委譲。
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()


def generate_report(queue_date: str = None, report_type: str = "morning") -> str:
    """日次レポートを生成する

    Args:
        queue_date: 対象日（省略時は今日）
        report_type: "morning"（9時レポート）or "night"（23時日報+計画）

    Returns:
        str: レポートテキスト
    """
    date = queue_date or datetime.now().strftime("%Y-%m-%d")

    if report_type == "night":
        return _generate_night_report(date)
    else:
        return _generate_morning_report(date)


def _generate_morning_report(date: str) -> str:
    """朝9時レポート: 投稿結果サマリー"""
    stats = _get_queue_stats(date)
    pool = _get_pool_info()
    health = _get_health_status(date)

    posted = stats.get("posted", 0)
    failed = stats.get("failed", 0)
    skipped = stats.get("skipped", 0)
    total_processed = posted + failed + skipped
    success_rate = (posted / (posted + failed) * 100) if (posted + failed) > 0 else 0

    # 処理時間（最初の投稿〜最後の投稿）
    elapsed = _get_elapsed_time(date)
    avg_speed = elapsed / posted if posted > 0 else 0

    elapsed_str = _format_duration(elapsed)

    lines = [
        f"============================================================",
        f"ROOM BOT 朝レポート: {date}",
        f"============================================================",
        f"",
        f"[投稿実績]",
        f"  成功:       {posted}件",
        f"  失敗:       {failed}件",
        f"  スキップ:   {skipped}件",
        f"  成功率:     {success_rate:.1f}%",
        f"",
        f"[パフォーマンス]",
        f"  処理時間:   {elapsed_str}",
        f"  平均速度:   {avg_speed:.1f}秒/件" if posted > 0 else "  平均速度:   -",
        f"",
        f"[プール状況]",
        f"  残り商品:   {pool['total']:,}件",
        f"  推定稼働日: {pool['depletion_days']}日",
        f"",
        f"[健全性]",
        f"  ステータス: {health['status']}",
    ]
    if health.get("warnings"):
        for w in health["warnings"]:
            lines.append(f"  警告: {w}")
    else:
        lines.append(f"  警告: なし")

    # 残キュー
    queued = stats.get("queued", 0)
    lines.extend([
        f"",
        f"[キュー]",
        f"  本日残り:   {queued}件",
        f"============================================================",
    ])

    return "\n".join(lines)


def _generate_night_report(date: str) -> str:
    """23時日報: 本日の日報 + 明日の計画"""
    stats = _get_queue_stats(date)
    pool = _get_pool_info()
    health = _get_health_status(date)
    tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    posted = stats.get("posted", 0)
    failed = stats.get("failed", 0)
    skipped = stats.get("skipped", 0)
    success_rate = (posted / (posted + failed) * 100) if (posted + failed) > 0 else 0
    elapsed = _get_elapsed_time(date)
    avg_speed = elapsed / posted if posted > 0 else 0

    mode = config.get_operation_mode()
    if mode["mode"] == "AUTO":
        tomorrow_target = f"{config.POST_DAILY_MIN}〜{config.POST_DAILY_MAX}件"
    elif mode["mode"] == "SAFE":
        tomorrow_target = f"{mode.get('safe_limit', 20)}件（SAFE）"
    else:
        tomorrow_target = "停止中（STOP）"

    lines = [
        f"============================================================",
        f"ROOM BOT 日報: {date}",
        f"============================================================",
        f"",
        f"[本日の実績]",
        f"  成功:       {posted}件",
        f"  失敗:       {failed}件",
        f"  スキップ:   {skipped}件",
        f"  成功率:     {success_rate:.1f}%",
        f"  平均速度:   {avg_speed:.1f}秒/件" if posted > 0 else "  平均速度:   -",
        f"",
        f"[明日の計画] {tomorrow}",
        f"  予定投稿:   {tomorrow_target}",
        f"  プール残:   {pool['total']:,}件（推定{pool['depletion_days']}日分）",
        f"  運用モード: {mode['mode']}",
        f"",
        f"[健全性]",
        f"  ステータス: {health['status']}",
    ]
    if health.get("warnings"):
        for w in health["warnings"]:
            lines.append(f"  警告: {w}")
    else:
        lines.append(f"  警告: なし")

    lines.append(f"============================================================")
    return "\n".join(lines)


def generate_slack_morning(date: str = None) -> str:
    """Slack用朝レポート（簡潔版）"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    stats = _get_queue_stats(date)
    pool = _get_pool_info()
    health = _get_health_status(date)

    posted = stats.get("posted", 0)
    failed = stats.get("failed", 0)
    skipped = stats.get("skipped", 0)
    success_rate = (posted / (posted + failed) * 100) if (posted + failed) > 0 else 0
    elapsed = _get_elapsed_time(date)
    avg_speed = elapsed / posted if posted > 0 else 0

    lines = [
        f"[朝レポート] {date}",
        f"成功{posted}件 / 失敗{failed}件 / スキップ{skipped}件",
        f"成功率{success_rate:.1f}% / 平均{avg_speed:.1f}秒",
        f"プール残: {pool['total']:,}件（{pool['depletion_days']}日分）",
        f"ステータス: {health['status']}",
    ]
    if health.get("warnings"):
        lines.append(f"警告: {'; '.join(health['warnings'])}")

    return "\n".join(lines)


def generate_slack_night(date: str = None) -> str:
    """Slack用23時日報+計画（簡潔版）"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    stats = _get_queue_stats(date)
    pool = _get_pool_info()
    health = _get_health_status(date)
    tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    posted = stats.get("posted", 0)
    failed = stats.get("failed", 0)
    success_rate = (posted / (posted + failed) * 100) if (posted + failed) > 0 else 0
    elapsed = _get_elapsed_time(date)
    avg_speed = elapsed / posted if posted > 0 else 0

    mode = config.get_operation_mode()
    if mode["mode"] == "AUTO":
        tomorrow_target = f"{config.POST_DAILY_MIN}〜{config.POST_DAILY_MAX}件"
    elif mode["mode"] == "SAFE":
        tomorrow_target = f"{mode.get('safe_limit', 20)}件（SAFE）"
    else:
        tomorrow_target = "停止中"

    lines = [
        f"[日報] {date}",
        f"成功{posted}件 / 失敗{failed}件 / スキップ{stats.get('skipped', 0)}件",
        f"成功率{success_rate:.1f}% / 平均{avg_speed:.1f}秒",
        f"",
        f"[明日の計画] {tomorrow}",
        f"予定投稿: {tomorrow_target}",
        f"プール残: {pool['total']:,}件（{pool['depletion_days']}日分）",
        f"ステータス: {health['status']}",
    ]
    if health.get("warnings"):
        lines.append(f"警告: {'; '.join(health['warnings'])}")

    return "\n".join(lines)


def save_report(report: str, queue_date: str = None, report_type: str = "morning") -> Path:
    """レポートをファイルに保存"""
    date = queue_date or datetime.now().strftime("%Y-%m-%d")
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 個別ファイル
    filename = f"{date}_{report_type}_report.txt"
    path = config.REPORT_DIR / filename
    path.write_text(report, encoding="utf-8")

    # latest
    latest = config.REPORT_DIR / "latest_report.txt"
    latest.write_text(report, encoding="utf-8")

    logger.info(f"レポート保存: {path}")
    return path


# --- ヘルパー関数 ---

def _get_queue_stats(date: str) -> dict:
    """キュー統計取得"""
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        stats = qm.get_status_summary(date)
        qm.close()
        return stats
    except Exception:
        return {"queued": 0, "running": 0, "posted": 0, "failed": 0, "skipped": 0, "total": 0}


def _get_pool_info() -> dict:
    """プール情報取得"""
    try:
        from planner.pool_manager import get_pool_stats
        stats = get_pool_stats()
        total = stats["total"]
        mode = config.get_operation_mode()
        daily = mode.get("safe_limit", 20) if mode["mode"] == "SAFE" else config.POST_DAILY_MAX
        days = total // daily if daily > 0 else 999
        return {"total": total, "depletion_days": days, "by_genre": stats.get("by_genre", {})}
    except Exception:
        return {"total": 0, "depletion_days": 0, "by_genre": {}}


def _get_health_status(date: str) -> dict:
    """ヘルスステータス取得"""
    try:
        from monitor.health_checker import check_health
        return check_health(date)
    except Exception:
        return {"status": "UNKNOWN", "warnings": []}


def _get_elapsed_time(date: str) -> float:
    """処理時間（秒）を取得"""
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        items = qm.get_by_date(date)
        qm.close()

        first_time = None
        last_time = None
        for item in items:
            posted_at = item.get("posted_at")
            if posted_at:
                if first_time is None:
                    first_time = posted_at
                last_time = posted_at

        if first_time and last_time:
            t1 = datetime.fromisoformat(first_time.replace(" ", "T"))
            t2 = datetime.fromisoformat(last_time.replace(" ", "T"))
            return max((t2 - t1).total_seconds(), 0)
    except Exception:
        pass
    return 0


def _format_duration(seconds: float) -> str:
    """秒数を「Xh Ym Zs」形式に変換"""
    if seconds <= 0:
        return "-"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}時間{minutes}分{secs}秒"
    return f"{minutes}分{secs}秒"
