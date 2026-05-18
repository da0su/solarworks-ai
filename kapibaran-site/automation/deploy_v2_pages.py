# -*- coding: utf-8 -*-
"""KAPIBARAN v2 ページ全面リビルド

CEO 指示 (5/18) に基づき、実商品 5SKU + Coming Soon 構成で
全ページを WP REST API 経由で作成・更新する。

- TOP / About / Products / Contact / 特商法 / Privacy / 利用規約 (7 pages)
- 商品詳細 (Products 配下): KB-FC01 / KB-TM01 (2 pages)
- 旧 v1 で残った商品詳細 (3 ページ) は draft 化
- 旧 v1 で残った Journal 系投稿はそのまま残置（CEO 指示外）

実行:
    cd kapibaran-site
    python automation/deploy_v2_pages.py
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
sys.path.insert(0, str(BASE / "content"))

from wp_session import wp_browser, _log  # noqa
from wp_rest import WPRest  # noqa
from products_v2 import PRODUCTS  # noqa
from pages_v2 import (  # noqa
    build_top, build_about, build_products, build_product_detail,
    build_contact, build_tokushoho, build_privacy, build_terms,
)


# ---- 既存 v1 で残った旧商品 page (slug) — draft 化対象 ----
LEGACY_PRODUCT_SLUGS = [
    "footcare-device",
    "smart-treadmill",
    "shapewear-set",
]


# ---- 親なしページ定義 ----
TOP_LEVEL_PAGES = [
    {"slug": "home",      "title": "Home",        "content_fn": build_top},
    {"slug": "about",     "title": "About",       "content_fn": build_about},
    {"slug": "products",  "title": "プロダクト",   "content_fn": build_products},
    {"slug": "contact",   "title": "サポート",     "content_fn": build_contact},
    {"slug": "tokushoho", "title": "特定商取引法に基づく表記",
                                                "content_fn": build_tokushoho},
    {"slug": "terms",     "title": "利用規約",     "content_fn": build_terms},
]


def upsert_page(rest: WPRest, slug: str, title: str, content: str,
                status: str = "publish", parent_id: int = 0) -> int:
    existing = rest.get("/wp/v2/pages", {"slug": slug, "status": "any"})
    payload = {"title": title, "slug": slug, "content": content, "status": status}
    if parent_id:
        payload["parent"] = parent_id
    if isinstance(existing, list) and existing:
        pid = existing[0]["id"]
        _log(f"  ↻ '{title}' update (id={pid})")
        r = rest.patch(f"/wp/v2/pages/{pid}", payload)
    else:
        _log(f"  + '{title}' create")
        r = rest.post("/wp/v2/pages", payload)
    if isinstance(r, dict) and r.get("id"):
        return r["id"]
    _log(f"    ⚠️ {r}")
    return 0


def set_page_status(rest: WPRest, slug: str, status: str) -> int:
    existing = rest.get("/wp/v2/pages", {"slug": slug, "status": "any"})
    if not (isinstance(existing, list) and existing):
        _log(f"  (skip) {slug} 旧ページなし")
        return 0
    pid = existing[0]["id"]
    r = rest.patch(f"/wp/v2/pages/{pid}", {"status": status})
    _log(f"  · {slug} (id={pid}) -> status={status}")
    return pid if isinstance(r, dict) and r.get("id") else 0


def main():
    slug_to_id = {}
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== KAPIBARAN v2 ページ一括更新 開始 =====")

        # 1) 親なしページを upsert
        for d in TOP_LEVEL_PAGES:
            pid = upsert_page(rest, d["slug"], d["title"], d["content_fn"]())
            if pid:
                slug_to_id[d["slug"]] = pid
            time.sleep(0.4)

        # 2) Products 配下に SKU 詳細ページを upsert
        products_pid = slug_to_id.get("products", 0)
        for p in PRODUCTS:
            pid = upsert_page(
                rest,
                slug=p["slug"],
                title=p["full_name"],
                content=build_product_detail(p),
                parent_id=products_pid,
            )
            if pid:
                slug_to_id[p["slug"]] = pid
            time.sleep(0.4)

        # 3) 旧 v1 残骸 (3 商品詳細) を draft 化
        _log("旧 v1 商品詳細を draft 化:")
        for legacy_slug in LEGACY_PRODUCT_SLUGS:
            set_page_status(rest, legacy_slug, "draft")
            time.sleep(0.3)

        # 4) ホームページ設定: page_on_front = home, show_on_front = page
        home_id = slug_to_id.get("home", 0)
        if home_id:
            _log(f"フロントページを home (id={home_id}) に設定")
            r = rest.post("/wp/v2/settings", {
                "show_on_front": "page",
                "page_on_front": home_id,
            })
            if isinstance(r, dict):
                _log(f"  ← settings: show_on_front={r.get('show_on_front')}, "
                     f"page_on_front={r.get('page_on_front')}")

        # 5) state 保存
        (BASE / "state" / "pages_v2_deploy.json").write_text(
            json.dumps(slug_to_id, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        _log(f"===== 完了 (slug->id 件数: {len(slug_to_id)}) =====")
        for s, i in slug_to_id.items():
            _log(f"  {s} = {i}")


if __name__ == "__main__":
    main()
