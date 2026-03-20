"""ROOM BOT v5.0 - 異常検知

投稿システムの健全性をチェックし、異常時にアラートを出す。

チェック項目:
- プール残量
- 連続失敗数
- 成功率
- スキップ率
- プール枯渇予測日数
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()


# 閾値設定
THRESHOLDS = {
    "pool_size":           {"warning": 150, "critical": 50},
    "consecutive_fails":   {"warning": 2, "critical": 3},
    "success_rate":        {"warning": 0.70, "critical": 0.50},
    "skip_rate":           {"warning": 0.30, "critical": 0.50},
    "pool_depletion_days": {"warning": 7, "critical": 3},
}


def check_health(queue_date: str = None) -> dict:
    """全項目をチェックして健全性レポートを返す

    Returns:
        dict: {
            "status": "OK" | "WARNING" | "CRITICAL",
            "checks": {項目名: {status, value, threshold}},
            "warnings": [str],
            "timestamp": str,
        }
    """
    date = queue_date or datetime.now().strftime("%Y-%m-%d")
    checks = {}
    warnings = []
    overall = "OK"

    # 1. プール残量
    pool_check = _check_pool_size()
    checks["pool_size"] = pool_check
    if pool_check["status"] != "OK":
        warnings.append(f"プール: {pool_check['value']}件 ({pool_check['status']})")
        overall = _escalate(overall, pool_check["status"])

    # 2. 連続失敗
    fail_check = _check_consecutive_failures(date)
    checks["consecutive_fails"] = fail_check
    if fail_check["status"] != "OK":
        warnings.append(f"連続失敗: {fail_check['value']}件 ({fail_check['status']})")
        overall = _escalate(overall, fail_check["status"])

    # 3. 成功率
    rate_check = _check_success_rate(date)
    checks["success_rate"] = rate_check
    if rate_check["status"] != "OK":
        warnings.append(f"成功率: {rate_check['value']*100:.1f}% ({rate_check['status']})")
        overall = _escalate(overall, rate_check["status"])

    # 4. スキップ率
    skip_check = _check_skip_rate(date)
    checks["skip_rate"] = skip_check
    if skip_check["status"] != "OK":
        warnings.append(f"スキップ率: {skip_check['value']*100:.1f}% ({skip_check['status']})")
        overall = _escalate(overall, skip_check["status"])

    # 5. プール枯渇予測
    depl_check = _check_pool_depletion()
    checks["pool_depletion_days"] = depl_check
    if depl_check["status"] != "OK":
        warnings.append(f"プール枯渇: 約{depl_check['value']}日 ({depl_check['status']})")
        overall = _escalate(overall, depl_check["status"])

    return {
        "status": overall,
        "checks": checks,
        "warnings": warnings,
        "date": date,
        "timestamp": datetime.now().isoformat(),
    }


def should_stop(queue_date: str = None) -> tuple[bool, str]:
    """CRITICALがあれば停止すべきかを判定"""
    result = check_health(queue_date)
    if result["status"] == "CRITICAL":
        return True, "; ".join(result["warnings"])
    return False, ""


def _check_pool_size() -> dict:
    """プール残量チェック"""
    from planner.pool_manager import get_pool_stats
    stats = get_pool_stats()
    total = stats["total"]
    t = THRESHOLDS["pool_size"]

    if total <= t["critical"]:
        status = "CRITICAL"
    elif total <= t["warning"]:
        status = "WARNING"
    else:
        status = "OK"

    return {"status": status, "value": total,
            "threshold_warning": t["warning"], "threshold_critical": t["critical"]}


def _check_consecutive_failures(date: str) -> dict:
    """連続失敗チェック（当日のキューから）"""
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        items = qm.get_by_date(date)
        qm.close()
    except Exception:
        return {"status": "OK", "value": 0, "threshold_warning": 2, "threshold_critical": 3}

    # 末尾から連続失敗をカウント
    consecutive = 0
    for item in reversed(items):
        if item["status"] == "failed":
            consecutive += 1
        elif item["status"] in ("posted", "skipped"):
            break

    t = THRESHOLDS["consecutive_fails"]
    if consecutive >= t["critical"]:
        status = "CRITICAL"
    elif consecutive >= t["warning"]:
        status = "WARNING"
    else:
        status = "OK"

    return {"status": status, "value": consecutive,
            "threshold_warning": t["warning"], "threshold_critical": t["critical"]}


def _check_success_rate(date: str) -> dict:
    """成功率チェック"""
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        stats = qm.get_status_summary(date)
        qm.close()
    except Exception:
        return {"status": "OK", "value": 1.0, "threshold_warning": 0.70, "threshold_critical": 0.50}

    posted = stats.get("posted", 0)
    failed = stats.get("failed", 0)
    total = posted + failed
    if total == 0:
        return {"status": "OK", "value": 1.0, "threshold_warning": 0.70, "threshold_critical": 0.50}

    rate = posted / total
    t = THRESHOLDS["success_rate"]

    if rate < t["critical"]:
        status = "CRITICAL"
    elif rate < t["warning"]:
        status = "WARNING"
    else:
        status = "OK"

    return {"status": status, "value": round(rate, 3),
            "threshold_warning": t["warning"], "threshold_critical": t["critical"]}


def _check_skip_rate(date: str) -> dict:
    """スキップ率チェック"""
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        stats = qm.get_status_summary(date)
        qm.close()
    except Exception:
        return {"status": "OK", "value": 0.0, "threshold_warning": 0.30, "threshold_critical": 0.50}

    skipped = stats.get("skipped", 0)
    total = stats.get("total", 0)
    if total == 0:
        return {"status": "OK", "value": 0.0, "threshold_warning": 0.30, "threshold_critical": 0.50}

    rate = skipped / total
    t = THRESHOLDS["skip_rate"]

    if rate > t["critical"]:
        status = "CRITICAL"
    elif rate > t["warning"]:
        status = "WARNING"
    else:
        status = "OK"

    return {"status": status, "value": round(rate, 3),
            "threshold_warning": t["warning"], "threshold_critical": t["critical"]}


def _check_pool_depletion() -> dict:
    """プール枯渇予測（日数）"""
    from planner.pool_manager import get_pool_stats
    stats = get_pool_stats()
    total = stats["total"]

    mode = config.get_operation_mode()
    if mode["mode"] == "SAFE":
        daily = mode.get("safe_limit", 20)
    else:
        daily = config.POST_DAILY_MAX

    if daily == 0:
        days = 999
    else:
        days = total // daily

    t = THRESHOLDS["pool_depletion_days"]
    if days <= t["critical"]:
        status = "CRITICAL"
    elif days <= t["warning"]:
        status = "WARNING"
    else:
        status = "OK"

    return {"status": status, "value": days,
            "threshold_warning": t["warning"], "threshold_critical": t["critical"]}


def _escalate(current: str, new: str) -> str:
    """ステータスをエスカレーション"""
    order = {"OK": 0, "WARNING": 1, "CRITICAL": 2}
    if order.get(new, 0) > order.get(current, 0):
        return new
    return current


def format_health_report(result: dict) -> str:
    """ヘルスチェック結果を人間が読める形式で返す"""
    lines = [
        f"\n{'=' * 60}",
        f"システム健全性チェック: {result['date']}",
        f"{'=' * 60}",
        f"  総合ステータス: {result['status']}",
    ]

    for name, check in result["checks"].items():
        icon = {"OK": "o", "WARNING": "!", "CRITICAL": "X"}[check["status"]]
        lines.append(f"  [{icon}] {name}: {check['value']} ({check['status']})")

    if result["warnings"]:
        lines.append(f"\n  [警告]")
        for w in result["warnings"]:
            lines.append(f"    {w}")

    lines.append(f"\n  チェック時刻: {result['timestamp']}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
