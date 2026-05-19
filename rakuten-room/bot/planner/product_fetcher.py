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
    pages_per_keyword = max(3, max_per_keyword // 30)  # 最低3ページ取得（90件/KW）

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


def fetch_all_genres(target_total: int = None,
                     allowlist_genres: list[str] | None = None,
                     run_id: str = "") -> list[dict]:
    """全ジャンルから商品を取得

    Args:
        target_total: 目標取得数。Noneの場合はconfig.POOL_MIN + buffer
        allowlist_genres: 指定がある場合はこのジャンルのみ取得 (CEO 5/20 緊急止血対策)
        run_id: 実行 ID (構造化ログ用)

    Returns:
        list[dict]: 全ジャンル合算の商品リスト（重複除去済み）

    【2026-05-20 Codex 29回目 review 反映 (CEO 5/20 02:30 指示)】
    真因: 旧コードは dict 挿入順 (kitchen→beauty→living→fashion→...→baby) で
    iterate しており、kitchen(w=1) / fashion(w=1) で target に達するため
    food(w=3) / kids(w=2) / baby(w=3) は『目標達成済みでスキップ』され、
    weight が selection に 全く効かなかった (5/18 以降 baby=3,food=2,kids=2)。

    修正:
    1. weight 降順で iterate (food/baby/kids が先頭に来る)
    2. per_genre は『weight 比率で正確配分 + 高 weight は >= 取得最低保証』
    3. target 早期到達でも 高 weight 残ジャンルは取得継続 (deprioritize なジャンルのみスキップ)
    4. debug ログ強化 (genre 別 入力 weight / 計算済 per_genre / 取得件数 / 監査前)
    5. allowlist_genres 指定時 (CEO 緊急止血) は完全に限定取得
    """
    target = target_total or (config.POOL_MIN + config.POOL_REPLENISH_BUFFER)
    # Codex 31回目 #7 fix: target_total<=0 早期 return (異常呼出ガード)
    if target <= 0:
        logger.warning(f"fetch_all_genres: target<=0 ({target}) → 早期 return")
        return []
    genres = config.GENRE_SEARCH_KEYWORDS

    # CEO 5/16 19:00 「即買い」実態確認反映:
    # PERSONA_GENRE_WEIGHTS で baby/food/kids を厚く・pet/car/outdoor/garden を除外
    weights = getattr(config, "PERSONA_GENRE_WEIGHTS", {})
    # allowlist 指定時はそれ以外を完全に除外
    # Codex 34回目 #3 fix: allowlist 内で weight=0 でも強制的に active 化
    # (CEO 緊急止血で指定したのに weight=0 で skip されると意図と乖離)
    if allowlist_genres:
        allow_set = set(allowlist_genres)
        active_genres = {
            g: max(1.0, weights.get(g, 1.0))  # 最低 1.0 (weight=0 を上書き)
            for g in genres.keys() if g in allow_set
        }
        logger.info(f"[persona_filter] ALLOWLIST mode (CEO 5/20 緊急止血): {sorted(active_genres.keys())}")
    else:
        active_genres = {g: weights.get(g, 1.0) for g in genres.keys() if weights.get(g, 1.0) > 0}
    total_weight = sum(active_genres.values()) or 1.0
    # weight=0 (除外) ジャンルは取得しない
    excluded_genres = [g for g in genres.keys() if weights.get(g, 1.0) == 0.0]
    if excluded_genres:
        logger.info(f"[persona_filter] 除外 genre (weight=0): {excluded_genres}")

    # 【重要】weight 降順 sort (DESC) - food/baby/kids が先頭に来るよう保証
    # 同 weight 内では dict 挿入順を維持 (Python 3.7+ stable sort)
    # Codex 31回目 #5 fix: O(n^2) → O(n) (index map を事前計算)
    orig_order = {g: i for i, g in enumerate(genres.keys())}
    sorted_genres = sorted(
        active_genres.items(),
        key=lambda kv: (-kv[1], orig_order[kv[0]])
    )

    # genre 別 per_genre 計算 (weight 比率) + 最低保証
    # Codex 32回目 #4 fix: target に対する比率上限で 過剰最低保証を回避
    # Codex 33回目 #3 fix: 最低保証総和が target を上回らないよう scale down
    # HIGH_PRIORITY_MIN = min(60, ceil(target * 0.4)), NORMAL_MIN = min(25, ceil(target * 0.15))
    import math as _math
    per_genre_plan = {}
    HIGH_PRIORITY_MIN = min(60, _math.ceil(target * 0.4))
    NORMAL_MIN = min(25, _math.ceil(target * 0.15))
    for g, w in sorted_genres:
        base = int(target * w / total_weight) + 5
        if w >= 2.0:
            per_genre_plan[g] = max(base, HIGH_PRIORITY_MIN)
        else:
            per_genre_plan[g] = max(base, NORMAL_MIN)
    # Codex 33回目 #3: 全 plan 総和が target*1.3 を超える場合 scale down
    PLAN_SOFT_CAP_MULT = 1.3
    plan_sum = sum(per_genre_plan.values())
    target_soft_cap = int(target * PLAN_SOFT_CAP_MULT)
    if plan_sum > target_soft_cap and plan_sum > 0:
        scale = target_soft_cap / plan_sum
        for g in per_genre_plan:
            # 最低 10 件は確保 (取得 0 だと監査 pool 不足)
            per_genre_plan[g] = max(10, int(per_genre_plan[g] * scale))
        logger.info(
            f"[scale_down] plan_sum {plan_sum} > target*{PLAN_SOFT_CAP_MULT}={target_soft_cap} "
            f"→ scale={scale:.2f}"
        )

    logger.info(f"=== 全ジャンル商品取得開始: 目標 {target}件 (active {len(active_genres)} ジャンル) run_id={run_id or 'NA'} ===")
    # Codex 34回目 #4 fix: ログ表示で順序保持 (list で表示, dict は順序が見えにくい)
    logger.info(f"[persona_filter] weights (sorted DESC): {sorted_genres}")
    logger.info(f"[persona_filter] per_genre 配分 plan: {per_genre_plan}")

    # 除外コード集約
    exclude = _load_posted_item_codes() | _load_existing_item_codes()
    logger.info(f"除外item_code: {len(exclude)}件")

    all_items = []
    genre_counts = {}

    # 【Codex 30回目 #3 fix】overall hard cap (過剰取得防止 API 負荷+pool 肥大化)
    # 高 weight ジャンルだけで target * 2 を超えないよう全体に上限
    OVERALL_HARD_CAP = target * 2

    # weight 降順で iterate
    for genre, weight in sorted_genres:
        keywords = genres[genre]
        per_genre = per_genre_plan[genre]

        # Codex 31回目 #6 fix: len(keywords)==0 ガード (ZeroDivisionError 防止)
        if not keywords:
            logger.warning(f"[{genre}] keywords 空 → skip (config 確認推奨)")
            genre_counts[genre] = 0
            continue

        # 【修正】高 weight (>=2.0) は target 超過しても取得 (CEO 5/20 配分絶対遵守)
        # 低 weight (<2.0) は target 達成済みで skip
        if len(all_items) >= target and weight < 2.0:
            logger.info(f"[{genre}] スキップ (低 weight={weight}, target {target}件 達成済 {len(all_items)}件)")
            genre_counts[genre] = 0
            continue
        # Codex 30回目 #3: overall hard cap (高 weight でも 越えたら停止)
        if len(all_items) >= OVERALL_HARD_CAP:
            logger.info(f"[{genre}] スキップ (overall hard cap {OVERALL_HARD_CAP} 到達 {len(all_items)}件)")
            genre_counts[genre] = 0
            continue

        logger.info(f"[{genre}] 取得開始 (weight={weight}, per_genre={per_genre}, "
                    f"キーワード{len(keywords)}個, 現在累計{len(all_items)})")
        # Codex 34回目 #2 fix: max_per_keyword は per_genre ベースで計算 (+30 固定 buffer 廃止).
        # ceil((per_genre*2) / len(keywords)) で 取得目標が per_genre*2 に収まる API 負荷.
        # min 15 (最低 1 ページ ~= 30 件) はキーワード探索の取得多様性確保.
        import math as _math2
        max_per_kw = max(15, _math2.ceil((per_genre * 2) / len(keywords)))
        items = fetch_genre_items(genre, keywords,
                                  max_per_keyword=max_per_kw,
                                  exclude_codes=exclude)
        # weight 配分に従って cap (高 weight が pool を過剰占有しないよう per_genre で打ち切り)
        # ただし NORMAL_MIN は確保する (取得 0 だと監査済データなし)
        if len(items) > per_genre * 2:
            items = items[:per_genre * 2]  # 倍までは許容 (監査 fail で 半減見込み)
        # 取得済みコードを除外セットに追加（ジャンル間重複防止）
        for item in items:
            exclude.add(item["item_code"])

        all_items.extend(items)
        genre_counts[genre] = len(items)
        logger.info(f"[{genre}] 取得完了: {len(items)}件 (累計 {len(all_items)} / weight={weight})")

    # Codex 32回目 #5 fix: ループ末尾でも overall hard cap 検証 (途中で超過した場合 truncate)
    if len(all_items) > OVERALL_HARD_CAP:
        logger.info(
            f"[truncate] all_items {len(all_items)} > OVERALL_HARD_CAP {OVERALL_HARD_CAP} "
            f"→ 末尾 truncate"
        )
        # 高 weight (先頭) を優先保持・末尾切り捨て
        all_items = all_items[:OVERALL_HARD_CAP]

    # 構造化ログ (Codex #9 反映)
    _write_fetch_run_log(run_id, {
        "target": target,
        "weights": dict(sorted_genres),
        "per_genre_plan": per_genre_plan,
        "genre_counts": genre_counts,
        "total_fetched": len(all_items),
        "allowlist_genres": allowlist_genres,
    })

    logger.info(f"=== 全ジャンル取得完了: {len(all_items)}件 ===")
    logger.info(f"ジャンル分布: {genre_counts}")

    return all_items


def _write_fetch_run_log(run_id: str, data: dict) -> None:
    """fetch run の構造化ログ書込 (Codex #9 反映: JSONL).

    Module top-level で json, os, datetime は import 済 (line 9-17 で確認可能).

    Codex 30回目 #2: 行 atomic 書込 (1 entry 1 line) + flush で破損リスク低減.
    並行プロセスが同 run_id を書く設計ではない (run_id は uuid 込み) のでロック不要.

    Codex 31回目 #4 反映: fsync は env RUN_LOG_FSYNC=1 時のみ (default は flush のみ).
    I/O 負荷+ディスク寿命 vs 障害復旧 のバランス CEO 判断.
    """
    # Defensive import (Codex 33回目 #1: module top で import 済だが念のため)
    import json as _json
    import os as _os
    from datetime import datetime as _dt
    if not run_id:
        return
    try:
        log_dir = config.DATA_DIR / "run_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{run_id}.jsonl"
        entry = {
            "ts": _dt.now().isoformat(timespec="seconds"),
            "type": "fetch_all_genres_complete",
            **data,
        }
        # 1 行に固める + flush で部分書込破損リスク回避
        line = _json.dumps(entry, ensure_ascii=False) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            if _os.environ.get("RUN_LOG_FSYNC") == "1":
                try:
                    _os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass
    except Exception as e:
        logger.warning(f"run_log write failed: {e}")


def cleanup_old_run_logs(retention_days: int = 7) -> int:
    """run_logs ディレクトリ内の retention_days より古い .jsonl を削除.

    Codex 31回目 #4 反映: 永遠膨張防止. CEO ops cron で 1日1回実行推奨.

    Returns: 削除した file count
    """
    import time
    log_dir = config.DATA_DIR / "run_logs"
    if not log_dir.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for f in log_dir.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception as e:
            logger.warning(f"cleanup run_log {f.name} failed: {e}")
    if removed:
        logger.info(f"[cleanup] {removed} old run_logs removed (>{retention_days}d)")
    return removed


def replenish_pool(target_min: int = None, target_max: int = None,
                   dry_run: bool = False,
                   allowlist_genres: list[str] | None = None,
                   run_id: str = "") -> dict:
    """商品プールを補充する（メインエントリ）

    フロー: API取得 → audit → pass のみ source_items に追加

    Args:
        target_min: 最低維持件数（デフォルト: config.POOL_MIN）
        target_max: 最大件数（デフォルト: config.POOL_MAX）
        dry_run: Trueの場合は取得のみ（保存しない）
        allowlist_genres: 指定があれば そのジャンルのみ取得 (CEO 5/20 緊急止血)
            None で env POOL_ALLOWLIST_GENRES (CSV) を fallback 参照
        run_id: 実行 ID (構造化ログ用)

    Returns:
        dict: {fetched, audited_pass, audited_review, audited_fail,
               new_added, pruned, total_pool, genre_distribution}
    """
    t_min = target_min or config.POOL_MIN
    t_max = target_max or config.POOL_MAX

    # run_id 自動生成 (caller が指定しなかった場合)
    if not run_id:
        import uuid
        run_id = f"replenish_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # allowlist env fallback (Codex #1 反映: CEO 緊急止血 48h baby/food/kids only mode)
    if allowlist_genres is None:
        env_allow = os.environ.get("POOL_ALLOWLIST_GENRES", "").strip()
        if env_allow:
            allowlist_genres = [g.strip() for g in env_allow.split(",") if g.strip()]
            logger.info(f"[replenish] env POOL_ALLOWLIST_GENRES={allowlist_genres}")

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
    raw_items = fetch_all_genres(target_total=needed,
                                  allowlist_genres=allowlist_genres,
                                  run_id=run_id)
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

    # Codex 32回目 #2 fix: run_logs cleanup を replenish_pool 終端で実行
    # retention_days は env で override 可能
    try:
        retention = int(os.environ.get("RUN_LOG_RETENTION_DAYS", "7"))
        cleanup_old_run_logs(retention_days=retention)
    except Exception as _e:
        logger.warning(f"cleanup_old_run_logs failed (non-fatal): {_e}")

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
