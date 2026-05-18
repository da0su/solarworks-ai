# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — ジャーナル 5 記事 + アイキャッチ設定

- 既存投稿 (slug 一致 or タイトル一致) があれば更新
- なければ新規作成 (Journal カテゴリー)
- アイキャッチ画像を JR1〜JR5 に設定
- 旧 Uncategorized 投稿の draft 化は行わない (v3 範囲外)
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
from products_v3 import JOURNAL_POSTS  # noqa


def _ensure_category(rest: WPRest, name: str, slug: str) -> int:
    """カテゴリーが無ければ作成して term_id を返す"""
    r = rest.get("/wp/v2/categories", {"slug": slug, "per_page": 5})
    if isinstance(r, list) and r:
        return r[0]["id"]
    cr = rest.post("/wp/v2/categories", {"name": name, "slug": slug})
    if isinstance(cr, dict) and cr.get("id"):
        _log(f"  + category '{name}' (id={cr['id']})")
        return cr["id"]
    return 0


def _find_post(rest: WPRest, slug: str, title: str) -> dict | None:
    # 1) slug 一致
    r = rest.get("/wp/v2/posts", {"slug": slug, "status": "any", "per_page": 5})
    if isinstance(r, list) and r:
        return r[0]
    # 2) タイトル一致 (search)
    r2 = rest.get("/wp/v2/posts", {"search": title, "status": "any", "per_page": 20})
    if isinstance(r2, list):
        for p in r2:
            if p.get("title", {}).get("rendered", "").strip() == title.strip():
                return p
    return None


def main():
    media_file = BASE / "state" / "media_v3.json"
    if not media_file.exists():
        raise FileNotFoundError("先に deploy_v3_media.py を実行してください")
    raw_media = json.loads(media_file.read_text(encoding="utf-8"))
    media_ids = {k: v.get("id", 0) for k, v in raw_media.items()}

    post_map: dict[str, int] = {}
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== v3 ジャーナル 5 記事 デプロイ =====")

        journal_cat_id = _ensure_category(rest, "Journal", "journal")
        _log(f"  Journal category id = {journal_cat_id}")

        for j in JOURNAL_POSTS:
            slug = j["slug"]
            title = j["title"]
            content = f"<p class='kbv2-lead'>{j['excerpt']}</p>\n{j['body']}"
            existing = _find_post(rest, slug, title)
            payload = {
                "title": title,
                "slug": slug,
                "content": content,
                "status": "publish",
                "excerpt": j["excerpt"],
                "categories": [journal_cat_id] if journal_cat_id else [],
            }
            mid = media_ids.get(j["image_key"], 0)
            if mid:
                payload["featured_media"] = int(mid)

            if existing:
                pid = existing["id"]
                r = rest.patch(f"/wp/v2/posts/{pid}", payload)
                _log(f"  ↻ '{title}' update (id={pid})")
            else:
                r = rest.post("/wp/v2/posts", payload)
                pid = r.get("id") if isinstance(r, dict) else 0
                _log(f"  + '{title}' create (id={pid})")

            if isinstance(r, dict) and r.get("id"):
                post_map[slug] = r["id"]
                # featured_media が effectively セットされたか確認、未設定なら separate patch
                if mid and r.get("featured_media") != int(mid):
                    rest.patch(f"/wp/v2/posts/{r['id']}", {"featured_media": int(mid)})
            time.sleep(0.4)

        out = BASE / "state" / "journal_v3_deploy.json"
        out.write_text(json.dumps(post_map, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"===== ジャーナル完了 ({len(post_map)}/{len(JOURNAL_POSTS)}) -> {out} =====")


if __name__ == "__main__":
    main()
