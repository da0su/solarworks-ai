# -*- coding: utf-8 -*-
"""KAPIBARAN v2 メニュー再構築

ヘッダーメニュー (menus=2) / フッターメニュー (menus=3) を
v2 ページ構成に合わせて再構築する。

メニュー ID (2 / 3) は v1 で既に作成済を流用 (state/menu.json 参照)。
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))

from wp_session import wp_browser, _log  # noqa
from wp_rest import WPRest  # noqa


def sync_menu(rest: WPRest, menu_id: int, new_items: list):
    """既存項目を PATCH で更新、不足分は POST 追加、余りは draft 化"""
    existing = rest.get("/wp/v2/menu-items", {"menus": menu_id, "per_page": 100})
    existing_sorted = sorted(existing, key=lambda x: x.get("menu_order", 999)) \
        if isinstance(existing, list) else []

    for i, it in enumerate(new_items):
        payload = {
            "menus": menu_id,
            "title": it["title"],
            "menu_order": i + 1,
            "status": "publish",
        }
        if it.get("page_id"):
            payload["object"] = "page"
            payload["type"] = "post_type"
            payload["object_id"] = it["page_id"]
            payload["url"] = ""
        elif it.get("url"):
            payload["type"] = "custom"
            payload["url"] = it["url"]
            payload["object"] = "custom"
            payload["object_id"] = 0
        if i < len(existing_sorted):
            mid = existing_sorted[i]["id"]
            rest.patch(f"/wp/v2/menu-items/{mid}", payload)
            _log(f"  ↻ menu_id={menu_id} item#{mid} → '{it['title']}'")
        else:
            r = rest.post("/wp/v2/menu-items", payload)
            new_id = r.get("id") if isinstance(r, dict) else "?"
            _log(f"  + menu_id={menu_id} item new → '{it['title']}' (id={new_id})")
        time.sleep(0.2)

    # 余ったアイテムを draft 化
    if len(existing_sorted) > len(new_items):
        for extra in existing_sorted[len(new_items):]:
            mid = extra["id"]
            rest.patch(f"/wp/v2/menu-items/{mid}", {
                "menus": menu_id,
                "title": "(unused)",
                "type": "custom",
                "url": "#",
                "menu_order": 999,
                "status": "draft",
            })
            _log(f"  ⏷ menu_id={menu_id} item#{mid} → draft (unused)")


def rebuild_menus():
    state_file = BASE / "state" / "pages_v2_deploy.json"
    if not state_file.exists():
        raise FileNotFoundError("先に deploy_v2_pages.py を実行してください")
    PAGES = json.loads(state_file.read_text(encoding="utf-8"))

    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== KAPIBARAN v2 メニュー再構築 =====")

        # Header menu
        main_items = [
            {"title": "ブランド",       "page_id": PAGES.get("about")},
            {"title": "プロダクト",     "page_id": PAGES.get("products")},
            {"title": "フットケア",     "page_id": PAGES.get("footcare-kb-fc01")},
            {"title": "ホームフィットネス", "page_id": PAGES.get("treadmill-kb-tm01")},
            {"title": "サポート",       "page_id": PAGES.get("contact")},
        ]
        main_items = [it for it in main_items if it.get("page_id")]
        _log("Header menu (menus=2)")
        sync_menu(rest, 2, main_items)

        # Footer menu
        # 既存の privacy-policy ID を取得 (v1 で id=3 を更新済の前提)
        privacy_existing = rest.get("/wp/v2/pages", {"slug": "privacy-policy", "status": "any"})
        privacy_id = privacy_existing[0]["id"] if isinstance(privacy_existing, list) and privacy_existing else None

        footer_items = [
            {"title": "ブランド",                  "page_id": PAGES.get("about")},
            {"title": "プロダクト",                "page_id": PAGES.get("products")},
            {"title": "サポート",                  "page_id": PAGES.get("contact")},
            {"title": "プライバシーポリシー",      "page_id": privacy_id},
            {"title": "利用規約",                  "page_id": PAGES.get("terms")},
            {"title": "特定商取引法に基づく表記",  "page_id": PAGES.get("tokushoho")},
        ]
        footer_items = [it for it in footer_items if it.get("page_id")]
        _log("Footer menu (menus=3)")
        sync_menu(rest, 3, footer_items)

        _log("===== メニュー再構築 完了 =====")


if __name__ == "__main__":
    rebuild_menus()
