# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — Customizer に CSS_V3 を流し込み (v2 と同じ手法)"""
from __future__ import annotations
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
sys.path.insert(0, str(BASE / "content"))

from wp_session import wp_browser, _log, _snap  # noqa
from custom_css_v3 import CSS_V3  # noqa


def deploy_css():
    with wp_browser(headless=True) as (ctx, page):
        _log("===== Customizer に v3 Custom CSS を流し込み =====")
        page.goto("https://www.kapibaran.com/wp-admin/customize.php",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#customize-controls", timeout=60000)
        time.sleep(3)
        _snap(page, "v3css_01_customizer_loaded")

        result = page.evaluate(
            """(css) => {
                if (typeof wp === 'undefined' || !wp.customize) return {error: 'no_customize'};
                const theme = (wp.customize.settings && wp.customize.settings.theme && wp.customize.settings.theme.stylesheet) || null;
                const candidates = [
                    'custom_css',
                    theme ? ('custom_css[' + theme + ']') : null,
                ].filter(Boolean);
                const tried = [];
                for (const key of candidates) {
                    const s = wp.customize(key);
                    if (s) {
                        s.set(css);
                        tried.push({key: key, applied: true});
                        return {ok: true, key: key, length: css.length, tried: tried};
                    }
                    tried.push({key: key, applied: false});
                }
                const allCssKeys = [];
                wp.customize.each((s, k) => { if (k && k.indexOf('custom_css') !== -1) allCssKeys.push(k); });
                for (const key of allCssKeys) {
                    const s = wp.customize(key);
                    if (s) { s.set(css); return {ok: true, key: key, length: css.length, scanned: allCssKeys}; }
                }
                return {error: 'no_custom_css_setting', tried: tried, scanned: allCssKeys};
            }""",
            CSS_V3,
        )
        _log(f"  set result: {result}")

        page.evaluate("() => { const b = document.querySelector('#save'); if (b) b.disabled = false; }")
        time.sleep(0.5)
        page.locator("#save").first.click(force=True)
        _log("公開ボタンクリック")
        time.sleep(8)
        _snap(page, "v3css_02_saved")
        _log("===== v3 CSS デプロイ完了 =====")


if __name__ == "__main__":
    deploy_css()
