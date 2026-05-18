# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — 法令遵守版ページ一括 upsert

state/media_v3.json から media key -> URL を読み込み、
content/pages_v3.py の各ビルダーに渡してページを再生成、
WP REST API で upsert する。

- v2 で作成済の page slug を流用 (state/pages_v2_deploy.json)
- 旧 v1 商品 (footcare-device / smart-treadmill / shapewear-set) は draft 維持
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
from products_v3 import PRODUCTS  # noqa
from pages_v3 import (  # noqa
    build_top, build_about, build_products, build_product_detail,
    build_contact, build_tokushoho, build_privacy, build_terms,
)


LEGACY_PRODUCT_SLUGS = [
    "footcare-device",
    "smart-treadmill",
    "shapewear-set",
]


def _load_media_urls() -> dict:
    f = BASE / "state" / "media_v3.json"
    if not f.exists():
        raise FileNotFoundError(
            f"{f} が見つかりません。先に deploy_v3_media.py を実行してください。"
        )
    raw = json.loads(f.read_text(encoding="utf-8"))
    return {k: v.get("url", "") for k, v in raw.items()}


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
        return 0
    pid = existing[0]["id"]
    rest.patch(f"/wp/v2/pages/{pid}", {"status": status})
    _log(f"  · {slug} (id={pid}) -> status={status}")
    return pid


def set_featured_image(rest: WPRest, page_id: int, media_id: int):
    if not page_id or not media_id:
        return
    r = rest.patch(f"/wp/v2/pages/{page_id}", {"featured_media": int(media_id)})
    if isinstance(r, dict) and r.get("featured_media") == int(media_id):
        _log(f"  ★ featured_media set page={page_id} media={media_id}")


def main():
    media_urls = _load_media_urls()
    _log(f"media_urls: {len(media_urls)} keys")

    # featured_media 用に id も load
    raw_media = json.loads((BASE / "state" / "media_v3.json").read_text(encoding="utf-8"))
    media_ids = {k: v.get("id", 0) for k, v in raw_media.items()}

    TOP_LEVEL_PAGES = [
        {"slug": "home",      "title": "Home",
         "content": build_top(media_urls)},
        {"slug": "about",     "title": "About",
         "content": build_about(media_urls)},
        {"slug": "products",  "title": "プロダクト",
         "content": build_products(media_urls)},
        {"slug": "contact",   "title": "サポート",
         "content": build_contact(media_urls)},
        {"slug": "tokushoho", "title": "特定商取引法に基づく表記",
         "content": build_tokushoho()},
        {"slug": "terms",     "title": "利用規約",
         "content": build_terms()},
    ]

    slug_to_id: dict[str, int] = {}
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== KAPIBARAN v3 ページ一括更新 開始 =====")

        # 1) 親なしページ
        for d in TOP_LEVEL_PAGES:
            pid = upsert_page(rest, d["slug"], d["title"], d["content"])
            if pid:
                slug_to_id[d["slug"]] = pid
            time.sleep(0.4)

        # About に featured_media を設定 (OGP/SEO 用にも使われる)
        about_pid = slug_to_id.get("about", 0)
        if about_pid and media_ids.get("ABOUT"):
            set_featured_image(rest, about_pid, media_ids["ABOUT"])

        # Home に hero を featured_media として設定
        home_pid = slug_to_id.get("home", 0)
        if home_pid and media_ids.get("HERO"):
            set_featured_image(rest, home_pid, media_ids["HERO"])

        # 2) 商品詳細ページ
        products_pid = slug_to_id.get("products", 0)
        for p in PRODUCTS:
            content = build_product_detail(p, media_urls)
            pid = upsert_page(
                rest, slug=p["slug"], title=p["full_name"],
                content=content, parent_id=products_pid,
            )
            if pid:
                slug_to_id[p["slug"]] = pid
                # 商品メイン画像を featured_media に
                main_key = p["main_image_key"]
                if media_ids.get(main_key):
                    set_featured_image(rest, pid, media_ids[main_key])
            time.sleep(0.4)

        # 3) 旧 v1 商品 draft 化
        _log("旧 v1 商品 draft 維持:")
        for s in LEGACY_PRODUCT_SLUGS:
            set_page_status(rest, s, "draft")
            time.sleep(0.3)

        # 4) ホームページ設定
        if home_pid:
            r = rest.post("/wp/v2/settings", {
                "show_on_front": "page",
                "page_on_front": home_pid,
            })
            _log(f"  ← settings: show_on_front={r.get('show_on_front') if isinstance(r, dict) else '?'}, "
                 f"page_on_front={r.get('page_on_front') if isinstance(r, dict) else '?'}")

        out = BASE / "state" / "pages_v3_deploy.json"
        out.write_text(json.dumps(slug_to_id, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"===== 完了 ({len(slug_to_id)} pages) -> {out} =====")


if __name__ == "__main__":
    main()
