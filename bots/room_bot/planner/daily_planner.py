"""ROOM BOT v2 - デイリープランナー

source_items.json から商品候補を読み込み、
post_history.json の投稿済みitem_codeを除外し、
コメント生成 → スコアリング → SQLiteキューに登録する。
"""

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from planner.queue_manager import QueueManager
from executor.comment_generator import detect_genre, generate_comment
from executor.post_scorer import score_and_regenerate

from logger.logger import setup_logger

logger = setup_logger()

SOURCE_ITEMS_PATH = config.DATA_DIR / "source_items.json"
POST_HISTORY_PATH = config.DATA_DIR / "post_history.json"


def _extract_item_code(url: str) -> str:
    """楽天商品URLからitem_codeを抽出する

    例: https://item.rakuten.co.jp/cyberl2010/0102-0310-0201/
    → cyberl2010:0102-0310-0201

    例: https://books.rakuten.co.jp/rb/16834204/
    → books:16834204
    """
    m = re.search(r"item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)", url)
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    m = re.search(r"books\.rakuten\.co\.jp/rb/(\d+)", url)
    if m:
        return f"books:{m.group(1)}"

    return url.split("?")[0].rstrip("/").split("/")[-1]


def _load_posted_item_codes() -> set[str]:
    """post_history.json から投稿済みitem_codeを読み込む"""
    codes = set()
    if not POST_HISTORY_PATH.exists():
        return codes
    try:
        with open(POST_HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
        for entry in history:
            url = entry.get("url", "")
            if url:
                codes.add(_extract_item_code(url))
            ic = entry.get("item_code", "")
            if ic:
                codes.add(ic)
    except Exception as e:
        logger.warning(f"post_history.json 読み込みエラー: {e}")
    return codes


def load_source_items() -> list[dict]:
    """source_items.json から商品候補を読み込む"""
    if not SOURCE_ITEMS_PATH.exists():
        logger.warning(f"商品候補ファイルが見つかりません: {SOURCE_ITEMS_PATH}")
        return []

    with open(SOURCE_ITEMS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return []


def generate_daily_plan(queue_date: str = None, max_items: int = None,
                        source_path: str = None) -> dict:
    """当日の投稿計画を生成してSQLiteに登録する

    Args:
        queue_date: 対象日 (YYYY-MM-DD)。省略時は今日
        max_items: 最大投稿件数。省略時はconfig値
        source_path: 商品候補JSONのパス。省略時はsource_items.json

    Returns:
        dict: {"date": str, "planned": int, "skipped_duplicate": int, "items": [...]}
    """
    date = queue_date or datetime.now().strftime("%Y-%m-%d")
    target_count = max_items or config.get_daily_post_target()

    logger.info(f"=== 投稿計画生成: {date} (目標: {target_count}件) ===")

    # 商品候補読み込み
    if source_path:
        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data if isinstance(data, list) else data.get("items", data.get("posts", []))
    else:
        items = load_source_items()

    if not items:
        logger.error("商品候補が0件です。source_items.json を作成してください。")
        return {"date": date, "planned": 0, "skipped_duplicate": 0, "items": []}

    logger.info(f"商品候補: {len(items)}件")

    # 投稿済みitem_codeを取得（post_history.json + SQLiteキュー両方）
    posted_codes = _load_posted_item_codes()
    qm = QueueManager()
    posted_codes |= qm.get_posted_item_codes()
    logger.info(f"投稿済みitem_code: {len(posted_codes)}件")

    # priority でソート（1が最優先）→ 同priority内でシャッフル
    candidates = list(items)
    random.shuffle(candidates)
    candidates.sort(key=lambda x: x.get("priority", 99))

    planned_items = []
    skipped = 0

    for item in candidates:
        if len(planned_items) >= target_count:
            break

        url = item.get("url", item.get("item_url", ""))
        title = item.get("title", "")

        if not url or not url.startswith("http"):
            continue

        # item_code: source_items.json に明示されていればそれを使う
        item_code = item.get("item_code") or _extract_item_code(url)

        # 重複チェック（post_history + SQLiteキュー）
        if item_code in posted_codes:
            skipped += 1
            continue

        # ジャンル: source_items.json に明示されていればそれを使う
        genre = item.get("genre") or detect_genre(title, url)

        # コメント生成 + スコアリング
        comment, score_result = score_and_regenerate(
            title, url, genre, generate_comment,
        )
        score = score_result["score"] if score_result else 0

        # キューに登録
        queue_id = qm.enqueue(
            queue_date=date,
            item_code=item_code,
            item_url=url,
            title=title,
            comment=comment,
            genre=genre,
            score=score,
        )

        if queue_id is not None:
            planned_items.append({
                "queue_id": queue_id,
                "item_code": item_code,
                "title": title,
                "url": url,
                "genre": genre,
                "score": score,
                "comment_preview": comment[:60],
            })
            posted_codes.add(item_code)
        else:
            skipped += 1

    # デイリーサマリー保存
    qm.save_daily_summary(date)
    qm.close()

    result = {
        "date": date,
        "planned": len(planned_items),
        "skipped_duplicate": skipped,
        "items": planned_items,
    }

    logger.info(f"計画完了: {result['planned']}件追加, {result['skipped_duplicate']}件重複スキップ")
    return result


def format_plan_report(plan: dict) -> str:
    """計画結果を人間が読みやすい形式で返す"""
    lines = [
        f"\n{'=' * 60}",
        f"投稿計画: {plan['date']}",
        f"{'=' * 60}",
        f"  登録:     {plan['planned']}件",
        f"  重複除外: {plan['skipped_duplicate']}件",
    ]

    if plan["items"]:
        lines.append(f"\n--- 登録済みアイテム ---")
        for i, item in enumerate(plan["items"]):
            lines.append(f"  [{i+1}] {item['title'][:40]}")
            lines.append(f"      genre={item['genre']} score={item['score']}pt")
            lines.append(f"      {item['comment_preview']}")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
