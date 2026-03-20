"""ROOM BOT v2 - キューベース投稿実行

SQLiteキューから queued のアイテムを取り出し、
既存の PostExecutor を使って投稿を実行する。
自動停止条件: ログイン切れ、連続失敗、セレクタ不一致

v2.1: スキップ補填 + ログ見える化 + レポート改善
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from planner.queue_manager import QueueManager, STATUS_POSTED, STATUS_FAILED, STATUS_SKIPPED
from executor.browser_manager import BrowserManager
from executor.post_executor import PostExecutor
from executor.comment_generator import detect_genre, generate_comment
from executor.post_scorer import score_and_regenerate
from logger.logger import setup_logger, save_post_result

POST_HISTORY_PATH = config.DATA_DIR / "post_history.json"


def _append_post_history(item_code: str, url: str, title: str,
                         genre: str, comment: str, score: int,
                         room_url: str = ""):
    """post_history.json に投稿記録を追加する"""
    history = []
    if POST_HISTORY_PATH.exists():
        try:
            with open(POST_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, Exception):
            history = []

    history.append({
        "item_code": item_code,
        "url": url,
        "genre": genre,
        "title": title,
        "opening": comment.split("\n")[0] if comment else "",
        "comment": comment[:200],
        "score": score,
        "room_url": room_url,
        "posted_at": datetime.now().isoformat(),
    })

    POST_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POST_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

logger = setup_logger()

# 自動停止閾値
MAX_CONSECUTIVE_FAILURES = 3
# ログイン切れ等の致命的エラー
FATAL_ERROR_TYPES = {"login_redirect", "mix_page_error"}
# 導線問題（skipped扱い、連続失敗にカウントしない）
SKIP_ERROR_TYPES = {"collect_not_supported", "product_page_error",
                    "no_share_button", "no_room_link"}

# 補填の安全上限（無限ループ防止）
MAX_BACKFILL_ATTEMPTS = 10


class QueueExecutor:
    """SQLiteキューから投稿を実行"""

    def __init__(self, queue_date: str = None, limit: int = None,
                 min_wait: float = None, max_wait: float = None):
        self.queue_date = queue_date or datetime.now().strftime("%Y-%m-%d")
        self.limit = limit
        self.min_wait = min_wait if min_wait is not None else config.POST_INTERVAL_MIN
        self.max_wait = max_wait if max_wait is not None else config.POST_INTERVAL_MAX

    def run(self) -> dict:
        """キューから投稿を実行（スキップ時は自動補填）"""
        start_time = time.time()
        qm = QueueManager()

        # 異常終了で running のまま残ったレコードを戻す
        reset_count = qm.reset_running(self.queue_date)
        if reset_count > 0:
            logger.info(f"前回異常終了の {reset_count}件 を queued に戻しました")

        # 実行対象件数を確認
        target_count = self.limit  # 目標成功件数
        pending_snapshot = qm.get_pending(self.queue_date)
        total_available = len(pending_snapshot)

        if total_available == 0:
            logger.info("実行対象が0件です")
            print("\n実行対象が0件です。")
            print("  → python run.py plan で計画を生成してください。")
            qm.close()
            return self._make_summary(0, 0, 0, 0, {}, 0, False, None)

        logger.info(f"=== キュー実行開始: {self.queue_date} (目標: {target_count or total_available}件成功) ===")
        logger.info(f"  待機キュー: {total_available}件 | 投稿間隔: {self.min_wait:.0f}〜{self.max_wait:.0f}秒")

        bm = BrowserManager()
        consecutive_failures = 0
        posted_count = 0
        failed_count = 0
        skipped_count = 0
        backfill_count = 0
        skip_reasons = {}
        abort_reason = None
        processed_ids = set()

        try:
            logger.info("[フェーズ] ブラウザ起動中...")
            bm.start()

            # ログイン確認
            logger.info("[フェーズ] ログイン確認中...")
            login_status = bm.check_login_status()
            if not login_status["logged_in"]:
                logger.error(f"未ログイン ({login_status['method']})")
                abort_reason = f"未ログイン ({login_status['method']})"
                elapsed = time.time() - start_time
                return self._make_summary(0, posted_count, failed_count,
                                          skipped_count, skip_reasons, elapsed,
                                          True, abort_reason)

            executor = PostExecutor(bm)

            while True:
                # 目標達成チェック
                if target_count and posted_count >= target_count:
                    logger.info(f"目標 {target_count}件 達成!")
                    break

                # 補填上限チェック
                if backfill_count > MAX_BACKFILL_ATTEMPTS:
                    logger.warning(f"補填上限 {MAX_BACKFILL_ATTEMPTS}件 に到達")
                    break

                # 連続失敗チェック
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    abort_reason = f"{MAX_CONSECUTIVE_FAILURES}件連続失敗で自動停止"
                    logger.error(abort_reason)
                    break

                # 原子的にキューから1件取得 (SELECT + UPDATE running を単一トランザクション)
                item = qm.acquire_next(self.queue_date, skip_ids=processed_ids)
                if item is None:
                    logger.info("キューの全アイテムを処理済み")
                    break

                queue_id = item["id"]
                processed_ids.add(queue_id)

                title = item["title"]
                url = item["item_url"]
                comment = item["comment"]
                display_num = posted_count + skipped_count + failed_count + 1
                target_label = f"/{target_count}" if target_count else ""

                logger.info(f"-" * 40)
                logger.info(f"[{display_num}{target_label}] {title[:50]}")

                # コメントが空なら自動生成
                if not comment or len(comment.strip()) < 10:
                    logger.info("[フェーズ] コメント自動生成中...")
                    genre = item.get("genre") or detect_genre(title, url)
                    comment, score_result = score_and_regenerate(
                        title, url, genre, generate_comment,
                    )
                    logger.info(f"  コメント生成完了 ({len(comment)}文字, score={score_result['score']})")

                # 投稿実行
                try:
                    logger.info("[フェーズ] 商品ページ遷移中...")
                    result = executor.execute(url, comment)

                    if result["success"]:
                        # 成功
                        room_url = result.get("room_url", "")
                        qm.mark_posted(queue_id, room_url=room_url,
                                       result_message="OK")
                        posted_count += 1
                        consecutive_failures = 0
                        logger.info(f"  ✅ 成功 (成功{posted_count}{target_label}) -> {room_url}")

                        # post_history.json にも記録
                        _append_post_history(
                            item_code=item.get("item_code", ""),
                            url=url, title=title,
                            genre=item.get("genre", ""),
                            comment=comment,
                            score=item.get("score", 0),
                            room_url=room_url,
                        )

                        # POST_LOG にも記録
                        save_post_result({
                            "post_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
                            "date": self.queue_date,
                            "title": title,
                            "product_url": url,
                            "comment": comment[:200],
                            "status": "posted",
                            "posted_at": datetime.now().isoformat(),
                            "room_url": room_url,
                            "method": "room_bot_v2_queue",
                        })

                    elif result.get("error_type") in SKIP_ERROR_TYPES:
                        # 導線問題 → skipped + 補填
                        reason = result.get("error_type", "unknown")
                        qm.mark_skipped(queue_id, reason=reason)
                        skipped_count += 1
                        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                        backfill_count += 1
                        logger.warning(f"  ⏭ スキップ({reason}) → 次の商品で補填 (補填{backfill_count}回目)")

                    elif result.get("error_type") in FATAL_ERROR_TYPES:
                        # 致命的エラー → 即停止
                        error_msg = result.get("error", "致命的エラー")
                        qm.mark_failed(queue_id,
                                       error_type=result.get("error_type", ""),
                                       result_message=error_msg)
                        abort_reason = f"致命的エラー: {error_msg}"
                        logger.error(abort_reason)
                        failed_count += 1
                        break

                    else:
                        # 通常失敗
                        error_msg = result.get("error", "不明なエラー")
                        qm.mark_failed(queue_id,
                                       error_type=result.get("error_type", ""),
                                       result_message=error_msg)
                        failed_count += 1
                        consecutive_failures += 1
                        logger.error(f"  ❌ 失敗: {error_msg}")
                        logger.warning(f"  連続失敗: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")

                except Exception as e:
                    error_msg = str(e)
                    qm.mark_failed(queue_id, error_type="exception",
                                   result_message=error_msg)
                    failed_count += 1
                    consecutive_failures += 1
                    logger.error(f"  ❌ 例外: {error_msg}")

                # 次の投稿まで待機（スキップ時は短く、成功時は通常間隔）
                has_next = (target_count and posted_count < target_count and
                            item_index < total_available) or \
                           (not target_count and item_index < total_available)

                if has_next and abort_reason is None:
                    if result and not result.get("success") and \
                       result.get("error_type") in SKIP_ERROR_TYPES:
                        interval = random.uniform(1.0, 2.0)
                        logger.info(f"[フェーズ] スキップ後即補填 ({interval:.0f}秒)")
                    else:
                        interval = random.uniform(self.min_wait, self.max_wait)
                        logger.info(f"[フェーズ] 次の投稿まで {interval:.0f}秒 待機中...")
                    time.sleep(interval)

        finally:
            logger.info("[フェーズ] ブラウザ終了中...")
            bm.stop()
            # デイリーサマリー更新
            qm.save_daily_summary(self.queue_date)
            qm.close()

        elapsed = time.time() - start_time
        return self._make_summary(posted_count + failed_count + skipped_count,
                                  posted_count, failed_count, skipped_count,
                                  skip_reasons, elapsed,
                                  abort_reason is not None, abort_reason)

    def _make_summary(self, total, posted, failed, skipped,
                      skip_reasons, elapsed,
                      aborted=False, reason=None) -> dict:
        summary = {
            "date": self.queue_date,
            "total": total,
            "posted": posted,
            "failed": failed,
            "skipped": skipped,
            "skip_reasons": skip_reasons,
            "elapsed_seconds": round(elapsed, 1),
            "aborted": aborted,
            "reason": reason,
        }

        target = self.limit
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        print(f"\n{'=' * 60}")
        if aborted:
            print(f"キュー実行中断: {reason}")
        else:
            print("キュー実行完了")
        print(f"{'=' * 60}")
        print(f"  対象日:     {self.queue_date}")
        if target:
            print(f"  目標:       {target}件")
        print(f"  処理合計:   {total}件")
        print(f"  成功:       {posted}件" +
              (f"  ({'目標達成!' if target and posted >= target else f'目標まであと{target - posted}件' if target else ''})" if target else ""))
        print(f"  失敗:       {failed}件")
        print(f"  スキップ:   {skipped}件")
        if skip_reasons:
            for reason_key, count in skip_reasons.items():
                print(f"    - {reason_key}: {count}件")
        print(f"  処理時間:   {minutes}分{seconds}秒")
        if posted > 0:
            avg = elapsed / posted
            print(f"  平均速度:   {avg:.1f}秒/件")
        print(f"{'=' * 60}")

        log_path = config.DATA_DIR / "logs" / f"{self.queue_date}.log"
        ss_path = config.DATA_DIR / "screenshots" / self.queue_date
        print(f"\nログ: {log_path}")
        print(f"スクリーンショット: {ss_path}/")

        return summary
