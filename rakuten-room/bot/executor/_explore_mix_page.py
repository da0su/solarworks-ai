"""mix page DOM 探索 script (CEO 5/17 投稿復旧指示).

【目的】 「Name 空白 NG」 validation の原因 = どの field が「Name」を要求しているのか特定.
chrome_profile_post (login 済) で実際の mix ページを開いて DOM を全 dump.

【使い方】
    python rakuten-room/bot/executor/_explore_mix_page.py

【output】
    state/mix_page_dom_dump.json
    state/mix_page_screenshots/*.png
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

import config
from executor.browser_manager import BrowserManager
from playwright.sync_api import Page


def _take_screenshot(page: Page, label: str) -> Path:
    out = ROOT.parent / "state" / "mix_page_screenshots"
    out.mkdir(parents=True, exist_ok=True)
    fn = out / f"{datetime.now().strftime('%H%M%S')}_{label}.png"
    page.screenshot(path=str(fn), full_page=True)
    return fn


def _dump_all_inputs(page: Page) -> list:
    """ページ内の全 input/textarea/select/[contenteditable] を抽出."""
    return page.evaluate("""() => {
        const elements = document.querySelectorAll('input, textarea, select, [contenteditable]');
        const out = [];
        elements.forEach((el, idx) => {
            const rect = el.getBoundingClientRect();
            out.push({
                index: idx,
                tag: el.tagName,
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                value: (el.value || '').slice(0, 100),
                className: el.className || '',
                visible: rect.width > 0 && rect.height > 0 && rect.top >= 0,
                rect: {x: Math.round(rect.x), y: Math.round(rect.y),
                       w: Math.round(rect.width), h: Math.round(rect.height)},
                outerHTML: el.outerHTML.slice(0, 300),
                required: el.required || false,
                contentEditable: el.getAttribute('contenteditable') || '',
            });
        });
        return out;
    }""")


def _dump_buttons(page: Page) -> list:
    """ページ内の全 button + [role=button] を抽出."""
    return page.evaluate("""() => {
        const elements = document.querySelectorAll('button, [role="button"], a[href*="javascript"], input[type="submit"]');
        const out = [];
        elements.forEach((el, idx) => {
            const rect = el.getBoundingClientRect();
            out.push({
                index: idx,
                tag: el.tagName,
                type: el.type || '',
                text: (el.innerText || el.value || '').slice(0, 80).trim(),
                className: el.className || '',
                disabled: el.disabled || false,
                visible: rect.width > 0 && rect.height > 0,
                rect: {x: Math.round(rect.x), y: Math.round(rect.y)},
                outerHTML: el.outerHTML.slice(0, 250),
            });
        });
        return out;
    }""")


def _dump_visible_text(page: Page) -> str:
    return page.evaluate("() => document.body.innerText.slice(0, 5000)")


def main():
    print(f"[{datetime.now()}] start mix page exploration")
    out_path = ROOT.parent / "state" / "mix_page_dom_dump.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # サンプル商品を 1 つ用意 (5/17 queued の 1 件目)
    item_url = "https://item.rakuten.co.jp/asiabnc/shape_101/"  # 任意の商品 (3:00 Batch4 で失敗した 1 件目に近い)

    bm = BrowserManager(action="post")
    bm.start()

    # login 確認
    status = bm.check_login_status()
    print(f"login_status: {status}")
    if not status.get("logged_in"):
        print("ERR: not logged in")
        bm.stop()
        return 1

    dump = {"explored_at": datetime.now().isoformat(), "phases": {}}

    page = bm.page
    # Phase 1: 商品ページ → シェア → ROOM に投稿
    try:
        page.goto(item_url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _take_screenshot(page, "01_product_page")
        print("[phase1] product page opened")

        # シェアボタン click
        share = page.locator('a:has-text("シェア"), button:has-text("シェア"), .susumeru-roomShareButton').first
        share.wait_for(timeout=10000)
        share.click()
        time.sleep(2)
        _take_screenshot(page, "02_after_share_click")

        # ROOM に投稿 link
        room_link = page.locator('a[href*="room.rakuten.co.jp/mix"]').first
        mix_url = room_link.get_attribute("href")
        print(f"[phase1] mix_url: {mix_url}")
        dump["mix_url"] = mix_url

        # mix ページ遷移
        page.goto(mix_url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3)  # angular/react app 読込待ち
        _take_screenshot(page, "03_mix_page_initial")
        print(f"[phase1] mix page loaded, url={page.url}")

        # Phase 2: 初期 mix page の全 DOM dump
        dump["phases"]["initial"] = {
            "url": page.url,
            "title": page.title(),
            "inputs": _dump_all_inputs(page),
            "buttons": _dump_buttons(page),
            "visible_text": _dump_visible_text(page),
        }
        print(f"[phase2] initial dump: {len(dump['phases']['initial']['inputs'])} inputs, {len(dump['phases']['initial']['buttons'])} buttons")
    except Exception as e:
        print(f"[ERR phase1/2] {e}")
        dump["error_phase1"] = str(e)
        try:
            _take_screenshot(page, "ERR_phase1")
        except Exception:
            pass

    # Phase 3: 投稿ボタン click 後 modal 表示状態 dump
    try:
        # 完了 / 投稿 ボタンを探して click
        submit_candidates = [
            'button:has-text("完了")',
            'button:has-text("投稿")',
            'button:has-text("コレ")',
            'button.collect-btn',
            'button[type="submit"]',
        ]
        clicked = False
        for sel in submit_candidates:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    print(f"[phase3] submit btn found: {sel}")
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            time.sleep(3)
            _take_screenshot(page, "04_after_submit_click")
            dump["phases"]["after_submit"] = {
                "url": page.url,
                "inputs": _dump_all_inputs(page),
                "buttons": _dump_buttons(page),
                "visible_text": _dump_visible_text(page),
                "modal_text": page.evaluate("""() => {
                    // モーダル/dialog テキスト
                    const els = document.querySelectorAll('[role=\"dialog\"], .modal, .popup, [class*=\"alert\"], [class*=\"error\"]');
                    return Array.from(els).map(e => e.innerText.slice(0, 500));
                }"""),
            }
            print(f"[phase3] after-submit dump: {len(dump['phases']['after_submit']['inputs'])} inputs, modal_text: {dump['phases']['after_submit']['modal_text']}")
        else:
            print("[phase3] no submit btn found")
    except Exception as e:
        print(f"[ERR phase3] {e}")
        dump["error_phase3"] = str(e)

    out_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] dump saved: {out_path}")
    print(f"       screenshots: {ROOT.parent / 'state' / 'mix_page_screenshots'}")

    bm.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
