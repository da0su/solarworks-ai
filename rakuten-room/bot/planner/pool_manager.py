"""ROOM BOT v5.0 - 商品プール管理

source_items.json の健全性チェック・メンテナンスを行う。
- プールサイズ監視
- ジャンルバランス確認
- 投稿済み商品のクリーンアップ
- 超過分のプルーニング
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()

SOURCE_ITEMS_PATH = config.DATA_DIR / "source_items.json"
POST_HISTORY_PATH = config.DATA_DIR / "post_history.json"


def _load_source_items() -> list[dict]:
    """source_items.json を読み込む"""
    if not SOURCE_ITEMS_PATH.exists():
        return []
    try:
        with open(SOURCE_ITEMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_source_items(items: list[dict]):
    """source_items.json をatomic writeで保存"""
    tmp_path = SOURCE_ITEMS_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp_path), str(SOURCE_ITEMS_PATH))


def get_pool_stats() -> dict:
    """プールの統計情報を取得"""
    items = _load_source_items()
    by_genre = {}
    by_priority = {1: 0, 2: 0, 3: 0}
    total_score = 0

    for item in items:
        g = item.get("genre", "unknown")
        by_genre[g] = by_genre.get(g, 0) + 1
        p = item.get("priority", 3)
        by_priority[p] = by_priority.get(p, 0) + 1
        total_score += item.get("score", 0)

    return {
        "total": len(items),
        "by_genre": by_genre,
        "by_priority": by_priority,
        "avg_score": round(total_score / len(items), 1) if items else 0,
    }


def check_pool_health() -> dict:
    """プールの健全性をチェック"""
    stats = get_pool_stats()
    total = stats["total"]
    warnings = []
    is_healthy = True
    needs_replenish = False

    # サイズチェック
    if total < config.POOL_MIN:
        warnings.append(f"プール不足: {total}/{config.POOL_MIN}件")
        needs_replenish = True
        is_healthy = False
    elif total < config.POOL_MIN * 0.5:
        warnings.append(f"プール危険水準: {total}件")
        is_healthy = False

    # ジャンルバランス
    genres = stats["by_genre"]
    if genres:
        avg_count = total / len(config.GENRE_SEARCH_KEYWORDS)
        for genre in config.GENRE_SEARCH_KEYWORDS:
            count = genres.get(genre, 0)
            if count == 0:
                warnings.append(f"ジャンル欠落: {genre}")
                is_healthy = False
            elif count < avg_count * 0.3:
                warnings.append(f"ジャンル不足: {genre}={count}件")

    return {
        "is_healthy": is_healthy,
        "warnings": warnings,
        "needs_replenish": needs_replenish,
        "stats": stats,
    }


def remove_posted_items() -> int:
    """投稿済み商品をプールから除去"""
    items = _load_source_items()
    if not items:
        return 0

    # 投稿済みitem_code取得
    posted_codes = set()
    if POST_HISTORY_PATH.exists():
        try:
            with open(POST_HISTORY_PATH, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    if entry.get("item_code"):
                        posted_codes.add(entry["item_code"])
        except Exception:
            pass

    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        posted_codes |= qm.get_posted_item_codes()
        qm.close()
    except Exception:
        pass

    if not posted_codes:
        return 0

    before = len(items)
    items = [item for item in items if item.get("item_code") not in posted_codes]
    removed = before - len(items)

    if removed > 0:
        _save_source_items(items)
        logger.info(f"投稿済み商品を{removed}件プールから除去 ({before} → {len(items)})")

    return removed


def prune_excess(max_items: int = None) -> int:
    """超過分を削除（score低い順・priority高い順に残す）"""
    max_count = max_items or config.POOL_MAX
    items = _load_source_items()
    if len(items) <= max_count:
        return 0

    # priority昇順（1が最優先）、同priority内でscore降順
    items.sort(key=lambda x: (x.get("priority", 99), -x.get("score", 0)))
    pruned = len(items) - max_count
    items = items[:max_count]
    _save_source_items(items)
    logger.info(f"超過商品を{pruned}件削除 → 残り{len(items)}件")
    return pruned


def rebalance_genres() -> dict:
    """ジャンルバランスを分析"""
    stats = get_pool_stats()
    genres = stats["by_genre"]
    total = stats["total"]

    if total == 0:
        return {"underweight": list(config.GENRE_SEARCH_KEYWORDS.keys()),
                "overweight": [], "balanced": []}

    target_per_genre = total / len(config.GENRE_SEARCH_KEYWORDS)
    underweight = []
    overweight = []
    balanced = []

    for genre in config.GENRE_SEARCH_KEYWORDS:
        count = genres.get(genre, 0)
        if count < target_per_genre * 0.5:
            underweight.append(genre)
        elif count > target_per_genre * 1.5:
            overweight.append(genre)
        else:
            balanced.append(genre)

    return {
        "underweight": underweight,
        "overweight": overweight,
        "balanced": balanced,
        "target_per_genre": round(target_per_genre),
        "distribution": genres,
    }


def format_pool_report() -> str:
    """プール状況を人間が読める形式で返す"""
    stats = get_pool_stats()
    health = check_pool_health()
    balance = rebalance_genres()

    lines = [
        f"\n{'=' * 60}",
        f"商品プール状況",
        f"{'=' * 60}",
        f"  合計:       {stats['total']}件 (目標: {config.POOL_MIN}〜{config.POOL_MAX})",
        f"  平均スコア: {stats['avg_score']}点",
        f"  priority 1: {stats['by_priority'].get(1, 0)}件",
        f"  priority 2: {stats['by_priority'].get(2, 0)}件",
        f"  priority 3: {stats['by_priority'].get(3, 0)}件",
        f"\n  [ジャンル分布]",
    ]
    for g, c in sorted(stats["by_genre"].items()):
        lines.append(f"    {g}: {c}件")

    if health["warnings"]:
        lines.append(f"\n  [警告]")
        for w in health["warnings"]:
            lines.append(f"    ! {w}")

    if balance["underweight"]:
        lines.append(f"\n  [不足ジャンル] {', '.join(balance['underweight'])}")

    lines.append(f"\n  健全性: {'OK' if health['is_healthy'] else 'NG'}")
    lines.append(f"  補充必要: {'はい' if health['needs_replenish'] else 'いいえ'}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
