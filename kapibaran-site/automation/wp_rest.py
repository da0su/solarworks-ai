# -*- coding: utf-8 -*-
"""WP REST API クライアント（Cookie + Nonce認証 / Application Password両対応）

Playwright で wp-admin にログインしてから、そのcookieとnonceを使って REST API を叩く。
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
from wp_session import wp_browser, _log, _snap, safe_goto


class WPRest:
    def __init__(self, ctx, page):
        self.ctx = ctx
        self.page = page
        # Playwright の APIRequestContext を使ってcookieを共有
        self.req = ctx.request
        self.base = "https://www.kapibaran.com/wp-json"
        self._nonce = None

    def fetch_nonce(self) -> str:
        """wp-admin から wpApiSettings.nonce を取得"""
        safe_goto(self.page, "https://www.kapibaran.com/wp-admin/")
        time.sleep(1)
        nonce = self.page.evaluate("() => (window.wpApiSettings && wpApiSettings.nonce) || null")
        if not nonce:
            # 別の方法: <link rel="https://api.w.org/" data-...> から取得
            # 確実な方法: edit_user用ノンスを生成しているページから取得
            nonce = self.page.evaluate("""
                () => {
                    // CSS 内に隠された wp.api.utils.WPApiSettings.nonce を探す
                    if (typeof wp !== 'undefined' && wp.api && wp.api.utils && wp.api.utils.WPApiSettings) {
                        return wp.api.utils.WPApiSettings.nonce;
                    }
                    return null;
                }
            """)
        if not nonce:
            # 最終手段: api-fetch nonce を inject
            self.page.evaluate("""
                async () => {
                    const r = await fetch('/wp-admin/admin-ajax.php?action=rest-nonce', {credentials: 'same-origin'});
                    window._inj_nonce = await r.text();
                }
            """)
            time.sleep(1)
            nonce = self.page.evaluate("() => window._inj_nonce || null")
        if not nonce:
            raise RuntimeError("X-WP-Nonce が取得できません")
        self._nonce = nonce
        _log(f"REST nonce 取得: {nonce[:8]}...")
        return nonce

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"X-WP-Nonce": self._nonce or self.fetch_nonce(), "Content-Type": "application/json; charset=utf-8"}
        if extra:
            h.update(extra)
        return h

    def _safe_json(self, r, label: str):
        try:
            return r.json()
        except Exception:
            txt = r.text()[:300]
            _log(f"  ⚠️ {label} non-JSON resp ({r.status}): {txt!r}")
            return {"_error": True, "status": r.status, "text": txt[:200]}

    def get(self, path: str, params: dict | None = None) -> dict | list:
        url = self.base + path
        r = self.req.get(url, headers=self._headers(), params=params or {})
        if not r.ok:
            _log(f"GET {path} failed: {r.status} {r.text()[:200]}")
        return self._safe_json(r, f"GET {path}")

    def post(self, path: str, data: dict) -> dict:
        url = self.base + path
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        r = self.req.post(url, headers=self._headers(), data=body)
        if not r.ok:
            _log(f"POST {path} failed: {r.status} {r.text()[:300]}")
        return self._safe_json(r, f"POST {path}")

    def patch(self, path: str, data: dict) -> dict:
        url = self.base + path
        h = self._headers()
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        r = self.req.post(url, headers=h, data=body)
        if not r.ok:
            _log(f"POST(update) {path} failed: {r.status} {r.text()[:300]}")
        return self._safe_json(r, f"PATCH {path}")

    def put(self, path: str, data: dict) -> dict:
        return self.patch(path, data)

    def delete(self, path: str) -> dict:
        url = self.base + path
        r = self.req.delete(url, headers=self._headers())
        return self._safe_json(r, f"DELETE {path}")


if __name__ == "__main__":
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        # 動作確認
        print("\n== settings ==")
        s = rest.get("/wp/v2/settings")
        for k in ["title", "description", "url", "language", "timezone_string", "show_on_front", "page_on_front"]:
            print(f"  {k}: {s.get(k)}")
        print("\n== pages ==")
        pages = rest.get("/wp/v2/pages", {"per_page": 50, "status": "any"})
        if isinstance(pages, list):
            for p in pages:
                print(f"  [{p['id']}] {p['title']['rendered']} (status={p['status']}, slug={p['slug']})")
        print("\n== posts ==")
        posts = rest.get("/wp/v2/posts", {"per_page": 50, "status": "any"})
        if isinstance(posts, list):
            for p in posts:
                print(f"  [{p['id']}] {p['title']['rendered']}")
        print("\n== media (limit 20) ==")
        media = rest.get("/wp/v2/media", {"per_page": 20})
        if isinstance(media, list):
            for m in media:
                print(f"  [{m['id']}] {m['title']['rendered']} ({m['source_url']})")
        print("\n== themes ==")
        try:
            t = rest.get("/wp/v2/themes?status=active")
            print(json.dumps(t, ensure_ascii=False, indent=2)[:500])
        except Exception as e:
            print(f"themes取得失敗: {e}")
