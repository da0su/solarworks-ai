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

# ------------------------------------------------------------------
# POST 成功判定 厳格 URL regex (Codex 5/16 6回目 review 指摘で tightened)
#
# 正規 item URL: https://(sp.)room.rakuten.co.jp/<owner>/items/<digits>
#   - <digits> の直後は終端 / クエリ / フラグメントのみ許容
#   - /items/<id>/like /items/<id>/edit 等の非カノニカル下位パスは不許可
#
# Codex 指摘で防止される false positive:
#   - /items/123/like (いいね画面)  → match しない
#   - /items/123/edit (編集画面)    → match しない
#   - /mix?... (URL残留)            → 元々除外
# ------------------------------------------------------------------
import re as _re
# 末尾スラッシュ '/items/123/' も許容 (本番で SP 版が trailing slash 返却する case を考慮)
STRICT_SUCCESS_URL = _re.compile(
    r"^https://(?:sp\.)?room\.rakuten\.co\.jp/[^/]+/items/\d+/?(?:[?#].*)?$"
)
# モジュール定数化 (Codex 5/16 review 指摘・一貫性 + テスト容易性)
ALLOWED_HOSTS = {"room.rakuten.co.jp", "sp.room.rakuten.co.jp"}
FORBIDDEN_PATH_KEYWORDS = ["/mix", "/collect", "/common/error", "/error"]


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
            # 2026-05-24: /sso/authorize?...sign_in 経路も同じ処理 (POST mix で頻発)
            _sso_fragments = (
                "login.account.rakuten.com/session/upgrade",
                "login.account.rakuten.com/sso/authorize",
                "login.account.rakuten.com/sso/sign_in",
                "grp01.id.rakuten.co.jp/sign_in",
            )
            if any(frag in current_url for frag in _sso_fragments):
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

            # 入力確認 v9.1 (CEO 5/20 指示「省略禁止」+ Codex 28回目 review 反映)
            # ・改行 \r\n → \n 正規化 (textarea は \n に統一されるため)
            # ・Unicode NFC 正規化 (ブラウザ側 NFC 変換に合わせる)
            # ・文字数完全一致 + 全文一致 (中間 1-2 文字改変も検知)
            # ・空入力 / mismatch すべて screenshot 必須 (監査性)
            # ・SHA-256 ハッシュ ログ + maxlength 記録
            # 関連: memory/comment_full_text_rule.md
            import hashlib
            import unicodedata

            def _normalize(s: str) -> str:
                # \r\n / \r → \n + NFC
                return unicodedata.normalize("NFC", (s or "").replace("\r\n", "\n").replace("\r", "\n"))

            def _normalize_loose(s: str) -> str:
                """post_after_check 用 緩い正規化 (HTML 起因の不可視/全角 SP/nbsp 等を吸収).

                Codex v9.2 review #9: HTML 内の改行/nbsp、前後空白、不可視文字を正規化.
                """
                import re as _re_n
                t = unicodedata.normalize(
                    "NFC",
                    (s or "").replace("\r\n", "\n").replace("\r", "\n"),
                )
                # nbsp/全角SP/zwsp/zwj/zwnj/word joiner を半角 SP に
                for ch, rep in [(" ", " "), ("　", " "),
                                 ("​", ""), ("‌", ""),
                                 ("‍", ""), ("⁠", "")]:
                    t = t.replace(ch, rep)
                # 連続空白を 1 つに、前後 trim
                t = _re_n.sub(r"\s+", " ", t).strip()
                return t

            entered_raw = textarea.input_value() or ""
            entered = _normalize(entered_raw)
            expected = _normalize(review_text)
            expected_len = len(expected)
            entered_len = len(entered)
            try:
                maxlength = textarea.get_attribute("maxlength") or "?"
            except Exception:
                maxlength = "?"
            exp_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()[:12]
            ent_hash = hashlib.sha256(entered.encode("utf-8")).hexdigest()[:12]

            # ハッシュ + maxlength を evidence に記録
            result["comment_verify"] = {
                "expected_len": expected_len,
                "entered_len": entered_len,
                "expected_sha256_12": exp_hash,
                "entered_sha256_12": ent_hash,
                "maxlength": maxlength,
                "expected_head": expected[:20],
                "expected_tail": expected[-20:],
                "entered_head": entered[:20],
                "entered_tail": entered[-20:],
            }

            # 空入力 check (空でも screenshot 必須)
            if not entered:
                result["error"] = f"入力テキストが空 (fill 失敗) expected_len={expected_len} maxlength={maxlength}"
                logger.error(f"COMMENT 空入力検知: expected={expected_len}文字 / maxlength={maxlength}")
                result["screenshots"].append(str(self.bm.take_screenshot("04_comment_empty")))
                return result

            # 長さ check
            if entered_len != expected_len:
                result["error"] = (f"入力テキスト長 mismatch: expected={expected_len}, "
                                    f"entered={entered_len}, maxlength={maxlength} (省略 risk)")
                logger.error(f"COMMENT 省略検知: expected={expected_len}文字, entered={entered_len}文字 "
                              f"(差={expected_len-entered_len}), maxlength={maxlength}, "
                              f"exp_hash={exp_hash}, ent_hash={ent_hash}")
                result["comment_length_mismatch"] = True
                result["screenshots"].append(str(self.bm.take_screenshot("04_comment_truncated")))
                return result

            # 全文一致 check (Codex #4 反映: 中間 1-2 文字改変も検知)
            if entered != expected:
                result["error"] = f"入力テキスト中間改変疑い: 長さ一致だが内容不一致 (exp_hash={exp_hash} ent_hash={ent_hash})"
                logger.error(f"COMMENT 中間改変検知: head={entered[:20]!r} vs {expected[:20]!r}, "
                              f"tail={entered[-20:]!r} vs {expected[-20:]!r}")
                result["comment_content_mismatch"] = True
                result["screenshots"].append(str(self.bm.take_screenshot("04_comment_mid_modified")))
                return result

            logger.info(f"入力完了 (full match): {entered_len}文字 / sha={ent_hash} / maxlength={maxlength}")

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

            # submit click 直前 baseline 取得 (Codex 15回目 #3 反映)
            from .selectors import POST_SUCCESS_LINK_SELECTOR as _SUCCESS_LINK_SEL
            try:
                baseline_link_count = self.page.locator(_SUCCESS_LINK_SEL).count()
            except Exception:
                baseline_link_count = 0
            logger.info(f"[success_check] baseline 'my ROOM を見る' link count: {baseline_link_count}")

            # ==========================================================
            # CEO 5/17 真因確定: 真の成功 = my ROOM 商品数 +1 確認 (Codex 18/19回目)
            # Codex 19 #1: import 失敗時は graceful degrade (検証スキップで通常 toast 判定)
            # Codex 19 #3: new_page() を try/finally で確実 close
            # Codex 19 #7: profile health gate も統合
            # ==========================================================
            _fetch_my_room_fingerprint = None
            try:
                # repo root を sys.path に追加 (Codex 19 #1 stable import)
                import sys as _sys
                from pathlib import Path as _Path
                _repo_root = _Path(__file__).resolve().parents[3]
                if str(_repo_root) not in _sys.path:
                    _sys.path.insert(0, str(_repo_root))
                from shared.profile_health import fetch_my_room_fingerprint as _fetch_my_room_fingerprint
            except Exception as _imp_e:
                logger.warning(f"[verify+1] profile_health import 失敗 (検証スキップで toast 判定のみ): {_imp_e}")

            my_room_baseline = None
            if _fetch_my_room_fingerprint:
                check_page = None
                try:
                    ctx = self.page.context
                    check_page = ctx.new_page()
                    my_room_baseline = _fetch_my_room_fingerprint(check_page)
                    logger.info(f"[verify+1] my ROOM baseline: items={my_room_baseline.get('item_count')}, followers={my_room_baseline.get('follower_count')}")
                    # Profile gate (Codex 19 #7): 商品数 baseline=None or 0 のみは別アカウント疑い → fail-fast
                    if my_room_baseline.get("item_count") is None:
                        logger.error("[profile_gate] my ROOM 商品数取得失敗 → profile 異常疑い")
                        try:
                            check_page.close()
                        except Exception:
                            pass
                        result["error"] = "profile gate: my ROOM item_count 取得不能 → 別アカウント or profile 破損疑い"
                        result["profile_gate_fail"] = True
                        result["screenshots"].append(str(self.bm.take_screenshot("05_profile_gate_fail")))
                        return result
                except Exception as e:
                    logger.warning(f"[verify+1] baseline 取得失敗 (続行・後段で検証): {e}")
                finally:
                    if check_page:
                        try:
                            check_page.close()
                        except Exception:
                            pass

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

            # ==========================================================
            # 投稿成功判定 v5 (CEO 2026-05-17 + Codex 14/15回目 review 反映)
            # ==========================================================
            # 真因 (DOM 探索 5/17 21:38):
            #   Rakuten ROOM 現代 UI は submit 後 URL 不変 (mix/collect のまま)
            #   ajax で「コレ ! 完了!」 + 「my ROOM を見る」link 出現 = 投稿成功
            #
            # Codex 15回目 review 反映 (REJECT 9件):
            #   - link は header 'my ROOM' と区別: 'my ROOM を見る' 全文 + text-is で厳格 match
            #   - submit 前 baseline 取得 → 新規出現 (count 増) のみ trigger
            #   - 成功判定 = トースト regex AND link 新規出現 (AND 条件)
            #     どちらか単独では成功にしない (虚偽成功遮断)
            #   - 軽量 URL host ガード fail 復活
            #   - evidence に outerHTML 含める
            # ==========================================================
            from .selectors import POST_SUCCESS_TEXT_SELECTORS, POST_FAILURE_MODAL_SELECTORS

            # Codex 16回目 #2 反映: is_visible(timeout=...) は Python Playwright で TypeError
            # → wait_for(state="visible", timeout=...) + try/except で待機
            def _visible_within(locator, timeout_ms: int) -> bool:
                """指定 timeout 内で visible になれば True. ならなければ False (例外吸収)."""
                try:
                    locator.wait_for(state="visible", timeout=timeout_ms)
                    return True
                except Exception:
                    return False

            # (B) 失敗 modal を先 check
            failure_msg = None
            for fsel in POST_FAILURE_MODAL_SELECTORS:
                try:
                    loc = self.page.locator(fsel).first
                    if _visible_within(loc, 1500):
                        try:
                            failure_text = (loc.inner_text(timeout=500) or "")[:200]
                        except Exception:
                            failure_text = fsel
                        failure_msg = f"{fsel} (text={failure_text!r})"
                        break
                except Exception:
                    continue
            if failure_msg:
                result["error"] = f"投稿失敗 modal 検出: {failure_msg}"
                logger.error(f"投稿失敗: {failure_msg}")
                result["screenshots"].append(str(self.bm.take_screenshot("06_failure_modal")))
                return result

            # (A) 成功判定: トースト regex AND 'my ROOM を見る' link 新規出現 を 30s polling
            #     Codex 15 #2/#3 反映: 両方揃わないと success にしない.
            success_evidence = None
            success_link_href = None
            success_outer_html = None
            toast_detected = False
            link_new_visible = False
            import time as _time
            deadline = _time.time() + 30.0

            while _time.time() < deadline:
                # 失敗 modal 再 check (mid 検知)
                for fsel in POST_FAILURE_MODAL_SELECTORS:
                    try:
                        if _visible_within(self.page.locator(fsel).first, 200):
                            result["error"] = f"投稿失敗 modal mid 検出: {fsel}"
                            logger.error(f"投稿失敗 (mid): {fsel}")
                            result["screenshots"].append(str(self.bm.take_screenshot("06_failure_modal_mid")))
                            return result
                    except Exception:
                        pass

                # トースト regex 検出 check
                if not toast_detected:
                    for tsel in POST_SUCCESS_TEXT_SELECTORS:
                        try:
                            loc = self.page.locator(tsel).first
                            if _visible_within(loc, 200):
                                try:
                                    toast_text = (loc.inner_text(timeout=500) or "")[:80]
                                    toast_outer = (loc.evaluate("el => el.outerHTML") or "")[:250]
                                except Exception:
                                    toast_text = ""; toast_outer = ""
                                toast_detected = True
                                toast_info = f"toast:{tsel}|text={toast_text!r}|outer={toast_outer!r}"
                                logger.info(f"[success_check] toast 検出: {toast_info}")
                                break
                        except Exception:
                            continue

                # link 新規出現 check (count > baseline)
                if not link_new_visible:
                    try:
                        now_count = self.page.locator(_SUCCESS_LINK_SEL).count()
                        if now_count > baseline_link_count:
                            link_new_visible = True
                            # 最新 (visible) link の href + outerHTML
                            for i in range(now_count):
                                cand = self.page.locator(_SUCCESS_LINK_SEL).nth(i)
                                try:
                                    if _visible_within(cand, 200):
                                        success_link_href = cand.get_attribute("href", timeout=500)
                                        success_outer_html = (cand.evaluate("el => el.outerHTML") or "")[:250]
                                        link_inner = (cand.inner_text(timeout=500) or "")[:80]
                                        logger.info(f"[success_check] link 新規出現: count {baseline_link_count}→{now_count}, href={success_link_href}, text={link_inner!r}")
                                        break
                                except Exception:
                                    continue
                    except Exception:
                        pass

                # AND 条件: 両方揃ったら成功確定
                if toast_detected and link_new_visible:
                    success_evidence = (
                        f"AND(toast=True,link_new_count={baseline_link_count}+|"
                        f"href={success_link_href!r}|outer={success_outer_html!r})"
                    )
                    break
                _time.sleep(0.5)

            if not success_evidence:
                # 30s timeout
                final_url = self.page.url
                missing = []
                if not toast_detected: missing.append("toast")
                if not link_new_visible: missing.append("link_new")
                result["error"] = f"投稿成功 不検出 (30s timeout, missing={missing}): url={final_url[:120]}"
                logger.error(f"投稿失敗 (missing={missing}): url={final_url}")
                result["screenshots"].append(str(self.bm.take_screenshot("06_no_success_signals")))
                return result

            # (C) URL host ガード - allowlist 厳格化 + 例外時 fail-safe (Codex 16 #5/#6)
            from urllib.parse import urlparse
            ALLOWED_HOSTS = {"room.rakuten.co.jp", "sp.room.rakuten.co.jp"}
            final_url = self.page.url
            try:
                parsed = urlparse(final_url)
                if parsed.scheme not in ("http", "https") or parsed.hostname not in ALLOWED_HOSTS:
                    result["error"] = f"投稿成功検出後 URL 異常 host={parsed.hostname} scheme={parsed.scheme}: {final_url[:120]}"
                    logger.error(f"投稿失敗 (URL host allowlist 外 + 成功検出 無視): {final_url}")
                    result["screenshots"].append(str(self.bm.take_screenshot("06_url_host_fail")))
                    return result
            except Exception as e:
                # Codex 16 #6: 例外時 fail-safe で失敗扱い
                result["error"] = f"投稿成功検出後 URL parse 失敗 fail-safe: {e}"
                logger.error(f"投稿失敗 (URL parse 例外 fail-safe): {e}")
                result["screenshots"].append(str(self.bm.take_screenshot("06_url_parse_fail")))
                return result

            # (D) link href 妥当性: ^https?://(sp.)?room.rakuten.co.jp/ にマッチで room_url 設定
            # CEO 5/17 dry-run 結果: Rakuten ROOM 現代 UI の 'my ROOM を見る' button は
            #   SPA で href="" の anchor (JS で表示切替・遷移なし) = href から room_url 取得不可
            #   → toast + link 新規出現 の AND 条件が成立すれば success=True 確定
            #   → href 取れた場合のみ room_url 設定 (重複判定の補助)
            #   → href 取れない場合は room_url=None + degraded_success=True (ただし success=True 維持)
            #
            # 「room_url なし = success=False 降格」は 過剰防御 で実成功を取りこぼす false negative.
            # AND 2重条件 (toast + link 新規出現) は虚偽成功遮断に十分.
            import re as _re_url
            link_url_re = _re_url.compile(r"^https?://(?:sp\.)?room\.rakuten\.co\.jp/")
            room_url = None
            if success_link_href:
                if success_link_href.startswith("/"):
                    full_href = f"https://room.rakuten.co.jp{success_link_href}"
                else:
                    full_href = success_link_href
                if link_url_re.match(full_href):
                    room_url = full_href

            # ==========================================================
            # (E) 真の成功確定: my ROOM 商品数 +1 確認 (Codex 18/19回目 二相確定)
            # ==========================================================
            # Codex 19 #4: 60s→180s + 指数バックオフ (Rakuten 反映遅延考慮)
            # Codex 19 #3: try/finally で check_page 確実 close
            # toast + link 検出だけでは false success risk (5/12-5/17 立証)
            real_verify_ok = False
            real_verify_msg = "skipped (baseline 未取得)"
            check_count = None
            if my_room_baseline and my_room_baseline.get("item_count") is not None and _fetch_my_room_fingerprint:
                baseline_count = my_room_baseline["item_count"]
                import time as _time
                ctx = self.page.context
                # 180s 以内 + 指数バックオフ (5s, 10s, 15s, 20s, 25s, 30s ...)
                verify_deadline = _time.time() + 180.0
                check_count = baseline_count
                wait_sec = 5.0
                attempt = 0
                while _time.time() < verify_deadline:
                    attempt += 1
                    check_page = None
                    try:
                        check_page = ctx.new_page()
                        fp_now = _fetch_my_room_fingerprint(check_page)
                        new_count = fp_now.get("item_count")
                        if new_count is not None:
                            check_count = new_count
                            logger.info(f"[verify+1 attempt={attempt}] items {baseline_count}→{check_count}")
                            if check_count > baseline_count:
                                real_verify_ok = True
                                real_verify_msg = f"items {baseline_count}→{check_count} (+{check_count-baseline_count}) attempt={attempt}"
                                break
                    except Exception as e:
                        logger.warning(f"[verify+1 attempt={attempt}] check 例外 (retry): {e}")
                    finally:
                        if check_page:
                            try:
                                check_page.close()
                            except Exception:
                                pass
                    _time.sleep(wait_sec)
                    wait_sec = min(wait_sec * 1.3, 30.0)  # 指数バックオフ 最大 30s
                if not real_verify_ok:
                    real_verify_msg = f"items {baseline_count}→{check_count} (180s + {attempt} attempts でも +1 未確認・Rakuten 側拒否疑い)"
            else:
                real_verify_msg = "baseline 未取得 (profile 異常 or import 失敗)"

            if not real_verify_ok:
                # 真の成功検証 失敗 → false success として失敗扱い
                result["error"] = f"投稿成功 toast/link は検出されたが ROOM 商品数 +1 未確認: {real_verify_msg}"
                result["success"] = False
                result["false_success_suspect"] = True
                result["toast_link_evidence"] = success_evidence
                result["verify_msg"] = real_verify_msg
                logger.error(f"投稿失敗 (FALSE SUCCESS 疑い・商品数 +1 未確認): {real_verify_msg}")
                result["screenshots"].append(str(self.bm.take_screenshot("06_false_success_caught")))
                return result

            # 真の成功確定 (二相 AND OK)
            result["room_url"] = room_url
            result["success"] = True
            result["success_evidence"] = success_evidence
            result["verify_msg"] = real_verify_msg
            if not room_url:
                result["degraded_success"] = True
            logger.info(f"投稿真の成功 confirmed! evidence={success_evidence} | verify={real_verify_msg} | room_url={room_url}")
            result["screenshots"].append(str(self.bm.take_screenshot("06_success_verified")))

            # ==========================================================
            # (F) v9.2: read-after-post (CEO 5/22 指示 / 5/17・5/10 空 comment 投稿事案)
            # 投稿成功 confirmed 後、ROOM 公開ページで comment 表示を実検証.
            # 不一致 (空 or 省略 or 違う) なら success=True 維持しつつ
            # auto_fix_pending flag を立て、queue_executor が pending_comment_edit=1 を SET.
            # 関連: memory/comment_full_text_rule.md
            # ==========================================================
            expected_comment = _normalize(review_text)
            result["post_after_check"] = {"performed": False}
            try:
                ctx = self.page.context
                after_page = ctx.new_page()
                try:
                    # 検証先 URL 決定: room_url 取得済ならそれ / 無ければ my/items から直近 1 件
                    target_url = room_url
                    if not target_url:
                        try:
                            after_page.goto("https://room.rakuten.co.jp/my/items",
                                            timeout=30000, wait_until="domcontentloaded")
                            self._human_delay(2.0, 3.0)
                            latest = after_page.evaluate(r"""() => {
                                const a = document.querySelector('a[href*="/items/"]');
                                if (!a) return null;
                                const h = a.getAttribute('href') || '';
                                return h.startsWith('http') ? h : ('https://room.rakuten.co.jp' + h);
                            }""")
                            target_url = latest
                        except Exception as _ee:
                            logger.warning(f"[post_after_check] my/items 経由 URL 取得失敗: {_ee}")

                    if target_url:
                        after_page.goto(target_url, timeout=30000,
                                          wait_until="domcontentloaded")
                        try:
                            after_page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        self._human_delay(2.0, 3.0)

                        # comment 表示テキスト取得 (複数 selector を試す)
                        displayed_comment = ""
                        sel_used = None
                        for sel in ['[data-test*="comment"]', '.item-comment',
                                    '[class*="ItemComment"]', '[class*="item_comment"]',
                                    '[class*="comment-body"]', 'p.comment',
                                    '[class*="description"]']:
                            try:
                                loc = after_page.locator(sel).first
                                if loc.count() > 0:
                                    t = loc.text_content(timeout=2000) or ""
                                    if t.strip():
                                        displayed_comment = t
                                        sel_used = sel
                                        break
                            except Exception:
                                continue
                        # Codex v9.2 #9: HTML 起因の不可視文字も吸収する緩い正規化で再比較
                        displayed_norm = _normalize(displayed_comment)
                        displayed_loose = _normalize_loose(displayed_comment)
                        expected_loose = _normalize_loose(review_text)
                        check_info = {
                            "performed": True,
                            "target_url": target_url,
                            "selector_used": sel_used,
                            "displayed_len_strict": len(displayed_norm),
                            "displayed_len_loose": len(displayed_loose),
                            "expected_len_strict": len(expected_comment),
                            "expected_len_loose": len(expected_loose),
                            "displayed_head": displayed_loose[:60],
                            "expected_head": expected_loose[:60],
                        }
                        # 判定:
                        # (1) strict 完全一致 → EXACT_MATCH
                        # (2) loose 完全一致 (空白/nbsp 違いのみ) → EXACT_LOOSE_MATCH
                        # (3) 末尾 30 char loose 一致 + 表示長 >= 期待 * 0.9 → TAIL_MATCH_OK
                        # (4) その他 → EMPTY_OR_TRUNCATED + degraded
                        tail_min = max(30, min(50, len(expected_loose) // 3))
                        if displayed_norm == expected_comment:
                            check_info["verdict"] = "EXACT_MATCH"
                        elif displayed_loose == expected_loose:
                            check_info["verdict"] = "EXACT_LOOSE_MATCH"
                        elif (expected_loose
                              and len(displayed_loose) >= len(expected_loose) * 0.9
                              and displayed_loose.endswith(expected_loose[-tail_min:])):
                            check_info["verdict"] = "TAIL_MATCH_OK"
                        else:
                            check_info["verdict"] = "EMPTY_OR_TRUNCATED"
                            check_info["auto_fix_pending"] = True
                            # Codex v9.2 review #8: success=True 維持しつつ degraded を強く立てる.
                            # KPI/集計で「成功」に数えないよう queue_executor 側に通知.
                            result["degraded_empty_comment"] = True
                            logger.error(
                                f"[post_after_check] 投稿後 ROOM 表示 comment 不一致 (DEGRADED): "
                                f"displayed_loose_len={len(displayed_loose)} "
                                f"expected_loose_len={len(expected_loose)} "
                                f"displayed_head={displayed_loose[:60]!r} "
                                f"expected_head={expected_loose[:60]!r} "
                                f"selector={sel_used}"
                            )
                            try:
                                result["screenshots"].append(
                                    str(self.bm.take_screenshot("07_post_after_truncated"))
                                )
                            except Exception:
                                pass
                        result["post_after_check"] = check_info
                    else:
                        result["post_after_check"] = {
                            "performed": False,
                            "error": "target_url 不明 (room_url None かつ my/items 取得失敗)",
                        }
                finally:
                    try:
                        after_page.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[post_after_check] 例外 (post 自体は success 維持): {e}")
                result["post_after_check"] = {
                    "performed": False,
                    "error": str(e)[:200],
                }

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
