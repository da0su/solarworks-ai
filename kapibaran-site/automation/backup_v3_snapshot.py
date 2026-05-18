# -*- coding: utf-8 -*-
"""KAPIBARAN v3.1 — REST 全件 snapshot バックアップ

Codex review (2026-05-18 #2) 必須要件:
- WAF で wp db export が block されるため、REST API で 全 page/post/customizer/option
  を JSON snapshot + SHA256 ハッシュで保存。
- バックアップ失敗時は ABORT (exit code 2)。
- 復元手順は kapibaran-site/RUNBOOK_v3.md を参照。

出力先:
  kapibaran-site/state/v3_backups/<YYYYMMDD_HHMMSS>/
    ├── pages.json
    ├── posts.json
    ├── media_index.json
    ├── customizer_css.txt
    ├── settings.json
    ├── manifest.json   (各 file の sha256 + 件数)
"""
from __future__ import annotations
import sys
import io
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime

# Windows cp932 環境でも UTF-8 出力 (capture mode subprocess でも壊れない安全な reconfigure)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE.parent
sys.path.insert(0, str(BASE / "automation"))

from wp_session import wp_browser, _log  # noqa
from wp_rest import WPRest  # noqa


# Codex #6 (3 回目): 一元保管. 既定は site/state. 互換のため legacy symlink/copy も維持。
# - PRIMARY: kapibaran-site/state/v3_backups/<ts>/ (これが正)
# - LEGACY (互換): repo_root/state/v3_backups/<ts>/ にも複製 (将来削除予定)
BACKUP_ROOT_PRIMARY = BASE / "state" / "v3_backups"
BACKUP_ROOT_LEGACY = REPO_ROOT / "state" / "v3_backups"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _walk_collection(rest: WPRest, kind: str, context: str = "edit") -> list:
    """kind ∈ {pages, posts, media}. status=any で全件取得 (context=edit で raw も含む)"""
    per_page = 50
    page = 1
    out = []
    while True:
        params = {"per_page": per_page, "page": page, "status": "any", "context": context}
        if kind == "media":
            params.pop("status", None)  # media は status クエリで弾かれることがあるので外す
        r = rest.get(f"/wp/v2/{kind}", params)
        if not isinstance(r, list) or not r:
            break
        out.extend(r)
        if len(r) < per_page:
            break
        page += 1
        time.sleep(0.2)
    return out


def _fetch_customizer_css(rest: WPRest) -> str:
    """Customizer の Custom CSS を REST 経由で取得 (custom_css post_type)."""
    try:
        r = rest.get("/wp/v2/custom_css", {"per_page": 5})
        if isinstance(r, list) and r:
            # post_content に raw CSS が入る
            return r[0].get("content", {}).get("rendered", "") or ""
    except Exception as e:
        _log(f"  custom_css 取得失敗: {e}")
    # Fallback: テーマ option 経由 (権限あれば)
    try:
        r2 = rest.get("/wp/v2/themes")
        return json.dumps(r2, ensure_ascii=False)
    except Exception:
        return ""


def run() -> dict:
    """全件 snapshot を取得し manifest を返す. 失敗時は例外を raise."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Codex #4 (4 回目): 一元保管. PRIMARY のみに実体を置き、LEGACY は pointer file のみ。
    primary = BACKUP_ROOT_PRIMARY / ts
    primary.mkdir(parents=True, exist_ok=True)
    out_dirs = [primary]
    _log(f"===== v3.1 backup snapshot 開始 PRIMARY={primary} (LEGACY は pointer のみ) =====")

    manifest: dict = {
        "timestamp": ts,
        "iso": datetime.now().isoformat(timespec="seconds"),
        "site": "https://www.kapibaran.com/",
        "files": {},
        "counts": {},
        "status": "running",
    }

    try:
        with wp_browser(headless=True) as (ctx, page):
            rest = WPRest(ctx, page)
            rest.fetch_nonce()

            # 1) pages
            _log("  - pages 取得中…")
            pages = _walk_collection(rest, "pages", context="edit")
            manifest["counts"]["pages"] = len(pages)
            if len(pages) == 0:
                raise RuntimeError("pages が 0 件で取得できません (REST 認証エラーの可能性)")

            # 2) posts
            _log("  - posts 取得中…")
            posts = _walk_collection(rest, "posts", context="edit")
            manifest["counts"]["posts"] = len(posts)

            # 3) media index (URL / id / 軽量メタのみ — バイナリは保存しない)
            _log("  - media index 取得中…")
            media = _walk_collection(rest, "media", context="view")
            media_idx = [{
                "id": m.get("id"),
                "slug": m.get("slug"),
                "source_url": m.get("source_url"),
                "title": m.get("title", {}).get("rendered", ""),
                "mime_type": m.get("mime_type"),
            } for m in media]
            manifest["counts"]["media"] = len(media_idx)

            # 4) settings
            _log("  - settings 取得中…")
            settings = rest.get("/wp/v2/settings") or {}

            # 5) customizer CSS
            _log("  - customizer CSS 取得中…")
            css = _fetch_customizer_css(rest)

            # 6) taxonomies (Codex #6 2 回目: categories + tags 完全取得)
            _log("  - categories 取得中…")
            categories = rest.get("/wp/v2/categories", {"per_page": 100}) or []
            if not isinstance(categories, list):
                categories = []
            manifest["counts"]["categories"] = len(categories)

            _log("  - tags 取得中…")
            tags = rest.get("/wp/v2/tags", {"per_page": 100}) or []
            if not isinstance(tags, list):
                tags = []
            manifest["counts"]["tags"] = len(tags)

            # 7) menu items (REST API 経由・利用可能なら)
            # Codex #7 (3 回目): unsupported 時は明示
            _log("  - nav menus 取得中…")
            menus_payload: dict = {"available": False, "items": [], "note": ""}
            try:
                r = rest.get("/wp/v2/menus", {"per_page": 20})
                if isinstance(r, list):
                    menus_payload = {"available": True, "items": r, "note": ""}
                    manifest["counts"]["menus"] = len(r)
                else:
                    menus_payload["note"] = f"unsupported (got {type(r).__name__}: {str(r)[:100]})"
                    manifest["counts"]["menus_unsupported"] = True
            except Exception as e:
                menus_payload["note"] = f"unsupported (error: {e})"
                manifest["counts"]["menus_unsupported"] = True
            menus = menus_payload  # 後段 _dump 用

        # ファイル書き出し + sha256
        def _dump(name: str, payload):
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") \
                if not isinstance(payload, str) else payload.encode("utf-8")
            digest = _sha256(data)
            for d in out_dirs:
                (d / name).write_bytes(data)
            manifest["files"][name] = {
                "sha256": digest,
                "bytes": len(data),
            }
            _log(f"    + {name} ({len(data):,} bytes, sha256={digest[:16]}…)")

        _dump("pages.json", pages)
        _dump("posts.json", posts)
        _dump("media_index.json", media_idx)
        _dump("settings.json", settings)
        _dump("customizer_css.txt", css)
        _dump("categories.json", categories)
        _dump("tags.json", tags)
        _dump("menus.json", menus)

        manifest["status"] = "ok"
        manifest["primary_path"] = str(primary)
        for d in out_dirs:
            (d / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # LEGACY pointer (互換): repo_root/state/v3_backups/<ts>/_pointer.json のみ
        try:
            legacy_pointer = BACKUP_ROOT_LEGACY / ts
            legacy_pointer.mkdir(parents=True, exist_ok=True)
            (legacy_pointer / "_pointer.json").write_text(
                json.dumps({
                    "legacy_pointer": True,
                    "primary_path": str(primary),
                    "primary_manifest": str(primary / "manifest.json"),
                    "ts": ts,
                    "note": "v3.1 から実体は PRIMARY のみ. このディレクトリは互換のための pointer 専用.",
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _log(f"  · LEGACY pointer 作成 skip: {e}")

        _log(f"===== backup 完了 ({manifest['counts']}) -> {primary}/manifest.json =====")
        return manifest
    except Exception as e:
        manifest["status"] = "failed"
        manifest["error"] = str(e)
        for d in out_dirs:
            try:
                (d / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
        _log(f"💥 backup 失敗: {e}")
        raise


if __name__ == "__main__":
    try:
        m = run()
        ok = m.get("status") == "ok" and m["counts"].get("pages", 0) > 0
        sys.exit(0 if ok else 2)
    except Exception as e:
        print(f"BACKUP ABORT: {e}", file=sys.stderr)
        sys.exit(2)
