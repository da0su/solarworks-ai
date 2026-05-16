"""ROOM BOT v2 - 投稿実行

商品ページ → 「シェア」→「ROOMに投稿」→ /mix?itemcode= で投稿する。
（旧 collect?url= 方式は廃止済み）
"""

import random
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Page, Locator

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from executor.browser_manager import BrowserManager
from executor.selectors import (
    REVIEW_TEXTAREA_SELECTORS,
    SUBMIT_BUTTON_SELECTORS,
    COLLECT_ERROR_PATTERNS,
    COLLECT_NG_TEXT_PATTERNS,
    COLLECT_OK_SELECTORS,
    POST_SUCCESS_INDICATORS,
    ERROR_TEXT_PATTERNS,
)
from logger.logger import setup_logger

logger = setup_logger()


class PostExecutor:
    """楽天ROOMへの投稿を実行するクラス"""

    def __init__(self, browser_manager: BrowserManager):
        self.bm = browser_manager

    @property
    def page(self) -> Page:
        return self.bm.page

    def execute(self, product_url: str, review_text: str) -> dict:
        """1件の投稿を実行する

        フロー（/mix?itemcode= 方式）:
          1. 商品ページを開く
          2. 「シェア」ボタンをクリック
          3. 「ROOMに投稿」リンクのhrefからmix URLを取得
          4. mix ページに遷移
          5. textarea[name="content"]にレビュー文を入力
          6. 「完了」ボタンをクリック
          7. 成功/失敗を判定

        Args:
            product_url: 楽天市場の商品ページURL
            review_text: 投稿するレビュー文（ハッシュタグ含む）

        Returns:
            dict: {"success": bool, "room_url": str|None, "error": str|None, ...}
        """
        result = {
            "success": False,
            "room_url": None,
            "error": None,
            "error_type": None,
            "screenshots": [],
        }

        try:
            # Step 1: 商品ページを開く
            logger.info(f"商品ページに遷移: {product_url}")
            self.page.goto(product_url, wait_until="domcontentloaded")
            self._human_delay(3.0, 5.0)

            # 商品ページのエラーチェック
            page_title = self.page.title()
            if "エラー" in page_title or "見つかりません" in page_title:
                result["error"] = f"商品ページがエラー: {page_title}"
                result["error_type"] = "product_page_error"
                result["screenshots"].append(str(self.bm.take_screenshot("01_product_error")))
                return result

            result["screenshots"].append(str(self.bm.take_screenshot("01_product_page")))
            logger.info(f"商品ページ表示OK: {page_title[:60]}")

            # Step 2: 「シェア」ボタンをクリック
            logger.info("「シェア」ボタンを探しています...")
            share_btn = self.page.locator('button:has-text("シェア")')
            if share_btn.count() == 0:
                result["error"] = "シェアボタンが見つかりません"
                result["error_type"] = "no_share_button"
                result["screenshots"].append(str(self.bm.take_screenshot("02_no_share")))
                return result

            share_btn.first.click()
            self._human_delay(1.5, 3.0)
            logger.info("シェアボタンクリック完了")

            # Step 3: 「ROOMに投稿」リンクを取得
            logger.info("「ROOMに投稿」リンクを探しています...")
            room_link = self.page.locator('a[href*="room.rakuten.co.jp/mix"]')
            if room_link.count() == 0:
                # フォールバック: テキストで探す
                room_link = self.page.locator('a:has-text("ROOMに投稿")')

            if room_link.count() == 0:
                result["error"] = "ROOMに投稿リンクが見つかりません"
                result["error_type"] = "no_room_link"
                result["screenshots"].append(str(self.bm.take_screenshot("02_no_room_link")))
                return result

            mix_url = room_link.first.get_attribute("href")
            if not mix_url or "room.rakuten.co.jp" not in mix_url:
                result["error"] = f"mix URLが不正: {mix_url}"
                result["error_type"] = "invalid_mix_url"
                return result

            logger.info(f"mix URL取得: {mix_url}")

            # Step 4: mix ページに遷移
            self.page.goto(mix_url, wait_until="domcontentloaded")
            self._human_delay(3.0, 5.0)
            result["screenshots"].append(str(self.bm.take_screenshot("03_mix_page")))

            current_url = self.page.url
            logger.info(f"mix ページ遷移完了: {current_url}")

            # 2026-05-07 P0-1.5 (Plan v5 補強):
            # 楽天は POST 等で login.account.rakuten.com/session/upgrade に強制 redirect し
            # password 再入力 (= session 昇格) を要求する。
            # bot 自律運用には .env の RAKUTEN_LOGIN_PASSWORD で自動通過する。
            if "login.account.rakuten.com/session/upgrade" in current_url:
                logger.info("session/upgrade ページ検知 → 自動 password 入力試行")
                up = self.bm.handle_session_upgrade()
                if up.get("handled"):
                    current_url = self.page.url
                    logger.info(f"session/upgrade 通過後: {current_url}")
                    # 通過後は元の mix ページに戻る必要があるかも (楽天が自動で戻す想定)
                    self._human_delay(1.0, 2.0)
                else:
                    result["error"] = f"session/upgrade 通過失敗: {up.get('reason')}"
                    result["error_type"] = "session_upgrade_failed"
                    return result

            # mix ページのエラーチェック
            if "/common/error" in current_url or "404" in (self.page.title() or ""):
                result["error"] = "mix ページがエラー"
                result["error_type"] = "mix_page_error"
                return result

            # ログインリダイレクトチェック
            if "grp01.id.rakuten.co.jp" in current_url or "/nid/" in current_url:
                result["error"] = "ログインページにリダイレクトされました"
                result["error_type"] = "login_redirect"
                return result

            # Step 5: textarea[name="content"]にレビュー文を入力
            logger.info("テキストエリアを探しています...")
            textarea = self.page.locator('textarea[name="content"]')
            if textarea.count() == 0:
                # フォールバック
                textarea = self._find_review_textarea()
                if textarea is None:
                    result["error"] = "テキストエリアが見つかりません"
                    result["screenshots"].append(str(self.bm.take_screenshot("04_no_textarea")))
                    return result
            else:
                textarea = textarea.first

            textarea.wait_for(state="visible", timeout=10000)

            # div.background オーバーレイが消えるのを待つ
            overlay = self.page.locator("div.background")
            if overlay.count() > 0:
                try:
                    overlay.first.wait_for(state="hidden", timeout=5000)
                    logger.debug("div.background オーバーレイが非表示になりました")
                except Exception:
                    logger.debug("div.background 待ちタイムアウト（続行）")

            textarea.scroll_into_view_if_needed()
            textarea.focus()
            self._human_delay(0.3, 0.5)

            # fill で入力（click は使わない）
            logger.info(f"レビュー文を入力中... ({len(review_text)}文字)")
            try:
                textarea.fill(review_text)
            except Exception as fill_err:
                logger.warning(f"fill失敗、JSフォールバック: {fill_err}")
                self.page.evaluate("""([sel, text]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = text;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""", ['textarea[name="content"]', review_text])

            self._human_delay(1.0, 2.0)
            result["screenshots"].append(str(self.bm.take_screenshot("04_review_entered")))

            # 入力確認
            entered = textarea.input_value()
            if not entered or len(entered) < 10:
                result["error"] = f"入力テキストが短すぎます（{len(entered or '')}文字）"
                return result
            logger.info(f"入力完了: {len(entered)}文字")

            # Step 6: 「完了」ボタンをクリック
            logger.info("「完了」ボタンを探しています...")
            done_btn = self.page.locator('button.collect-btn')
            if done_btn.count() == 0:
                done_btn = self.page.locator('button:has-text("完了")')
            if done_btn.count() == 0:
                # フォールバック
                done_btn_loc = self._find_submit_button()
                if done_btn_loc is None:
                    result["error"] = "完了ボタンが見つかりません"
                    result["screenshots"].append(str(self.bm.take_screenshot("05_no_done_btn")))
                    return result
                done_btn = done_btn_loc
            else:
                done_btn = done_btn.first

            logger.info("「完了」ボタンをクリック")
            try:
                done_btn.click(timeout=5000)
            except Exception:
                logger.warning("通常clickタイムアウト、force=True で再試行")
                done_btn.click(force=True)
            self._human_delay(3.0, 6.0)
            result["screenshots"].append(str(self.bm.take_screenshot("05_submitted")))

            # Step 7: 投稿成功を確認
            # 2026-05-16 CEO 指摘 + ChatGPT Codex (GPT-5) review で強化:
            # 二段階成功判定 - 過去の false success/failure を再発しない設計.
            #   (A) /mix/collect URL から離脱 (wait_for_url で決定的待機)
            #   (B) AND room.rakuten.co.jp 配下の有効 URL に遷移
            # 両方満たさない場合 failed return.
            post_error = self._check_post_error()
            if post_error:
                result["error"] = f"投稿後エラー: {post_error}"
                result["screenshots"].append(str(self.bm.take_screenshot("06_post_error")))
                return result

            # collect 画面 error patterns check (例外も warning でログ)
            try:
                body_text = self.page.text_content("body") or ""
                from .selectors import COLLECT_ERROR_PATTERNS
                for p in COLLECT_ERROR_PATTERNS:
                    if p in body_text:
                        result["error"] = f"投稿エラー: {p}"
                        logger.error(f"投稿エラー検知: {p}")
                        result["screenshots"].append(str(self.bm.take_screenshot("06_collect_error")))
                        return result
            except Exception as e:
                logger.warning(f"COLLECT_ERROR_PATTERNS check 例外 (握りつぶさず継続): {e}")

            # 2026-05-16 Codex review 3 回目で重大バグ発見:
            # 初期 URL が /mix?itemcode=... (pathname=/mix) のため
            # wait_for_function("!location.pathname.includes('/mix/collect')") は即 true で待機ゼロ.
            # 修正: 初期 URL を保持・URL 自体の変化 + 許容パスへの遷移を待機.

            # (A) submit click 直前の URL を記録 (mix_url) - 「異なる URL に遷移」を待機条件に
            mix_url_snapshot = self.page.url

            # (B) URL 変化を決定的待機 (initial URL から変化するまで・30s)
            try:
                self.page.wait_for_function(
                    "(initial) => window.location.href !== initial",
                    arg=mix_url_snapshot,
                    timeout=30000,
                )
            except Exception:
                final_url = self.page.url
                result["error"] = f"投稿未完了 (URL unchanged 30s): {final_url[:100]}"
                logger.error(f"投稿未完了: 30s 経過しても URL が変化せず ({mix_url_snapshot} → {final_url})")
                result["screenshots"].append(str(self.bm.take_screenshot("06_url_unchanged")))
                return result

            # (C) 遷移先 URL の妥当性確認:
            #     1) 許容ホスト (room.rakuten.co.jp 系)
            #     2) 禁止パス (/mix /collect /common/error) は failure
            from urllib.parse import urlparse
            ALLOWED_HOSTS = {"room.rakuten.co.jp", "sp.room.rakuten.co.jp"}
            FORBIDDEN_PATH_KEYWORDS = ["/mix", "/collect", "/common/error", "/error"]
            final_url = self.page.url
            parsed = urlparse(final_url)
            if parsed.hostname not in ALLOWED_HOSTS:
                result["error"] = f"投稿後 不明なホスト: {final_url[:120]}"
                logger.error(f"投稿失敗: ホスト {parsed.hostname} が {ALLOWED_HOSTS} 外")
                result["screenshots"].append(str(self.bm.take_screenshot("06_bad_host")))
                return result
            for forbidden in FORBIDDEN_PATH_KEYWORDS:
                if forbidden in parsed.path:
                    result["error"] = f"投稿後 禁止パス遷移: {final_url[:120]}"
                    logger.error(f"投稿失敗: パス {parsed.path} に禁止キーワード '{forbidden}'")
                    result["screenshots"].append(str(self.bm.take_screenshot("06_forbidden_path")))
                    return result

            # (D) final_url 必須・http 開始 check
            if not final_url or not final_url.startswith("http"):
                result["error"] = f"投稿後 final_url 不正: {final_url}"
                logger.error(f"投稿失敗: final_url 不正")
                return result

            result["room_url"] = final_url
            result["success"] = True
            logger.info(f"投稿成功! room_url: {final_url}")
            result["screenshots"].append(str(self.bm.take_screenshot("06_success")))

            self.bm.save_session()
            return result

        except Exception as e:
            result["error"] = f"予期しないエラー: {str(e)}"
            logger.error(result["error"], exc_info=True)
            try:
                result["screenshots"].append(str(self.bm.take_screenshot("error_unexpected")))
            except Exception:
                pass
            return result

    # ================================================================
    # ページ分析
    # ================================================================

    def _analyze_page(self) -> dict:
        """現在のページの種別・ボタン・リンクを分析してログ出力"""
        info = {
            "url": "",
            "title": "",
            "page_type": "unknown",
            "buttons": [],
            "room_links": [],
        }

        try:
            info["url"] = self.page.url
            info["title"] = self.page.title()
        except Exception:
            pass

        url = info["url"]

        # ページ種別判定
        if "grp01.id.rakuten.co.jp" in url or "/nid/" in url:
            info["page_type"] = "login_redirect"
        elif "room.rakuten.co.jp/collect" in url:
            info["page_type"] = "room_collect"
        elif "room.rakuten.co.jp" in url:
            info["page_type"] = "room_other"
        elif "item.rakuten.co.jp" in url:
            info["page_type"] = "rakuten_item"
        elif "books.rakuten.co.jp" in url:
            info["page_type"] = "rakuten_books"
        elif "rakuten.co.jp" in url:
            info["page_type"] = "rakuten_other"

        # 主要ボタンのテキストを列挙
        try:
            buttons = self.page.locator("button, a[role='button'], input[type='submit'], input[type='button']")
            count = buttons.count()
            for i in range(min(count, 20)):
                try:
                    text = buttons.nth(i).inner_text(timeout=1000).strip().replace("\n", " ")
                    if text:
                        info["buttons"].append(text[:40])
                except Exception:
                    continue
        except Exception:
            pass

        # ROOM関連リンク
        try:
            room_links = self.page.locator('a[href*="room.rakuten.co.jp"]')
            count = room_links.count()
            for i in range(min(count, 10)):
                try:
                    href = room_links.nth(i).get_attribute("href", timeout=1000) or ""
                    text = room_links.nth(i).inner_text(timeout=1000).strip()[:30]
                    info["room_links"].append(f"{text} -> {href[:60]}")
                except Exception:
                    continue
        except Exception:
            pass

        return info

    # ================================================================
    # 要素検索
    # ================================================================

    def _find_review_textarea(self) -> Locator | None:
        """レビュー入力欄を探す"""
        self.page.wait_for_timeout(2000)

        for selector in REVIEW_TEXTAREA_SELECTORS:
            try:
                locator = self.page.locator(selector)
                if locator.first.is_visible(timeout=2000):
                    logger.debug(f"テキストエリア発見: {selector}")
                    return locator.first
            except Exception:
                continue
        return None

    def _find_submit_button(self) -> Locator | None:
        """投稿ボタンを探す"""
        for selector in SUBMIT_BUTTON_SELECTORS:
            try:
                locator = self.page.locator(selector)
                if locator.first.is_visible(timeout=2000):
                    logger.debug(f"投稿ボタン発見: {selector}")
                    return locator.first
            except Exception:
                continue
        return None

    # ================================================================
    # エラーチェック
    # ================================================================

    def _check_collect_error(self) -> str | None:
        """ROOM collect画面固有のエラーを確認"""
        try:
            body_text = self.page.text_content("body") or ""
            for pattern in COLLECT_ERROR_PATTERNS:
                if pattern in body_text:
                    logger.error(f"collect エラー検知: {pattern}")
                    return pattern
        except Exception:
            pass
        return None

    def _check_post_error(self) -> str | None:
        """投稿後のエラーを確認（商品ページでは使わない）"""
        try:
            body_text = self.page.text_content("body") or ""
            for pattern in ERROR_TEXT_PATTERNS:
                if pattern in body_text:
                    logger.error(f"投稿後エラー検知: {pattern}")
                    return pattern
        except Exception:
            pass
        return None

    # ================================================================
    # collect 事前検証
    # ================================================================

    def check_collect(self, product_url: str, save_screenshot: bool = True) -> dict:
        """商品URLがROOM collectに対応しているか厳密に検証する（投稿はしない）

        判定ロジック（優先順）:
          1. URL に /common/error → NG (common_error_url)
          2. ログインリダイレクト → NG (login_redirect)
          3. bodyテキストに NG パターン → NG (ng_text: ...)
          4. collect画面の主要要素（textarea/投稿ボタン）が見える → OK (textarea_visible / submit_button_visible)
          5. 上記いずれにも該当しない → NG (no_collect_elements)

        Returns:
            dict: {
                "url": str,
                "supported": bool,
                "reason": str,
                "redirected_url": str,
                "screenshot": str | None,
            }
        """
        collect_url = f"{ROOM_COLLECT_URL}?url={quote(product_url, safe='')}"
        result_base = {"url": product_url, "supported": False,
                       "reason": "", "redirected_url": "", "screenshot": None}

        try:
            self.page.goto(collect_url, wait_until="domcontentloaded")
            self._human_delay(2.0, 4.0)
        except Exception as e:
            result_base["reason"] = f"navigation_error: {e}"
            return result_base

        current_url = self.page.url
        result_base["redirected_url"] = current_url

        # スクリーンショット保存
        if save_screenshot:
            try:
                # URLからファイル名を作る（商品ID部分を使用）
                url_slug = product_url.split("/")[-2][:30] if "/" in product_url else "unknown"
                ss_path = str(self.bm.take_screenshot(f"check_collect_{url_slug}"))
                result_base["screenshot"] = ss_path
            except Exception:
                pass

        # --- NG判定1: URLベース ---
        if "/common/error" in current_url:
            result_base["reason"] = "ng: common_error_url"
            return result_base

        # --- NG判定2: ログインリダイレクト ---
        if "grp01.id.rakuten.co.jp" in current_url or "/nid/" in current_url:
            result_base["reason"] = "ng: login_redirect"
            return result_base

        # --- NG判定3: bodyテキストにNGパターン ---
        try:
            body_text = (self.page.text_content("body") or "").lower()
            for pattern in COLLECT_NG_TEXT_PATTERNS:
                if pattern.lower() in body_text:
                    result_base["reason"] = f"ng: ng_text ({pattern})"
                    return result_base
        except Exception:
            pass

        # --- OK判定: collect画面の主要要素が見えるか ---
        for selector in COLLECT_OK_SELECTORS:
            try:
                locator = self.page.locator(selector)
                if locator.first.is_visible(timeout=2000):
                    # どの要素で判定したかをreasonに記録
                    if "textarea" in selector or "contenteditable" in selector:
                        reason = "ok: textarea_visible"
                    else:
                        reason = "ok: submit_button_visible"
                    result_base["supported"] = True
                    result_base["reason"] = reason
                    return result_base
            except Exception:
                continue

        # --- どれにも該当しない → NG ---
        result_base["reason"] = "ng: no_collect_elements"
        # デバッグ用: ページのbody先頭を記録
        try:
            preview = (self.page.text_content("body") or "")[:200].replace("\n", " ")
            logger.warning(f"check_collect no_collect_elements body_preview: {preview}")
        except Exception:
            pass
        return result_base

    # ================================================================
    # 導線調査モード
    # ================================================================

    def investigate_page(self, url: str, page_label: str = "unknown") -> dict:
        """指定URLを開いて、ROOM投稿に関連する全要素を調査する

        調査内容:
          - ページ上のボタン・リンク一覧（テキスト + href + data属性）
          - ROOM関連の要素（"コレ" "room" "collect" を含む要素）
          - iframe / modal / popup の検出
          - 遷移先URLの記録
          - スクリーンショット保存

        Returns:
            dict: 調査結果の詳細
        """
        report = {
            "input_url": url,
            "page_label": page_label,
            "final_url": "",
            "title": "",
            "screenshots": [],
            "all_buttons": [],
            "all_links": [],
            "room_related": [],
            "data_attributes": [],
            "iframes": [],
            "textareas": [],
            "modals_dialogs": [],
            "body_preview": "",
        }

        try:
            logger.info(f"[調査] {page_label}: {url}")
            self.page.goto(url, wait_until="domcontentloaded")
            self._human_delay(3.0, 5.0)
        except Exception as e:
            report["error"] = f"navigation_error: {e}"
            return report

        report["final_url"] = self.page.url
        try:
            report["title"] = self.page.title()
        except Exception:
            pass

        # スクリーンショット
        try:
            slug = page_label.replace(" ", "_")[:20]
            ss = str(self.bm.take_screenshot(f"investigate_{slug}"))
            report["screenshots"].append(ss)
        except Exception:
            pass

        # --- ボタン一覧 ---
        try:
            buttons = self.page.locator("button")
            count = buttons.count()
            for i in range(min(count, 30)):
                try:
                    btn = buttons.nth(i)
                    text = btn.inner_text(timeout=1000).strip().replace("\n", " ")[:60]
                    cls = btn.get_attribute("class", timeout=500) or ""
                    data_ratid = btn.get_attribute("data-ratid", timeout=500) or ""
                    onclick = btn.get_attribute("onclick", timeout=500) or ""
                    entry = {"text": text, "class": cls[:80], "data-ratid": data_ratid, "onclick": onclick[:80]}
                    report["all_buttons"].append(entry)
                    # ROOM関連チェック
                    combined = f"{text} {cls} {data_ratid} {onclick}".lower()
                    if any(kw in combined for kw in ["コレ", "kore", "room", "collect", "投稿"]):
                        report["room_related"].append({"type": "button", **entry})
                except Exception:
                    continue
        except Exception:
            pass

        # --- リンク一覧 ---
        try:
            links = self.page.locator("a[href]")
            count = links.count()
            for i in range(min(count, 50)):
                try:
                    link = links.nth(i)
                    href = link.get_attribute("href", timeout=500) or ""
                    text = link.inner_text(timeout=500).strip().replace("\n", " ")[:60]
                    cls = link.get_attribute("class", timeout=500) or ""
                    data_ratid = link.get_attribute("data-ratid", timeout=500) or ""
                    entry = {"text": text, "href": href[:120], "class": cls[:80], "data-ratid": data_ratid}
                    report["all_links"].append(entry)
                    # ROOM関連チェック
                    combined = f"{text} {href} {cls} {data_ratid}".lower()
                    if any(kw in combined for kw in ["コレ", "kore", "room", "collect", "投稿"]):
                        report["room_related"].append({"type": "link", **entry})
                except Exception:
                    continue
        except Exception:
            pass

        # --- data-ratid / data-testid 属性を持つ全要素 ---
        try:
            for attr in ["data-ratid", "data-testid", "data-room", "data-action"]:
                elements = self.page.locator(f"[{attr}]")
                count = elements.count()
                for i in range(min(count, 20)):
                    try:
                        el = elements.nth(i)
                        val = el.get_attribute(attr, timeout=500) or ""
                        tag = el.evaluate("el => el.tagName", timeout=500)
                        text = el.inner_text(timeout=500).strip()[:40]
                        report["data_attributes"].append({
                            "attr": attr, "value": val, "tag": tag, "text": text
                        })
                    except Exception:
                        continue
        except Exception:
            pass

        # --- iframe 検出 ---
        try:
            iframes = self.page.locator("iframe")
            count = iframes.count()
            for i in range(min(count, 10)):
                try:
                    src = iframes.nth(i).get_attribute("src", timeout=500) or ""
                    name = iframes.nth(i).get_attribute("name", timeout=500) or ""
                    report["iframes"].append({"src": src[:120], "name": name})
                except Exception:
                    continue
        except Exception:
            pass

        # --- textarea / contenteditable ---
        try:
            tas = self.page.locator("textarea, [contenteditable='true']")
            count = tas.count()
            for i in range(min(count, 10)):
                try:
                    ta = tas.nth(i)
                    placeholder = ta.get_attribute("placeholder", timeout=500) or ""
                    name = ta.get_attribute("name", timeout=500) or ""
                    tag = ta.evaluate("el => el.tagName", timeout=500)
                    report["textareas"].append({"tag": tag, "name": name, "placeholder": placeholder})
                except Exception:
                    continue
        except Exception:
            pass

        # --- modal / dialog 検出 ---
        try:
            modals = self.page.locator("dialog, [role='dialog'], [role='modal'], .modal, .popup, .overlay")
            count = modals.count()
            for i in range(min(count, 5)):
                try:
                    m = modals.nth(i)
                    cls = m.get_attribute("class", timeout=500) or ""
                    visible = m.is_visible(timeout=500)
                    text = m.inner_text(timeout=500).strip()[:100]
                    report["modals_dialogs"].append({"class": cls[:80], "visible": visible, "text": text})
                except Exception:
                    continue
        except Exception:
            pass

        # --- body preview ---
        try:
            report["body_preview"] = (self.page.text_content("body") or "")[:500].replace("\n", " ")
        except Exception:
            pass

        return report

    def investigate_room_my_page(self) -> dict:
        """ROOMのマイページや投稿作成ページを調査する"""
        results = {}

        # ROOM トップ
        results["room_top"] = self.investigate_page(
            "https://room.rakuten.co.jp/", "room_top"
        )

        # ROOM 投稿作成ページ候補
        for path, label in [
            ("/collect", "room_collect_nourl"),
            ("/my/collect", "room_my_collect"),
            ("/post", "room_post"),
            ("/my/post", "room_my_post"),
            ("/create", "room_create"),
        ]:
            url = f"https://room.rakuten.co.jp{path}"
            results[label] = self.investigate_page(url, label)
            self._human_delay(1.0, 2.0)

        return results

    def investigate_product_page(self, product_url: str) -> dict:
        """商品ページ上のROOM連携要素を調査する"""
        report = self.investigate_page(product_url, "product_page")

        # ページ読み込み後、スクロールして隠れた要素を探す
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            self._human_delay(2.0, 3.0)
            ss = str(self.bm.take_screenshot("investigate_product_scrolled"))
            report["screenshots"].append(ss)
        except Exception:
            pass

        # 追加: JavaScript変数にROOM関連データがないか調査
        try:
            js_check = self.page.evaluate("""() => {
                const results = {};
                // window直下のROOM関連変数
                for (const key of Object.keys(window)) {
                    if (key.toLowerCase().includes('room') || key.toLowerCase().includes('kore') || key.toLowerCase().includes('collect')) {
                        try {
                            results[key] = JSON.stringify(window[key]).substring(0, 200);
                        } catch(e) {
                            results[key] = typeof window[key];
                        }
                    }
                }
                // meta tags
                const metas = {};
                document.querySelectorAll('meta').forEach(m => {
                    const name = m.getAttribute('name') || m.getAttribute('property') || '';
                    if (name) metas[name] = (m.getAttribute('content') || '').substring(0, 100);
                });
                results['__meta_tags'] = metas;
                return results;
            }""")
            report["js_room_vars"] = js_check
        except Exception as e:
            report["js_room_vars"] = {"error": str(e)}

        return report

    @staticmethod
    def format_investigation_report(report: dict, label: str = "") -> str:
        """調査結果を人間が読みやすい形式に整形する"""
        lines = []
        lines.append(f"\n{'=' * 70}")
        lines.append(f"[調査] {label or report.get('page_label', '')}")
        lines.append(f"{'=' * 70}")
        lines.append(f"  input_url:  {report.get('input_url', '')}")
        lines.append(f"  final_url:  {report.get('final_url', '')}")
        lines.append(f"  title:      {report.get('title', '')}")

        if report.get("error"):
            lines.append(f"  ERROR: {report['error']}")
            return "\n".join(lines)

        # screenshots
        for ss in report.get("screenshots", []):
            lines.append(f"  screenshot: {ss}")

        # ROOM関連要素（最重要）
        room = report.get("room_related", [])
        lines.append(f"\n  --- ROOM関連要素: {len(room)}件 ---")
        if room:
            for r in room:
                lines.append(f"    [{r.get('type', '?')}] text=\"{r.get('text', '')}\"")
                if r.get("href"):
                    lines.append(f"           href={r['href']}")
                if r.get("data-ratid"):
                    lines.append(f"           data-ratid={r['data-ratid']}")
                if r.get("onclick"):
                    lines.append(f"           onclick={r['onclick']}")
        else:
            lines.append("    (なし)")

        # textarea
        tas = report.get("textareas", [])
        if tas:
            lines.append(f"\n  --- textarea/contenteditable: {len(tas)}件 ---")
            for t in tas:
                lines.append(f"    {t.get('tag', '')} name=\"{t.get('name', '')}\" placeholder=\"{t.get('placeholder', '')}\"")

        # data属性
        das = report.get("data_attributes", [])
        if das:
            lines.append(f"\n  --- data属性: {len(das)}件 ---")
            for d in das[:15]:
                lines.append(f"    <{d.get('tag', '')} {d.get('attr', '')}=\"{d.get('value', '')}\">{d.get('text', '')[:30]}")

        # iframe
        ifs = report.get("iframes", [])
        if ifs:
            lines.append(f"\n  --- iframe: {len(ifs)}件 ---")
            for f in ifs:
                lines.append(f"    src={f.get('src', '')} name={f.get('name', '')}")

        # JS room vars
        js = report.get("js_room_vars", {})
        if js:
            lines.append(f"\n  --- JS ROOM関連変数 ---")
            for k, v in js.items():
                if k != "__meta_tags":
                    lines.append(f"    {k} = {str(v)[:100]}")

        # ボタン一覧（先頭15件）
        btns = report.get("all_buttons", [])
        if btns:
            lines.append(f"\n  --- ボタン一覧: {len(btns)}件 (先頭15件) ---")
            for b in btns[:15]:
                txt = b.get("text", "(空)")[:40]
                lines.append(f"    [{txt}]  class={b.get('class', '')[:40]}  ratid={b.get('data-ratid', '')}")

        # リンク一覧（ROOM/collect含むもののみ + 先頭10件）
        lks = report.get("all_links", [])
        room_links = [l for l in lks if any(kw in (l.get("href", "") + l.get("text", "")).lower()
                                             for kw in ["room", "collect", "コレ", "kore"])]
        if room_links:
            lines.append(f"\n  --- ROOM関連リンク: {len(room_links)}件 ---")
            for l in room_links:
                lines.append(f"    [{l.get('text', '')[:30]}] -> {l.get('href', '')}")

        # body preview
        bp = report.get("body_preview", "")
        if bp:
            lines.append(f"\n  --- body先頭500文字 ---")
            lines.append(f"    {bp[:300]}")

        return "\n".join(lines)

    # ================================================================
    # ヒューマンライク
    # ================================================================

    def _human_type(self, element: Locator, text: str) -> None:
        """人間のようにテキストを入力する"""
        for char in text:
            delay_ms = random.uniform(config.TYPE_DELAY_MIN, config.TYPE_DELAY_MAX) * 1000
            element.type(char, delay=delay_ms)
            if random.random() < 0.05:
                time.sleep(random.uniform(0.3, 0.8))

    def _human_delay(self, min_sec: float = None, max_sec: float = None) -> None:
        """人間的なランダム遅延"""
        min_s = min_sec or config.HUMAN_DELAY_MIN
        max_s = max_sec or config.HUMAN_DELAY_MAX
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)
