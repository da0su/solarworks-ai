"""ROOM BOT v6.1 - いいねBOT

楽天ROOMのフィードを巡回し、投稿にいいねする。
他ユーザーからのプロフィール訪問・フォロー・投稿露出の導線を増やす。

使い方:
  python run.py like                  # デフォルト件数でいいね
  python run.py like --limit 20       # 20件だけいいね

安全設計:
  - 未ログイン → 即終了
  - 連続失敗5件 → 自動停止
  - いいね済み → スキップ（連続失敗にカウントしない）
  - 人間らしい間隔（2-8秒 + たまに10-30秒休憩）

v6.1: 診断結果(2026-03-16)に基づくDOM対応
  - いいねボタン = <a class="icon-like right"> (buttonではない)
  - いいね済み = isLiked クラス
  - AngularJS: ng-click="like(item)"
  - 投稿カード = <div class="item-preview">
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from executor.browser_manager import BrowserManager
from logger.logger import setup_logger

logger = setup_logger()


class LikeExecutor:
    """楽天ROOMフィードでいいねを実行するクラス"""

    def __init__(self, limit: int | None = None, source: str = "daily_plan"):
        """
        Args:
            limit: いいね件数の上限（Noneならconfig値でランダム決定）
            source: "daily_plan" | "room_plus"（ログ区別用）
        """
        self.limit = limit or config.get_daily_like_target()
        self.source = source
        self.liked_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.consecutive_failures = 0
        self.liked_urls: set[str] = set()
        self._load_history()

    def _load_history(self) -> None:
        """いいね履歴を読み込む（重複防止）"""
        if config.LIKE_HISTORY_PATH.exists():
            try:
                with open(config.LIKE_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
                # 直近7日分のURLだけ保持
                cutoff = datetime.now().timestamp() - (7 * 86400)
                for entry in history:
                    ts = entry.get("timestamp", 0)
                    if ts > cutoff:
                        url = entry.get("url", "")
                        if url:
                            self.liked_urls.add(url)
                logger.info(f"いいね履歴: {len(self.liked_urls)}件（直近7日）")
            except Exception as e:
                logger.warning(f"いいね履歴読み込みエラー: {e}")

    def _save_history_entry(self, url: str) -> None:
        """いいね履歴に1件追加"""
        history = []
        if config.LIKE_HISTORY_PATH.exists():
            try:
                with open(config.LIKE_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append({
            "url": url,
            "timestamp": datetime.now().timestamp(),
            "liked_at": datetime.now().isoformat(),
            "source": self.source,
        })

        # 直近7日分だけ保持（ファイル肥大化防止）
        cutoff = datetime.now().timestamp() - (7 * 86400)
        history = [h for h in history if h.get("timestamp", 0) > cutoff]

        config.LIKE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LIKE_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def run(self) -> dict:
        """いいねセッションを実行する"""
        logger.info("=" * 60)
        logger.info(f"=== いいねBOT 開始 (目標: {self.limit}件) ===")
        logger.info("=" * 60)

        bm = BrowserManager()
        abort_reason = None

        try:
            bm.start()

            # ログイン確認
            login_status = bm.check_login_status()
            if not login_status["logged_in"]:
                abort_reason = f"未ログイン ({login_status['method']})"
                logger.error(abort_reason)
                return self._make_summary(True, abort_reason)

            logger.info(f"ログイン確認OK ({login_status['method']})")

            # フィードページを巡回していいね
            for feed_url in config.LIKE_FEED_URLS:
                if self.liked_count >= self.limit:
                    break
                if abort_reason:
                    break

                logger.info(f"--- フィード巡回: {feed_url} ---")
                abort_reason = self._like_feed(bm, feed_url)

            if not abort_reason and self.liked_count < self.limit:
                logger.info("全フィード巡回完了。追加のスクロールを試行...")
                # 最初のフィードに戻って追加スクロール
                abort_reason = self._like_feed(
                    bm, config.LIKE_FEED_URLS[0], extra_scroll=True
                )

        except Exception as e:
            abort_reason = f"予期しないエラー: {e}"
            logger.error(abort_reason)
        finally:
            bm.stop()

        return self._make_summary(abort_reason is not None, abort_reason)

    def _like_feed(self, bm: BrowserManager, feed_url: str,
                   extra_scroll: bool = False) -> str | None:
        """フィードページでいいねを実行する

        Returns:
            中断理由（正常完了ならNone）
        """
        page = bm.page

        try:
            page.goto(feed_url, wait_until="domcontentloaded")
            self._human_delay(3.0, 5.0)
        except Exception as e:
            logger.error(f"フィード遷移エラー: {e}")
            return None  # このフィードをスキップして次へ

        # 404チェック
        try:
            el_404 = page.locator("text=404").first
            if el_404.is_visible(timeout=1000):
                logger.error(f"404ページ検出: {page.url}")
                return None  # このフィードをスキップ
        except Exception:
            pass

        # ログインリダイレクトチェック
        if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
            return "ログインページにリダイレクトされました"

        logger.info(f"フィードページ表示: {page.url}")
        bm.take_screenshot("like_feed_start")

        # AngularJSのレンダリング待機
        self._wait_for_angular(page)

        # スクロールしながらいいねボタンを探す
        max_scroll_attempts = 15 if extra_scroll else 8
        scroll_count = 0

        while self.liked_count < self.limit and scroll_count < max_scroll_attempts:
            # 連続失敗チェック
            if self.consecutive_failures >= config.LIKE_MAX_CONSECUTIVE_FAILURES:
                return f"{config.LIKE_MAX_CONSECUTIVE_FAILURES}件連続失敗で自動停止"

            # いいねボタンを探す
            like_result = self._find_and_click_likes(page)

            if like_result == "no_buttons":
                # ボタンが見つからない → スクロール
                logger.debug("いいねボタンなし → スクロール")
            elif like_result == "error":
                # エラー
                pass

            # ページをスクロール
            scroll_count += 1
            page.evaluate("window.scrollBy(0, 600)")
            self._human_delay(1.5, 3.0)

            # たまに長めのスクロール待機
            if scroll_count % 3 == 0:
                self._human_delay(2.0, 5.0)

        return None

    def _wait_for_angular(self, page: Page, timeout: int = 5000) -> None:
        """AngularJSのレンダリング完了を待機"""
        try:
            page.wait_for_function(
                """() => {
                    if (window.angular) {
                        var injector = angular.element(document.body).injector();
                        if (injector) {
                            var $http = injector.get('$http');
                            return $http.pendingRequests.length === 0;
                        }
                    }
                    return true;
                }""",
                timeout=timeout
            )
            logger.debug("AngularJS レンダリング完了")
        except Exception:
            logger.debug("AngularJS 待機タイムアウト（続行）")

    def _find_and_click_likes(self, page: Page) -> str:
        """ページ上のいいねボタンを探してクリックする

        楽天ROOMの実DOM構造（2026-03-16 診断結果）:
          いいねボタン: <a class="icon-like right" ng-click="like(item)">いいね</a>
          いいね済み:   class に isLiked が追加される
          無効状態:     class に isDisabled が追加される
          いいねカウント: <li class="icon-like likes ng-binding">N</li>

        Returns:
            "clicked" | "no_buttons" | "error"
        """
        # 楽天ROOM実DOMに基づくセレクタ（優先順位順）
        like_selectors = [
            # ① メインセレクタ: <a class="icon-like right"> で未いいね
            'a.icon-like.right:not(.isLiked):not(.isDisabled):not(.waiting)',
            # ② ng-click属性ベース
            'a[ng-click="like(item)"]:not(.isLiked):not(.isDisabled)',
            # ③ テキスト「いいね」を含む icon-like 要素
            'a.icon-like:has-text("いいね"):not(.isLiked)',
            # ④ フォールバック: class に icon-like を含む <a> 要素
            'a[class*="icon-like"][class*="right"]:not([class*="isLiked"])',
        ]

        clicked_any = False

        for selector in like_selectors:
            try:
                buttons = page.locator(selector)
                count = buttons.count()

                if count == 0:
                    continue

                logger.info(f"いいねボタン候補: {count}件 ({selector})")

                for i in range(min(count, 5)):  # 一度に最大5件
                    if self.liked_count >= self.limit:
                        return "clicked"

                    try:
                        btn = buttons.nth(i)
                        if not btn.is_visible(timeout=1000):
                            continue

                        # デバッグ: クリック前に要素情報をログ出力
                        self._log_element_info(page, btn, i)

                        # ボタンの親要素のURLを取得（重複チェック用）
                        item_url = self._get_item_url_near(page, btn)
                        if item_url and item_url in self.liked_urls:
                            self.skipped_count += 1
                            logger.debug(f"スキップ（いいね済み）: {item_url[:50]}")
                            continue

                        # クリック前のURL記録（404遷移検出用）
                        url_before = page.url

                        # いいねクリック
                        btn.click(timeout=3000)

                        # クリック後の状態確認
                        self._human_delay(0.5, 1.0)
                        url_after = page.url

                        # 404 / 予期しない遷移検出
                        if url_after != url_before and "room.rakuten.co.jp" not in url_after:
                            logger.warning(f"クリック後に予期しない遷移: {url_before} → {url_after}")
                            page.go_back()
                            self._human_delay(1.0, 2.0)
                            self.failed_count += 1
                            self.consecutive_failures += 1
                            continue

                        # 成功判定: class変化 or カウント変化
                        like_confirmed = self._verify_like_success(btn)
                        if like_confirmed:
                            logger.debug("  成功判定: isLiked クラス確認")
                        else:
                            logger.debug("  成功判定: クラス変化未確認（クリック自体は成功）")

                        self.liked_count += 1
                        self.consecutive_failures = 0
                        clicked_any = True

                        if item_url:
                            self.liked_urls.add(item_url)
                            self._save_history_entry(item_url)

                        logger.info(
                            f"[{self.liked_count}/{self.limit}] いいね成功"
                            + (f" ({item_url[:40]})" if item_url else "")
                        )

                        # 人間らしい間隔
                        self._like_interval()

                    except Exception as e:
                        self.failed_count += 1
                        self.consecutive_failures += 1
                        logger.warning(f"いいねクリック失敗: {e}")

                if clicked_any:
                    return "clicked"

            except Exception as e:
                logger.debug(f"セレクタ {selector} 検索エラー: {e}")
                continue

        return "no_buttons" if not clicked_any else "clicked"

    def _verify_like_success(self, element) -> bool:
        """クリック後のいいね成功を検証する

        成功判定:
          1. 要素の class に isLiked が追加されたか
          2. AngularJS の scope で item.isLiked が true か
        """
        try:
            result = element.evaluate("""el => {
                var cls = el.className || '';
                var hasIsLiked = cls.indexOf('isLiked') !== -1;

                // AngularJS scope から確認
                var scopeLiked = false;
                try {
                    var scope = angular.element(el).scope();
                    if (scope && scope.item) {
                        scopeLiked = !!scope.item.isLiked;
                    }
                } catch(e) {}

                return { classLiked: hasIsLiked, scopeLiked: scopeLiked };
            }""")
            return result.get("classLiked", False) or result.get("scopeLiked", False)
        except Exception:
            return False

    def _log_element_info(self, page: Page, element, index: int) -> None:
        """クリック前の要素情報をデバッグログに出力"""
        try:
            info = element.evaluate("""el => ({
                tag: el.tagName,
                className: el.className,
                text: (el.textContent || '').trim().substring(0, 30),
                href: el.href || '',
                outerHTML: el.outerHTML.substring(0, 150),
            })""")
            logger.debug(
                f"  クリック対象[{index}]: <{info['tag']} class='{info['className']}'> "
                f"text='{info['text']}' href='{info['href'][:50]}'"
            )
        except Exception:
            pass

    def _get_item_url_near(self, page: Page, button) -> str:
        """いいねボタン付近の投稿URLを取得する

        楽天ROOM DOM構造:
          <div class="item-preview">  ← 投稿カード
            <a href="/xxx/items/yyy">  ← 投稿リンク
            ...
            <a class="icon-like right" ng-click="like(item)">いいね</a>
          </div>
        """
        try:
            # 方法1: 親の item-preview div からリンクを取得
            parent = button.locator(
                "xpath=ancestor::div[contains(@class, 'item-preview')]"
            ).first
            if parent.count() > 0:
                link = parent.locator("a[href*='/items/']").first
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                    if href:
                        return href
        except Exception:
            pass

        try:
            # 方法2: 親の ul/li 構造からリンクを取得
            parent_li = button.locator(
                "xpath=ancestor::ul"
            ).first
            if parent_li.count() > 0:
                link = parent_li.locator("a[href*='room.rakuten.co.jp']").first
                if link.count() > 0:
                    return link.get_attribute("href") or ""
        except Exception:
            pass

        # 方法3: ng-click のスコープから取得（フォールバック）
        try:
            item_id = button.evaluate("""el => {
                var scope = angular.element(el).scope();
                if (scope && scope.item) {
                    return scope.item.id || scope.item.itemId || '';
                }
                return '';
            }""")
            if item_id:
                return f"https://room.rakuten.co.jp/items/{item_id}"
        except Exception:
            pass

        return ""

    def _like_interval(self) -> None:
        """いいね後の間隔（人間らしい揺らぎ）"""
        # 通常間隔
        interval = config.get_like_interval()

        # 一定件数ごとに長めの休憩
        rest_every = config.get_like_rest_interval()
        if self.liked_count % rest_every == 0 and self.liked_count > 0:
            rest = config.get_like_rest_duration()
            logger.info(f"  休憩: {rest:.0f}秒 ({self.liked_count}件完了)")
            time.sleep(rest)
            return

        time.sleep(interval)

    def _human_delay(self, min_sec: float, max_sec: float) -> None:
        """人間的なランダム遅延"""
        time.sleep(random.uniform(min_sec, max_sec))

    def _make_summary(self, aborted: bool = False, reason: str = None) -> dict:
        """実行結果サマリーを生成"""
        summary = {
            "action": "like",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "target": self.limit,
            "liked": self.liked_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "aborted": aborted,
            "reason": reason,
            "finished_at": datetime.now().isoformat(),
        }

        print(f"\n{'=' * 60}")
        if aborted:
            print(f"いいねBOT 中断: {reason}")
        else:
            print("いいねBOT 完了")
        print(f"{'=' * 60}")
        print(f"  目標:     {self.limit}件")
        print(f"  成功:     {self.liked_count}件")
        print(f"  スキップ: {self.skipped_count}件")
        print(f"  失敗:     {self.failed_count}件")
        print(f"{'=' * 60}")

        logger.info(
            f"いいねBOT結果: liked={self.liked_count} "
            f"skipped={self.skipped_count} failed={self.failed_count}"
            + (f" abort={reason}" if aborted else "")
        )

        return summary
