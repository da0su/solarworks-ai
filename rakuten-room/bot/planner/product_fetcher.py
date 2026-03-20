"""ROOM BOT v5.0 - 楽天API商品自動取得

楽天市場APIでジャンル別に商品を自動取得し、
item_auditor で監査を通過した商品だけを source_items.json に追加する。

フロー: 楽天API → raw候補 → audit → pass のみ source_items に採用
"""

import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()

# 楽天API エンドポイント（新旧）
RAKUTEN_API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601"
RAKUTEN_API_URL_LEGACY = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

SOURCE_ITEMS_PATH = config.DATA_DIR / "source_items.json"
POST_HISTORY_PATH = config.DATA_DIR / "post_history.json"


def _extract_item_code(url: str) -> str:
    """楽天商品URLからitem_codeを抽出"""
    m = re.search(r"item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)", url)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"books\.rakuten\.co\.jp/rb/(\d+)", url)
    if m:
        return f"books:{m.group(1)}"
    return url.split("?")[0].rstrip("/").split("/")[-1]


def _load_posted_item_codes() -> set[str]:
    """投稿済みitem_codeを全ソースから集約"""
    codes = set()
    # post_history.json
    if POST_HISTORY_PATH.exists():
        try:
            with open(POST_HISTORY_PATH, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    if entry.get("item_code"):
                        codes.add(entry["item_code"])
                    if entry.get("url"):
                        codes.add(_extract_item_code(entry["url"]))
        except Exception as e:
            logger.warning(f"post_history.json 読み込みエラー: {e}")
    # SQLite posted
    try:
        from planner.queue_manager import QueueManager
        qm = QueueManager()
        codes |= qm.get_posted_item_codes()
        qm.close()
    except Exception as e:
        logger.warning(f"SQLite posted読み込みエラー: {e}")
    return codes


def _load_existing_item_codes() -> set[str]:
    """現在のsource_itemsのitem_codeを取得"""
    codes = set()
    if SOURCE_ITEMS_PATH.exists():
        try:
            with open(SOURCE_ITEMS_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
                if isinstance(items, list):
                    for item in items:
                        if item.get("item_code"):
                            codes.add(item["item_code"])
        except Exception:
            pass
    return codes


def fetch_api(keyword: str, page: int = 1, hits: int = 30,
              sort: str = "-reviewCount") -> list[dict]:
    """楽天市場API 1回呼出し（新旧ドメインフォールバック）

    Returns:
        list[dict]: APIレスポンスのItems配列（空リストの場合あり）
    """
    app_id = config.RAKUTEN_APP_ID
    if not app_id:
        logger.error("RAKUTEN_APP_ID が未設定")
        return []

    params = {
        "applicationId": app_id,
        "keyword": keyword,
        "hits": min(hits, 30),
        "page": page,
        "sort": sort,
        "imageFlag": 1,
        "format": "json",
    }
    if config.RAKUTEN_ACCESS_KEY:
        params["accessKey"] = config.RAKUTEN_ACCESS_KEY

    query = urllib.parse.urlencode(params)
    urls = [
        f"{RAKUTEN_API_URL}?{query}",
        f"{RAKUTEN_API_URL_LEGACY}?{query}",
    ]

    for api_url in urls:
        try:
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode("utf-8"))
            return data.get("Items", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning("API レート制限 - 30秒待機")
                time.sleep(30)
                continue
            logger.debug(f"API {e.code} エラー: {api_url.split('/')[2]}")
            continue
        except Exception as e:
            logger.debug(f"API 通信エラー: {e}")
            continue

    return []


def convert_to_source_item(raw_item: dict, genre: str) -> dict | None:
    """楽天APIレスポンスをsource_items形式に変換

    Returns:
        dict: {item_code, url, title, genre, priority, score, review_count,
               review_avg, price, image_url, shop_name, fetched_at}
        None: 変換不可の場合
    """
    item = raw_item.get("Item", raw_item)

    url = item.get("itemUrl", "")
    title = item.get("itemName", "")
    if not url or not title:
        return None

    # item_code: APIから直接取得（shopCode + itemCode）
    shop_code = item.get("shopCode", "")
    item_code_raw = item.get("itemCode", "")
    if shop_code and item_code_raw:
        item_code = f"{shop_code}:{item_code_raw}"
    else:
        item_code = _extract_item_code(url)

    # 画像URL
    image_url = ""
    medium_images = item.get("mediumImageUrls", [])
    if medium_images:
        first = medium_images[0]
        image_url = first.get("imageUrl", "") if isinstance(first, dict) else str(first)
    if image_url.startswith("data:"):
        image_url = ""

    # スコア計算
    review_count = item.get("reviewCount", 0)
    review_avg = item.get("reviewAverage", 0)
    if isinstance(review_avg, str):
        try:
            review_avg = float(review_avg)
        except ValueError:
            review_avg = 0
    price = item.get("itemPrice", 0)

    score = 50
    score += min(int(review_count / 10), 20)   # レビュー数 max+20
    score += min(int(review_avg * 4), 20)       # レビュー平均 max+20
    if 1000 <= price <= 10000:                  # 価格帯ボーナス
        score += 10
    else:
        score += 5
    score = min(score, 100)

    # priority
    if score >= 80:
        priority = 1
    elif score >= 60:
        priority = 2
    else:
        priority = 3

    return {
        "item_code": item_code,
        "url": url,
        "title": title[:100],
        "genre": genre,
        "priority": priority,
        "score": score,
        "review_count": review_count,
        "review_avg": review_avg,
        "price": price,
        "image_url": image_url,
        "shop_name": item.get("shopName", ""),
        "fetched_at": datetime.now().isoformat(),
    }


def fetch_genre_items(genre: str, keywords: list[str],
                      max_per_keyword: int = 60,
                      exclude_codes: set[str] = None) -> list[dict]:
    """1ジャンルの商品を複数キーワードで取得

    Args:
        genre: ジャンル名
        keywords: 検索キーワードリスト
        max_per_keyword: キーワードあたりの最大取得数
        exclude_codes: 除外するitem_codeセット

    Returns:
        list[dict]: source_items形式の商品リスト（重複除去済み）
    """
    exclude = exclude_codes or set()
    seen_codes = set()
    results = []
    pages_per_keyword = max(1, max_per_keyword // 30)

    for keyword in keywords:
        for page in range(1, pages_per_keyword + 1):
            raw_items = fetch_api(keyword, page=page)
            if not raw_items:
                break

            for raw in raw_items:
                item = convert_to_source_item(raw, genre)
                if item is None:
                    continue
                code = item["item_code"]
                if code in exclude or code in seen_codes:
                    continue
                seen_codes.add(code)
                results.append(item)

            # レート制限対応
            time.sleep(random.uniform(1.0, 2.0))

        logger.info(f"  [{genre}] '{keyword}' → {len(results)}件累計")

    return results


def fetch_all_genres(target_total: int = None) -> list[dict]:
    """全ジャンルから商品を取得

    Args:
        target_total: 目標取得数。Noneの場合はconfig.POOL_MIN + buffer

    Returns:
        list[dict]: 全ジャンル合算の商品リスト（重複除去済み）
    """
    target = target_total or (config.POOL_MIN + config.POOL_REPLENISH_BUFFER)
    genres = config.GENRE_SEARCH_KEYWORDS
    per_genre = max(10, target // len(genres) + 5)

    logger.info(f"=== 全ジャンル商品取得開始: 目標 {target}件 ({per_genre}件/ジャンル) ===")

    # 除外コード集約
    exclude = _load_posted_item_codes() | _load_existing_item_codes()
    logger.info(f"除外item_code: {len(exclude)}件")

    all_items = []
    genre_counts = {}

    for genre, keywords in genres.items():
        logger.info(f"[{genre}] 取得開始 (キーワード{len(keywords)}個)")
        items = fetch_genre_items(genre, keywords,
                                  max_per_keyword=per_genre // len(keywords) + 30,
                                  exclude_codes=exclude)
        # 取得済みコードを除外セットに追加（ジャンル間重複防止）
        for item in items:
            exclude.add(item["item_code"])

        all_items.extend(items)
        genre_counts[genre] = len(items)
        logger.info(f"[{genre}] 取得完了: {len(items)}件")

    logger.info(f"=== 全ジャンル取得完了: {len(all_items)}件 ===")
    logger.info(f"ジャンル分布: {genre_counts}")

    return all_items


def replenish_pool(target_min: int = None, target_max: int = None,
                   dry_run: bool = False) -> dict:
    """商品プールを補充する（メインエントリ）

    フロー: API取得 → audit → pass のみ source_items に追加

    Args:
        target_min: 最低維持件数（デフォルト: config.POOL_MIN）
        target_max: 最大件数（デフォルト: config.POOL_MAX）
        dry_run: Trueの場合は取得のみ（保存しない）

    Returns:
        dict: {fetched, audited_pass, audited_review, audited_fail,
               new_added, pruned, total_pool, genre_distribution}
    """
    t_min = target_min or config.POOL_MIN
    t_max = target_max or config.POOL_MAX

    # 既存プール読み込み
    existing = []
    if SOURCE_ITEMS_PATH.exists():
        try:
            with open(SOURCE_ITEMS_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
        except Exception:
            existing = []

    current_count = len(existing)
    logger.info(f"現在のプール: {current_count}件 (目標: {t_min}〜{t_max})")

    if current_count >= t_min:
        logger.info("プール十分 - 補充不要")
        return {
            "fetched": 0, "audited_pass": 0, "audited_review": 0,
            "audited_fail": 0, "new_added": 0, "pruned": 0,
            "total_pool": current_count,
            "genre_distribution": _count_genres(existing),
            "skipped": True,
        }

    # 不足分 + バッファを取得
    needed = t_min - current_count + config.POOL_REPLENISH_BUFFER
    logger.info(f"不足: {t_min - current_count}件 + バッファ{config.POOL_REPLENISH_BUFFER} = {needed}件取得予定")

    # API取得
    raw_items = fetch_all_genres(target_total=needed)
    fetched_count = len(raw_items)

    if fetched_count == 0:
        logger.warning("API取得結果が0件")
        return {
            "fetched": 0, "audited_pass": 0, "audited_review": 0,
            "audited_fail": 0, "new_added": 0, "pruned": 0,
            "total_pool": current_count,
            "genre_distribution": _count_genres(existing),
            "skipped": False,
        }

    # 監査実行
    from planner.item_auditor import audit_items
    audit_result = audit_items(raw_items)

    passed = audit_result["passed"]
    reviewed = audit_result["reviewed"]
    failed = audit_result["failed"]

    logger.info(f"監査結果: pass={len(passed)}, review={len(reviewed)}, fail={len(failed)}")

    if dry_run:
        logger.info("[DRY RUN] 保存をスキップ")
        return {
            "fetched": fetched_count,
            "audited_pass": len(passed),
            "audited_review": len(reviewed),
            "audited_fail": len(failed),
            "new_added": 0, "pruned": 0,
            "total_pool": current_count,
            "genre_distribution": _count_genres(existing),
            "skipped": False, "dry_run": True,
        }

    # passした商品をプールにマージ
    existing_codes = {item.get("item_code") for item in existing if item.get("item_code")}
    new_items = [item for item in passed if item.get("item_code") not in existing_codes]
    merged = existing + new_items
    new_added = len(new_items)

    # 超過分を削除（score低い順）
    pruned = 0
    if len(merged) > t_max:
        merged.sort(key=lambda x: (x.get("priority", 99), -x.get("score", 0)))
        pruned = len(merged) - t_max
        merged = merged[:t_max]

    # atomic write
    _atomic_save(SOURCE_ITEMS_PATH, merged)

    total = len(merged)
    genre_dist = _count_genres(merged)
    logger.info(f"プール更新完了: +{new_added}件, -{pruned}件削除 → 合計{total}件")
    logger.info(f"ジャンル分布: {genre_dist}")

    return {
        "fetched": fetched_count,
        "audited_pass": len(passed),
        "audited_review": len(reviewed),
        "audited_fail": len(failed),
        "new_added": new_added,
        "pruned": pruned,
        "total_pool": total,
        "genre_distribution": genre_dist,
        "skipped": False,
    }


def _count_genres(items: list[dict]) -> dict:
    """ジャンル別件数をカウント"""
    counts = {}
    for item in items:
        g = item.get("genre", "unknown")
        counts[g] = counts.get(g, 0) + 1
    return counts


def _atomic_save(path: Path, data):
    """atomic writeでJSON保存（書き込み中の破損防止）"""
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp_path), str(path))


def format_replenish_report(result: dict) -> str:
    """補充結果を人間が読める形式で返す"""
    lines = [
        f"\n{'=' * 60}",
        f"商品プール補充{'（DRY RUN）' if result.get('dry_run') else ''}完了",
        f"{'=' * 60}",
    ]
    if result.get("skipped"):
        lines.append(f"  プール十分のため補充不要（現在{result['total_pool']}件）")
    else:
        lines.extend([
            f"  API取得:     {result['fetched']}件",
            f"  監査pass:    {result['audited_pass']}件",
            f"  監査review:  {result['audited_review']}件",
            f"  監査fail:    {result['audited_fail']}件",
            f"  新規追加:    {result['new_added']}件",
            f"  超過削除:    {result['pruned']}件",
        ])
    lines.append(f"  プール合計:  {result['total_pool']}件")
    if result.get("genre_distribution"):
        lines.append(f"  ジャンル分布:")
        for g, c in sorted(result["genre_distribution"].items()):
            lines.append(f"    {g}: {c}件")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
