"""ROOM BOT v6.2 - フォローBOT（DOM修正版）

楽天ROOMのおすすめユーザー一覧を巡回し、フォローする。
フォローバックによるフォロワー増加・投稿露出拡大が目的。

使い方:
  python run.py follow                # デフォルト件数でフォロー
  python run.py follow --limit 20     # 20件だけフォロー

安全設計:
  - 未ログイン → 即終了
  - 連続失敗5件 → 自動停止
  - フォロー済み → スキップ（連続失敗にカウントしない）
  - 人間らしい間隔（1-3秒 + 10-15件ごとに10-30秒休憩）
  - 404ページ検出
  - AngularJS レンダリング待機

DOM構造（2026-03-16 診断結果に基づく）:
  フォローボタン:
    <div class="border-button ng-scope"
         ng-click="discover.toggleFollow(user)">
      <span class="follow icon-follow ng-scope">フォロー</span>
    </div>
  フォロー済み:
    spanのng-ifが切り替わり icon-follow → icon-following に変化
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

# フォロー履歴ファイル
FOLLOW_HISTORY_PATH = config.DATA_DIR / "follow_history.json"

# 連続失敗で停止する閾値
MAX_CONSECUTIVE_FAILURES = 5


class FollowExecutor:
    """楽天ROOMでフォローを実行するクラス"""

    def __init__(self, limit: int | None = None, source: str = "daily_plan"):
        """
        Args:
            limit: フォロー件数の上限（Noneなら初期安全値で30件）
            source: "daily_plan" | "room_plus"（ログ区別用）
        """
        self.limit = limit or 30
        self.source = source
        self.followed_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.consecutive_failures = 0
        self.followed_users: set[str] = set()
        self._load_history()

    def _load_history(self) -> None:
        """フォロー履歴を読み込む（重複防止）"""
        if FOLLOW_HISTORY_PATH.exists():
            try:
                with open(FOLLOW_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    user_id = entry.get("user_id", "")
                    if user_id:
                        self.followed_users.add(user_id)
                logger.info(f"フォロー履歴: {len(self.followed_users)}件")
            except Exception as e:
                logger.warning(f"フォロー履歴読み込みエラー: {e}")

    def _save_history_entry(self, user_id: str, user_name: str = "") -> None:
        """フォロー履歴に1件追加"""
        history = []
        if FOLLOW_HISTORY_PATH.exists():
            try:
                with open(FOLLOW_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append({
            "user_id": user_id,
            "user_name": user_name,
            "followed_at": datetime.now().isoformat(),
            "source": self.source,
        })

        FOLLOW_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FOLLOW_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    # ================================================================
    # AngularJS 補助（like_executor.py から移植）
    # ================================================================

    def _wait_for_angular(self, page: Page, timeout_ms: int = 5000) -> None:
        """AngularJS のレンダリング完了を待機する"""
        try:
            page.evaluate("""(timeout) => {
                return new Promise((resolve) => {
                    if (typeof angular === 'undefined') { resolve(); return; }
                    const start = Date.now();
                    const check = () => {
                        try {
                            const el = document.querySelector('[ng-app]') || document.body;
                            const injector = angular.element(el).injector();
                            if (!injector) { resolve(); return; }
                            const $http = injector.get('$http');
                            if ($http.pendingRequests.length === 0) {
                                resolve();
                            } else if (Date.now() - start > timeout) {
                                resolve();
                            } else {
                                setTimeout(check, 100);
                            }
                        } catch(e) { resolve(); }
                    };
                    check();
                });
            }""", timeout_ms)
            logger.debug("AngularJS レンダリング完了")
        except Exception:
            pass  # Angular未使用ページでも継続

    def _is_404_page(self, page: Page) -> bool:
        """404ページかどうかを判定する"""
        try:
            el_404 = page.locator("text=404").first
            if el_404.is_visible(timeout=500):
                return True
        except Exception:
            pass
        return False

    # ================================================================
    # メイン実行
    # ================================================================

    def run(self) -> dict:
        """フォローセッションを実行する"""
        logger.info("=" * 60)
        logger.info(f"=== フォローBOT 開始 (目標: {self.limit}件) ===")
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
                try:
                    from notifier import notify, NotifyType
                    notify(NotifyType.APPROVAL, detail=f"フォローBOT: {abort_reason}")
                except Exception:
                    pass
                return self._make_summary(True, abort_reason)

            logger.info(f"ログイン確認OK ({login_status['method']})")

            # おすすめユーザーページに遷移
            abort_reason = self._follow_from_recommend(bm)

        except Exception as e:
            abort_reason = f"予期しないエラー: {e}"
            logger.error(abort_reason)
        finally:
            bm.stop()

        return self._make_summary(abort_reason is not None, abort_reason)

    def _follow_from_recommend(self, bm: BrowserManager) -> str | None:
        """おすすめユーザーページからフォローする

        Returns:
            中断理由（正常完了ならNone）
        """
        page = bm.page

        try:
            page.goto(config.RECOMMEND_USERS_URL, wait_until="domcontentloaded")
            self._human_delay(3.0, 5.0)
        except Exception as e:
            logger.error(f"おすすめユーザーページ遷移エラー: {e}")
            return f"ページ遷移エラー: {e}"

        # ログインリダイレクトチェック
        if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
            return "ログインページにリダイレクトされました"

        # 404チェック
        if self._is_404_page(page):
            logger.error(f"404ページ: {page.url}")
            return f"404ページ: {page.url}"

        logger.info(f"おすすめユーザーページ表示: {page.url}")
        bm.take_screenshot("follow_recommend_start")

        # AngularJS レンダリング待機
        self._wait_for_angular(page)

        # スクロールしながらフォローボタンを探す
        max_scroll_attempts = 20
        scroll_count = 0

        while self.followed_count < self.limit and scroll_count < max_scroll_attempts:
            # 連続失敗チェック
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                return f"{MAX_CONSECUTIVE_FAILURES}件連続失敗で自動停止"

            # フォローボタンを探してクリック
            result = self._find_and_click_follow(page)

            if result == "no_buttons":
                # スクロールして新しいユーザーを読み込む
                scroll_count += 1
                page.evaluate("window.scrollBy(0, 500)")
                self._human_delay(2.0, 4.0)

                # AngularJS 再レンダリング待機
                self._wait_for_angular(page)

                # たまに長めの待機
                if scroll_count % 4 == 0:
                    self._human_delay(3.0, 6.0)
            elif result == "clicked":
                scroll_count = 0  # クリックできたらスクロールカウントリセット

        return None

    def _find_and_click_follow(self, page: Page) -> str:
        """ページ上のフォローボタンを探してクリックする

        実DOM構造（2026-03-16 診断結果）:
          <div class="border-button ng-scope"
               ng-click="discover.toggleFollow(user)">
            <span class="follow icon-follow ng-scope">フォロー</span>
          </div>

        Returns:
            "clicked" | "no_buttons"
        """
        # 楽天ROOMのフォローボタンセレクタ（診断結果に基づく正確なセレクタ）
        follow_selectors = [
            # 主候補: border-button内にフォロー用spanがある（未フォロー状態）
            'div.border-button:has(span.follow.icon-follow)',
            # 補助: ng-click ベース
            'div[ng-click="discover.toggleFollow(user)"]:has(span.follow)',
            # フォールバック: span直接（親divが変わった場合）
            'span.follow.icon-follow:not(.icon-following)',
        ]

        for selector in follow_selectors:
            try:
                buttons = page.locator(selector)
                count = buttons.count()

                if count == 0:
                    continue

                logger.info(f"フォローボタン候補: {count}件 ({selector})")

                for i in range(min(count, 3)):  # 一度に最大3件
                    if self.followed_count >= self.limit:
                        return "clicked"

                    try:
                        btn = buttons.nth(i)
                        if not btn.is_visible(timeout=1000):
                            continue

                        # ユーザーID/名前を取得（重複チェック用）
                        user_id = self._get_user_id_near(page, btn)
                        user_name = self._get_user_name_near(page, btn)

                        if user_id and user_id in self.followed_users:
                            self.skipped_count += 1
                            logger.debug(f"スキップ（フォロー済み）: {user_id}")
                            continue

                        # デバッグ: クリック対象の情報
                        self._log_element_info(btn, i)

                        # 現在のURLを記録（クリック後のナビゲーション検出用）
                        url_before = page.url

                        # フォロークリック
                        btn.click(timeout=3000)
                        self._human_delay(0.5, 1.0)

                        # URL変化検出（ページ遷移してしまった場合）
                        if page.url != url_before:
                            logger.warning(f"クリック後にURL変化: {url_before} → {page.url}")
                            page.go_back()
                            self._human_delay(1.0, 2.0)
                            self._wait_for_angular(page)
                            self.failed_count += 1
                            self.consecutive_failures += 1
                            continue

                        # フォロー成功判定
                        success = self._verify_follow_success(page, btn)

                        self.followed_count += 1
                        self.consecutive_failures = 0

                        if user_id:
                            self.followed_users.add(user_id)
                            self._save_history_entry(user_id, user_name)

                        if success:
                            logger.info(
                                f"[{self.followed_count}/{self.limit}] "
                                f"フォロー成功: {user_name or user_id or '(不明)'}"
                            )
                        else:
                            logger.info(
                                f"[{self.followed_count}/{self.limit}] "
                                f"フォロークリック: {user_name or user_id or '(不明)'} "
                                f"(状態変化未確認)"
                            )

                        # 人間らしい間隔
                        self._follow_interval()
                        return "clicked"

                    except Exception as e:
                        self.failed_count += 1
                        self.consecutive_failures += 1
                        logger.warning(
                            f"フォロークリック失敗: {e} "
                            f"(連続失敗: {self.consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                        )

            except Exception as e:
                logger.debug(f"セレクタ {selector} 検索エラー: {e}")
                continue

        return "no_buttons"

    def _log_element_info(self, element, index: int) -> None:
        """クリック対象の要素情報をログ出力する"""
        try:
            tag = element.evaluate("el => el.tagName")
            cls = element.evaluate("el => el.className.substring(0, 80)")
            text = (element.text_content() or "").strip()[:30]
            logger.debug(
                f"  クリック対象[{index}]: <{tag} class='{cls}'> text='{text}'"
            )
        except Exception:
            pass

    def _verify_follow_success(self, page: Page, button) -> bool:
        """フォロー成功を判定する

        判定方法（優先順）:
          1. border-button内のspan.follow.icon-followが消失（→ icon-followingに変化）
          2. AngularJS scope の user.is_following が true に変化
          3. ボタンテキストが「フォロー中」に変化
        """
        try:
            # 方法1: span.icon-follow が消えて icon-following に変わったか
            page.wait_for_timeout(500)
            try:
                # クリックしたボタン内のspan状態を確認
                has_following = button.evaluate("""el => {
                    // border-button div の場合
                    const followingSpan = el.querySelector('span.icon-following') ||
                                          el.querySelector('span.following');
                    if (followingSpan) return true;
                    // span直接クリックの場合、親を確認
                    const parent = el.closest('.border-button') || el.parentElement;
                    if (parent) {
                        const fs = parent.querySelector('span.icon-following') ||
                                   parent.querySelector('span.following');
                        if (fs) return true;
                    }
                    // クラスに following が含まれるか
                    const cls = el.className || '';
                    return cls.includes('following');
                }""")
                if has_following:
                    logger.debug("  成功判定: icon-following クラス確認")
                    return True
            except Exception:
                pass

            # 方法2: テキスト変化（フォロー → フォロー中）
            try:
                new_text = button.text_content() or ""
                if "中" in new_text or "済" in new_text:
                    logger.debug("  成功判定: テキスト変化確認")
                    return True
            except Exception:
                pass

            logger.debug("  成功判定: 状態変化未確認（クリック自体は成功）")
            return False

        except Exception:
            return False

    def _get_user_id_near(self, page: Page, button) -> str:
        """フォローボタン付近のユーザーIDを取得する"""
        try:
            # AngularJSスコープからユーザーIDを取得
            user_id = button.evaluate("""el => {
                try {
                    // border-button or その親からスコープを探す
                    const target = el.closest('[ng-repeat]') ||
                                   el.closest('[ng-click]') ||
                                   el.parentElement;
                    if (target && typeof angular !== 'undefined') {
                        const scope = angular.element(target).scope();
                        if (scope && scope.user) {
                            return scope.user.nickname || scope.user.id || '';
                        }
                    }
                } catch(e) {}
                return '';
            }""")
            if user_id:
                return user_id
        except Exception:
            pass

        # フォールバック: 親要素からユーザーリンクを探す
        try:
            parent = button.locator(
                "xpath=ancestor::div[contains(@class, 'user') or contains(@class, 'card')]"
            ).first
            if parent.count() > 0:
                link = parent.locator("a[href*='room.rakuten.co.jp/']").first
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                    parts = href.rstrip("/").split("/")
                    if parts:
                        return parts[-1]
        except Exception:
            pass

        return ""

    def _get_user_name_near(self, page: Page, button) -> str:
        """フォローボタン付近のユーザー名を取得する"""
        try:
            # AngularJSスコープからユーザー名を取得
            name = button.evaluate("""el => {
                try {
                    const target = el.closest('[ng-repeat]') ||
                                   el.closest('[ng-click]') ||
                                   el.parentElement;
                    if (target && typeof angular !== 'undefined') {
                        const scope = angular.element(target).scope();
                        if (scope && scope.user) {
                            return scope.user.nickname || '';
                        }
                    }
                } catch(e) {}
                return '';
            }""")
            if name:
                return name[:30]
        except Exception:
            pass

        # フォールバック: DOM上のテキスト要素から探す
        try:
            parent = button.locator(
                "xpath=ancestor::div[contains(@class, 'user') or contains(@class, 'card')]"
            ).first
            if parent.count() > 0:
                name_el = parent.locator(
                    '[class*="name"], [class*="username"], h3, h4'
                ).first
                if name_el.count() > 0:
                    return (name_el.text_content() or "").strip()[:30]
        except Exception:
            pass
        return ""

    def _follow_interval(self) -> None:
        """フォロー後の間隔（人間らしい揺らぎ）"""
        # 一定件数ごとに長めの休憩
        rest_every = config.get_follow_rest_interval()
        if self.followed_count % rest_every == 0 and self.followed_count > 0:
            rest = config.get_follow_rest_duration()
            logger.info(f"  休憩: {rest:.0f}秒 ({self.followed_count}件完了)")
            time.sleep(rest)
            return

        # 通常間隔（1-3秒）
        interval = random.uniform(
            config.FOLLOW_INTERVAL_MIN, config.FOLLOW_INTERVAL_MAX
        )
        time.sleep(interval)

    def _human_delay(self, min_sec: float, max_sec: float) -> None:
        """人間的なランダム遅延"""
        time.sleep(random.uniform(min_sec, max_sec))

    def _make_summary(self, aborted: bool = False, reason: str = None) -> dict:
        """実行結果サマリーを生成"""
        summary = {
            "action": "follow",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "target": self.limit,
            "followed": self.followed_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "aborted": aborted,
            "reason": reason,
            "finished_at": datetime.now().isoformat(),
        }

        print(f"\n{'=' * 60}")
        if aborted:
            print(f"フォローBOT 中断: {reason}")
        else:
            print("フォローBOT 完了")
        print(f"{'=' * 60}")
        print(f"  目標:     {self.limit}件")
        print(f"  成功:     {self.followed_count}件")
        print(f"  スキップ: {self.skipped_count}件")
        print(f"  失敗:     {self.failed_count}件")
        print(f"{'=' * 60}")

        logger.info(
            f"フォローBOT結果: followed={self.followed_count} "
            f"skipped={self.skipped_count} failed={self.failed_count}"
            + (f" abort={reason}" if aborted else "")
        )

        return summary
