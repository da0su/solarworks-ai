# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — メディアライブラリへ画像を投入

materials_v3/KAPIBARAN_assets_v2/ 配下の画像 17 点を WP REST API 経由で
メディアライブラリに upload し、key -> attachment_id / source_url を
state/media_v3.json に保存する。

冪等: 既にタイトル一致のメディアがあればそれを再利用。
"""
from __future__ import annotations
import sys
import json
import mimetypes
import time
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))

from wp_session import wp_browser, _log  # noqa
from wp_rest import WPRest  # noqa


MATERIALS_DIR = BASE / "materials_v3" / "KAPIBARAN_assets_v2"


# key -> (relative path under MATERIALS_DIR, WP media title)
ASSETS = {
    # Hero
    "HERO":        ("01_hero/hero_top_family_living.jpg",            "KAPIBARAN Hero - Family Living"),
    # Categories
    "CAT_FOOT":    ("02_categories/cat_01_foot_care.jpg",            "KAPIBARAN Category - Foot Care"),
    "CAT_FIT":     ("02_categories/cat_02_home_fitness.jpg",         "KAPIBARAN Category - Home Fitness"),
    "CAT_BODY":    ("02_categories/cat_03_body_care_comingsoon.jpg", "KAPIBARAN Category - Body Care (Coming Soon)"),
    "CAT_SHAPE":   ("02_categories/cat_04_body_shaping_comingsoon.jpg", "KAPIBARAN Category - Body Shaping (Coming Soon)"),
    # Products - Footcare
    "PROD_FC_NV":  ("03_products/footcare/KB-FC01-NV_navy.jpg",      "KAPIBARAN KB-FC01 Navy"),
    "PROD_FC_BE":  ("03_products/footcare/KB-FC01-BE_beige.jpg",     "KAPIBARAN KB-FC01 Beige"),
    # Products - Treadmill
    "PROD_TM_OR":  ("03_products/treadmill/KB-TM01-OR_orange.jpg",   "KAPIBARAN KB-TM01 Orange"),
    "PROD_TM_WH":  ("03_products/treadmill/KB-TM01-WH_white.jpg",    "KAPIBARAN KB-TM01 White"),
    "PROD_TM_BL":  ("03_products/treadmill/KB-TM01-BL_blue.jpg",     "KAPIBARAN KB-TM01 Blue"),
    # Journal
    "JR1":         ("04_journal/journal_01_foot_self_care.jpg",      "KAPIBARAN Journal - Foot Self Care"),
    "JR2":         ("04_journal/journal_02_home_fitness.jpg",        "KAPIBARAN Journal - Home Fitness"),
    "JR3":         ("04_journal/journal_03_premium_daily_life.jpg",  "KAPIBARAN Journal - Premium Daily Life"),
    "JR4":         ("04_journal/journal_04_material_selection.jpg",  "KAPIBARAN Journal - Material Selection"),
    "JR5":         ("04_journal/journal_05_adult_fitness.jpg",       "KAPIBARAN Journal - Adult Fitness"),
    # About
    "ABOUT":       ("05_about/about_hero_hands.jpg",                 "KAPIBARAN About Hero - Hands"),
    # OGP
    "OGP":         ("06_ogp/ogp_kapibaran_share.jpg",                "KAPIBARAN OGP Share"),
}


def _find_existing_media(rest: WPRest, title: str, filename: str):
    """既存メディアを title または filename で検索"""
    # 1) search by title
    r = rest.get("/wp/v2/media", {"search": title, "per_page": 50})
    if isinstance(r, list):
        for m in r:
            mt = m.get("title", {}).get("rendered", "")
            if mt == title:
                return m
    # 2) search by filename slug (stem 部分)
    stem = Path(filename).stem
    # WP slug は lower + hyphen
    slug_guess = re.sub(r"[^a-zA-Z0-9]+", "-", stem).lower().strip("-")
    r2 = rest.get("/wp/v2/media", {"slug": slug_guess, "per_page": 5})
    if isinstance(r2, list) and r2:
        return r2[0]
    return None


def _upload_media(rest: WPRest, file_path: Path, title: str) -> dict:
    """REST POST /wp/v2/media に multipart-like で upload"""
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "image/jpeg"
    data = file_path.read_bytes()
    headers = {
        "X-WP-Nonce": rest._nonce or rest.fetch_nonce(),
        "Content-Type": mime,
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
    }
    url = rest.base + "/wp/v2/media"
    r = rest.req.post(url, headers=headers, data=data)
    if not r.ok:
        _log(f"  ✗ upload failed {file_path.name}: {r.status} {r.text()[:300]}")
        return {"_error": True, "status": r.status, "text": r.text()[:300]}
    j = rest._safe_json(r, f"upload {file_path.name}")
    if isinstance(j, dict) and j.get("id"):
        # タイトルを設定
        rest.patch(f"/wp/v2/media/{j['id']}", {"title": title, "alt_text": title})
        _log(f"  ✓ uploaded id={j['id']} ({file_path.name})")
    return j


def deploy_media():
    if not MATERIALS_DIR.exists():
        raise FileNotFoundError(f"materials dir not found: {MATERIALS_DIR}")

    media_map: dict[str, dict] = {}
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== v3 メディアアップロード 開始 =====")

        for key, (rel, title) in ASSETS.items():
            fp = MATERIALS_DIR / rel
            if not fp.exists():
                _log(f"  ⚠ ファイルなし key={key}: {fp}")
                continue
            existing = _find_existing_media(rest, title, fp.name)
            if existing and existing.get("id"):
                mid = existing["id"]
                src = existing.get("source_url") or ""
                _log(f"  ↻ key={key} 既存メディア再利用 id={mid}")
                media_map[key] = {"id": mid, "url": src, "title": title, "reused": True}
                time.sleep(0.15)
                continue
            r = _upload_media(rest, fp, title)
            if isinstance(r, dict) and r.get("id"):
                media_map[key] = {
                    "id": r["id"],
                    "url": r.get("source_url") or r.get("guid", {}).get("rendered", ""),
                    "title": title,
                    "reused": False,
                }
            else:
                _log(f"  ✗ key={key} upload 失敗: {r}")
            time.sleep(0.5)

        out = BASE / "state" / "media_v3.json"
        out.write_text(json.dumps(media_map, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"===== v3 メディア完了 ({len(media_map)}/{len(ASSETS)}) -> {out} =====")
        for k, v in media_map.items():
            _log(f"  {k} -> id={v['id']}  url={v['url'][:80]}")
    return media_map


if __name__ == "__main__":
    deploy_media()
