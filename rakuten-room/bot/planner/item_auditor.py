"""ROOM BOT v5.0 - 商品監査レイヤー

API取得した商品候補を検査し、pass / review / fail を判定する。
passした商品だけがsource_itemsに採用される。

監査ルール:
1. item_url が実在する（HTTP 200）
2. 商品ページに正常到達（リダイレクト先が商品ページ）
3. item_code と URL が整合する
4. title が商品ページ見出しと大きくズレていない
5. バナー・画像・装飾要素の誤認を除外
6. コメント生成に必要な情報が揃っている
7. 怪しいものは review または fail
"""

import json
import re
import sys
import time
import random
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logger.logger import setup_logger

logger = setup_logger()

AUDIT_RESULTS_PATH = config.AUDIT_RESULTS_PATH

# --- 監査 NGパターン ---

# タイトルにこれらが含まれる → fail（商品本体ではない可能性が高い）
TITLE_FAIL_PATTERNS = [
    r"クーポン",
    r"ポイント\d+倍",
    r"セール開催中",
    r"バナー",
    r"お買い物マラソン",
    r"スーパーSALE",
    r"エントリー",
    r"まとめ買い.*クーポン",
    r"LINE友だち",
    r"メルマガ",
    r"店内全品",
    r"全商品対象",
    r"\d+%OFF.*クーポン",
    r"プレミアム会員",
    r"レビュー.*特典",
    r"おまけ付き",
]

# タイトルにこれらが含まれる → pass扱い（CEO判断 2026-03-20: 投稿OK）
# 旧review → 「訳あり」「福袋」「お試し」等は ROOM で人気が出やすいため pass
TITLE_REVIEW_PATTERNS = []

# URLパターン: 楽天市場商品ページとして有効か
VALID_URL_PATTERNS = [
    r"^https?://item\.rakuten\.co\.jp/[^/]+/[^/]+",
    r"^https?://books\.rakuten\.co\.jp/rb/\d+",
]

# 商品ページではないURL → fail
INVALID_URL_PATTERNS = [
    r"rakuten\.co\.jp/gold/",        # ショップページ
    r"event\.rakuten\.co\.jp",        # イベントページ
    r"coupon\.rakuten\.co\.jp",       # クーポンページ
    r"point\.rakuten\.co\.jp",        # ポイントページ
    r"search\.rakuten\.co\.jp",       # 検索結果
]


def audit_single(item: dict, check_url: bool = True) -> dict:
    """1商品を監査する

    Args:
        item: source_items形式の商品dict
        check_url: URLの実在チェックを行うか（API負荷軽減用）

    Returns:
        dict: {
            "item_code": str,
            "title": str,
            "item_url": str,
            "genre": str,
            "audit_result": "pass" | "review" | "fail",
            "fail_reason": str,
            "checks": dict,  # 各チェックの詳細
            "checked_at": str,
        }
    """
    item_code = item.get("item_code", "")
    title = item.get("title", "")
    url = item.get("url", "")
    genre = item.get("genre", "")
    price = item.get("price", 0)
    score = item.get("score", 0)

    result = {
        "item_code": item_code,
        "title": title[:80],
        "item_url": url,
        "genre": genre,
        "audit_result": "pass",
        "fail_reason": "",
        "checks": {},
        "checked_at": datetime.now().isoformat(),
    }

    # --- Check 1: 基本フィールド存在 ---
    if not item_code or not url or not title:
        result["audit_result"] = "fail"
        result["fail_reason"] = "必須フィールド不足(item_code/url/title)"
        result["checks"]["fields"] = "fail"
        return result
    result["checks"]["fields"] = "pass"

    # --- Check 2: URL形式チェック ---
    url_valid = any(re.match(p, url) for p in VALID_URL_PATTERNS)
    url_invalid = any(re.search(p, url) for p in INVALID_URL_PATTERNS)

    if url_invalid:
        result["audit_result"] = "fail"
        result["fail_reason"] = f"非商品URL: {url[:60]}"
        result["checks"]["url_format"] = "fail"
        return result
    if not url_valid:
        result["audit_result"] = "review"
        result["fail_reason"] = f"URL形式不明: {url[:60]}"
        result["checks"]["url_format"] = "review"
        return result
    result["checks"]["url_format"] = "pass"

    # --- Check 3: item_code と URL の整合 ---
    from planner.product_fetcher import _extract_item_code
    url_code = _extract_item_code(url)
    # APIからのitem_codeとURL由来のitem_codeが一致するか
    if item_code and url_code:
        # shopCode部分が一致すればOK（itemCode部分は形式が異なる場合あり）
        ic_shop = item_code.split(":")[0] if ":" in item_code else ""
        uc_shop = url_code.split(":")[0] if ":" in url_code else ""
        if ic_shop and uc_shop and ic_shop != uc_shop:
            result["audit_result"] = "review"
            result["fail_reason"] = f"item_code不整合: {item_code} vs URL:{url_code}"
            result["checks"]["code_match"] = "review"
            return result
    result["checks"]["code_match"] = "pass"

    # --- Check 4: タイトル品質チェック ---
    # failパターン
    for pattern in TITLE_FAIL_PATTERNS:
        if re.search(pattern, title):
            result["audit_result"] = "fail"
            result["fail_reason"] = f"非商品タイトル: '{pattern}' にマッチ"
            result["checks"]["title_quality"] = "fail"
            return result
    # reviewパターン
    for pattern in TITLE_REVIEW_PATTERNS:
        if re.search(pattern, title):
            result["audit_result"] = "review"
            result["fail_reason"] = f"要確認タイトル: '{pattern}' にマッチ"
            result["checks"]["title_quality"] = "review"
            return result

    # タイトルが極端に短い/長い
    if len(title) < 5:
        result["audit_result"] = "fail"
        result["fail_reason"] = f"タイトル短すぎ({len(title)}文字)"
        result["checks"]["title_quality"] = "fail"
        return result
    result["checks"]["title_quality"] = "pass"

    # --- Check 5: 価格チェック（CEO判断 2026-03-20: 異常値はfail） ---
    if price and (price < 100 or price > 500000):
        result["audit_result"] = "fail"
        result["fail_reason"] = f"価格異常: {price:,}円"
        result["checks"]["price"] = "fail"
        return result
    result["checks"]["price"] = "pass"

    # --- Check 6: スコアチェック（CEO判断 2026-03-20: 低スコアはfail） ---
    if score < 50:
        result["audit_result"] = "fail"
        result["fail_reason"] = f"低スコア: {score}点"
        result["checks"]["score"] = "fail"
        return result
    result["checks"]["score"] = "pass"

    # --- Check 7: URL実在チェック（オプション） ---
    if check_url:
        url_check = _check_url_exists(url)
        result["checks"]["url_exists"] = url_check["status"]
        if url_check["status"] == "fail":
            result["audit_result"] = "fail"
            result["fail_reason"] = f"URL到達不可: {url_check['reason']}"
            return result
        elif url_check["status"] == "review":
            result["audit_result"] = "review"
            result["fail_reason"] = f"URL要確認: {url_check['reason']}"
            return result
    else:
        result["checks"]["url_exists"] = "skipped"

    # --- Check 8: ジャンル整合チェック ---
    if not genre or genre == "unknown":
        result["checks"]["genre"] = "review"
        # ジャンル不明でもpassにはする（comment_generatorで自動判定される）
    else:
        result["checks"]["genre"] = "pass"

    return result


def _check_url_exists(url: str) -> dict:
    """URLの実在チェック（HEADリクエスト）

    Returns:
        dict: {"status": "pass"|"review"|"fail", "reason": str, "http_code": int}
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36")
        with urllib.request.urlopen(req, timeout=10) as res:
            code = res.getcode()
            final_url = res.geturl()

            # リダイレクト先が商品ページでない場合
            if "error" in final_url or "404" in final_url:
                return {"status": "fail", "reason": "エラーページにリダイレクト", "http_code": code}
            if "search.rakuten" in final_url:
                return {"status": "fail", "reason": "検索ページにリダイレクト", "http_code": code}

            return {"status": "pass", "reason": f"HTTP {code}", "http_code": code}

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "fail", "reason": "404 Not Found", "http_code": 404}
        if e.code == 403:
            # CEO判断 2026-03-20: 403はfail扱い
            return {"status": "fail", "reason": "403 Forbidden (HEAD)", "http_code": 403}
        return {"status": "fail", "reason": f"HTTP {e.code}", "http_code": e.code}
    except Exception as e:
        return {"status": "review", "reason": f"接続エラー: {str(e)[:50]}", "http_code": 0}


def audit_items(items: list[dict], check_url: bool = True,
                url_check_sample_rate: float = 0.3) -> dict:
    """複数商品を一括監査

    Args:
        items: source_items形式の商品リスト
        check_url: URLチェックを行うか
        url_check_sample_rate: URLチェックのサンプリング率（0.3 = 30%）

    Returns:
        dict: {
            "passed": list[dict],   # passした商品（元のitem dict）
            "reviewed": list[dict], # review判定の商品
            "failed": list[dict],   # fail判定の商品
            "audit_log": list[dict], # 全監査結果
            "stats": dict,          # 集計
        }
    """
    passed = []
    reviewed = []
    failed = []
    audit_log = []

    total = len(items)
    logger.info(f"=== 商品監査開始: {total}件 (URLチェック率: {url_check_sample_rate*100:.0f}%) ===")

    for i, item in enumerate(items):
        # URLチェックはサンプリング（全件やるとAPI負荷大）
        do_url_check = check_url and (random.random() < url_check_sample_rate)

        result = audit_single(item, check_url=do_url_check)
        audit_log.append(result)

        if result["audit_result"] == "pass":
            passed.append(item)
        elif result["audit_result"] == "review":
            reviewed.append(item)
        else:
            failed.append(item)

        # URL チェック時は負荷軽減
        if do_url_check:
            time.sleep(random.uniform(0.3, 0.8))

        # 進捗ログ（50件ごと）
        if (i + 1) % 50 == 0:
            logger.info(f"  監査進捗: {i+1}/{total} "
                        f"(pass={len(passed)}, review={len(reviewed)}, fail={len(failed)})")

    stats = {
        "total": total,
        "pass": len(passed),
        "review": len(reviewed),
        "fail": len(failed),
        "pass_rate": round(len(passed) / total * 100, 1) if total > 0 else 0,
    }

    logger.info(f"=== 監査完了: pass={stats['pass']} review={stats['review']} "
                f"fail={stats['fail']} (pass率: {stats['pass_rate']}%) ===")

    # 監査結果を保存
    _save_audit_log(audit_log)

    return {
        "passed": passed,
        "reviewed": reviewed,
        "failed": failed,
        "audit_log": audit_log,
        "stats": stats,
    }


def _save_audit_log(audit_log: list[dict]):
    """監査結果をファイルに保存（追記）"""
    existing = []
    if AUDIT_RESULTS_PATH.exists():
        try:
            with open(AUDIT_RESULTS_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    # 直近5000件に制限（ファイル肥大化防止）
    combined = existing + audit_log
    if len(combined) > 5000:
        combined = combined[-5000:]

    import os
    tmp_path = AUDIT_RESULTS_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp_path), str(AUDIT_RESULTS_PATH))


def format_audit_report(result: dict) -> str:
    """監査結果を人間が読める形式で返す"""
    stats = result["stats"]
    lines = [
        f"\n{'=' * 60}",
        f"商品監査レポート",
        f"{'=' * 60}",
        f"  検査対象:    {stats['total']}件",
        f"  pass:        {stats['pass']}件",
        f"  review:      {stats['review']}件",
        f"  fail:        {stats['fail']}件",
        f"  pass率:      {stats['pass_rate']}%",
    ]

    # fail理由の集計
    fail_reasons = {}
    for log in result["audit_log"]:
        if log["audit_result"] == "fail":
            reason = log.get("fail_reason", "不明")
            # 理由のカテゴリだけ取る
            key = reason.split(":")[0] if ":" in reason else reason
            fail_reasons[key] = fail_reasons.get(key, 0) + 1

    if fail_reasons:
        lines.append(f"\n  [fail理由]")
        for reason, count in sorted(fail_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"    {reason}: {count}件")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
