"""ROOM BOT v2 - バッチ投稿

JSONファイルから複数商品を読み込み、1件ずつ順次投稿する。
1件失敗しても次へ進む。3件連続失敗で自動停止。
最後に集計表示（success / failed / skipped）。
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from executor.browser_manager import BrowserManager
from executor.post_executor import PostExecutor
from executor.comment_generator import (
    detect_genre, generate_comment, DuplicateChecker,
)
from executor.post_scorer import score_comment, score_and_regenerate
from logger.logger import setup_logger, save_post_result

logger = setup_logger()

# 連続失敗で自動停止する閾値
MAX_CONSECUTIVE_FAILURES = 3


class BatchRunner:
    """JSONファイルから複数商品をバッチ投稿する"""

    def __init__(self, posts: list[dict], count: int | None = None,
                 min_wait: float | None = None, max_wait: float | None = None):
        """
        Args:
            posts: 投稿データのリスト [{title, url, image, comment}, ...]
            count: 投稿件数の上限（Noneなら全件）
            min_wait: 投稿間隔の最小秒数（Noneならconfig値）
            max_wait: 投稿間隔の最大秒数（Noneならconfig値）
        """
        self.posts = posts[:count] if count else posts
        self.results: list[dict] = []
        self.min_wait = min_wait if min_wait is not None else config.POST_INTERVAL_MIN
        self.max_wait = max_wait if max_wait is not None else config.POST_INTERVAL_MAX

    def run(self) -> dict:
        """バッチ投稿を実行し、集計結果を返す"""
        total = len(self.posts)
        logger.info("=" * 60)
        logger.info(f"バッチ投稿開始: {total}件")
        logger.info("=" * 60)

        bm = BrowserManager()
        consecutive_failures = 0

        try:
            bm.start()

            # ログイン確認（複合判定）
            login_status = bm.check_login_status()
            logger.info(f"ログイン判定: logged_in={login_status['logged_in']} method={login_status['method']}")
            logger.info(f"  URL: {login_status['url']}")
            logger.info(f"  Title: {login_status['title']}")

            if not login_status["logged_in"]:
                logger.error(f"未ログイン (method={login_status['method']})")
                if login_status["screenshot"]:
                    logger.error(f"  スクリーンショット: {login_status['screenshot']}")
                try:
                    from notifier import notify, NotifyType
                    notify(NotifyType.APPROVAL, detail=f"投稿BOT: 未ログイン ({login_status['method']})")
                except Exception:
                    pass
                return self._summary(aborted=True, reason=f"未ログイン ({login_status['method']})")

            executor = PostExecutor(bm)
            dup_checker = DuplicateChecker(config.DATA_DIR / "post_history.json")

            for i, post in enumerate(self.posts):
                num = i + 1
                title = post.get("title", "")
                url = post.get("url", "")
                comment = post.get("comment", "")

                logger.info("-" * 40)
                logger.info(f"[{num}/{total}] {title[:40]}")

                # --- 連続失敗チェック ---
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    reason = f"{MAX_CONSECUTIVE_FAILURES}件連続失敗のため自動停止"
                    logger.error(f"[{num}/{total}] {reason}")
                    return self._summary(aborted=True, reason=reason)

                # --- スキップ判定 ---
                skip_reason = self._check_skip(post)
                if skip_reason:
                    logger.warning(f"[{num}/{total}] スキップ: {skip_reason}")
                    self.results.append(self._record(post, status="skipped", error=skip_reason))
                    continue

                # --- 重複チェック ---
                genre = detect_genre(title, url, comment)
                dup_reason = dup_checker.check(url, genre)
                if dup_reason:
                    logger.warning(f"[{num}/{total}] スキップ(重複): {dup_reason}")
                    self.results.append(self._record(post, status="skipped", error=dup_reason))
                    continue

                # --- コメント自動生成（commentが空の場合） ---
                score_result = None
                if not comment or len(comment.strip()) < 10:
                    comment, score_result = score_and_regenerate(
                        title, url, genre, generate_comment,
                    )
                    logger.info(
                        f"[{num}/{total}] コメント自動生成 "
                        f"({len(comment)}文字, score={score_result['score']})"
                    )
                    if not score_result["pass"]:
                        logger.warning(
                            f"[{num}/{total}] スコア{score_result['score']}点 "
                            f"(閾値75未満だが最善を使用)"
                        )
                else:
                    # 既存コメントもスコアリング
                    score_result = score_comment(comment, genre)

                # --- 投稿実行 ---
                try:
                    result = executor.execute(url, comment)

                    if result["success"]:
                        logger.info(f"[{num}/{total}] 成功")
                        logger.info(f"  投稿URL: {url}")
                        logger.info(f"  ROOM URL: {result.get('room_url', 'N/A')}")
                        record = self._record(
                            post, status="posted",
                            room_url=result.get("room_url"),
                            comment=comment,
                            genre=genre,
                            score=score_result["score"] if score_result else 0,
                        )
                        save_post_result(record)
                        dup_checker.record(
                            url, genre, title, comment,
                            score=score_result["score"] if score_result else 0,
                        )
                        consecutive_failures = 0  # リセット
                    elif result.get("error_type") in (
                        "collect_not_supported", "product_page_error",
                        "no_share_button", "no_room_link",
                    ):
                        # 商品ページ/導線の問題 → skipped（連続失敗カウントに含めない）
                        skip_reason = result.get("error_type", "unknown")
                        logger.warning(f"[{num}/{total}] スキップ({skip_reason}): {url}")
                        record = self._record(
                            post, status="skipped",
                            error=skip_reason,
                        )
                    else:
                        error_msg = result.get("error", "不明なエラー")
                        logger.error(f"[{num}/{total}] 失敗: {error_msg}")
                        record = self._record(post, status="failed", error=error_msg)
                        consecutive_failures += 1
                        logger.warning(f"  連続失敗: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")

                    self.results.append(record)

                except Exception as e:
                    logger.error(f"[{num}/{total}] 例外: {e}")
                    self.results.append(self._record(post, status="failed", error=str(e)))
                    consecutive_failures += 1
                    logger.warning(f"  連続失敗: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")

                # 次の投稿まで待機（最後の1件以外）
                if num < total:
                    last_status = self.results[-1]["status"] if self.results else "unknown"
                    if last_status == "skipped":
                        # skipped → 短い待機（1〜3秒）
                        interval = random.uniform(1.0, 3.0)
                    else:
                        # 成功/失敗 → 通常待機
                        interval = random.uniform(self.min_wait, self.max_wait)
                    logger.info(f"次の投稿まで {interval:.0f}秒 待機...")
                    time.sleep(interval)

        finally:
            bm.stop()

        return self._summary()

    def _check_skip(self, post: dict) -> str | None:
        """スキップ理由を返す。Noneなら投稿可能。
        commentが空でもOK（自動生成するため）。
        """
        url = post.get("url", "")

        if not url:
            return "URLが空"
        if not url.startswith("http"):
            return f"URLが不正 ({url[:30]})"
        return None

    def _record(self, post: dict, status: str, error: str = None,
                room_url: str = None, comment: str = "",
                genre: str = "", score: int = 0) -> dict:
        """1件の投稿結果レコード（強化版）"""
        # コメントからタグを抽出
        tags = []
        if comment:
            for line in comment.split("\n"):
                if line.startswith("#"):
                    tags.append(line[1:])

        return {
            "post_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "title": post.get("title", ""),
            "product_url": post.get("url", ""),
            "image": post.get("image", ""),
            "review_text_preview": (comment or post.get("comment", ""))[:80],
            "comment": comment[:200] if comment else "",
            "genre": genre,
            "score": score,
            "tags": tags,
            "status": status,
            "error": error,
            "posted_at": datetime.now().isoformat(),
            "room_url": room_url,
            "method": "room_bot_v2_batch",
        }

    def _summary(self, aborted: bool = False, reason: str = None) -> dict:
        """集計結果を返す"""
        success = [r for r in self.results if r["status"] == "posted"]
        failed = [r for r in self.results if r["status"] == "failed"]
        skipped = [r for r in self.results if r["status"] == "skipped"]

        summary = {
            "total": len(self.posts),
            "success": len(success),
            "failed": len(failed),
            "skipped": len(skipped),
            "aborted": aborted,
            "reason": reason,
            "results": self.results,
        }

        # コンソール出力
        print("\n" + "=" * 60)
        if aborted:
            print(f"バッチ中断: {reason}")
        else:
            print("バッチ投稿完了")
        print("=" * 60)
        print(f"  合計:     {summary['total']}件")
        print(f"  成功:     {summary['success']}件")
        print(f"  失敗:     {summary['failed']}件")
        print(f"  スキップ: {summary['skipped']}件")

        if failed:
            print("\n--- 失敗リスト ---")
            for r in failed:
                print(f"  x {r['title'][:30]}  -> {r.get('error', '不明')}")

        if skipped:
            print("\n--- スキップリスト ---")
            for r in skipped:
                print(f"  - {r['title'][:30]}  -> {r.get('error', '不明')}")

        # collect非対応URL一覧
        collect_ng = [r for r in self.results if r.get("error") == "collect_not_supported"]
        if collect_ng:
            print(f"\n--- collect非対応URL ({len(collect_ng)}件) ---")
            for r in collect_ng:
                print(f"  ! {r['title'][:40]}")
                print(f"    {r['product_url']}")

        print("=" * 60)
        return summary


def load_posts_json(file_path: str) -> list[dict]:
    """JSONファイルから投稿データを読み込む

    対応形式:
      1. 配列: [{title, url, image, comment}, ...]
      2. オブジェクト: {"posts": [{...}, ...]}
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "posts" in data:
        return data["posts"]

    raise ValueError("JSONの形式が不正です。配列 or {\"posts\": [...]} で指定してください。")
