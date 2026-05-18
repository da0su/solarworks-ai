# -*- coding: utf-8 -*-
"""KAPIBARAN v3.1 — 法令違反表現の REST 全文置換 (safety net) + DOM markup strip

Codex review (2026-05-18 #2): 公開状態の page 全件 (25/26/27/28/74/75/124/125) に対し
明示的に走査し、ID/slug/status 一覧を log 出力する。draft 限定にならないことを保証。

Codex review (2026-05-18 #1): kbv2-pd__benefits markup を CSS で隠すのではなく
DOM markup ごと regex で物理削除する。

ConoHa WAF が DELETE を block するので、ここでは PATCH のみで対応。
"""
from __future__ import annotations
import sys
import re
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))

from wp_session import wp_browser, _log  # noqa
from wp_rest import WPRest  # noqa

import os as _os
# Codex #2: 公開 v2 ページ ID — これらが scan 対象に必ず含まれていることを保証
# 運用時のみ KAPIBARAN_V3_EXPECTED_PUBLISH_IDS=25,26,27,28 で override 可能 (Codex #5 2 回目)
_default_ids = "25,26,27,28,74,75,124,125"
EXPECTED_PUBLISH_PAGE_IDS = set(int(x) for x in _os.environ.get(
    "KAPIBARAN_V3_EXPECTED_PUBLISH_IDS", _default_ids
).split(",") if x.strip())
LEGACY_DRAFT_PAGE_IDS = set(int(x) for x in _os.environ.get(
    "KAPIBARAN_V3_LEGACY_DRAFT_IDS", "76,77,78"
).split(",") if x.strip())


# Codex #1: DOM markup ごと strip するパターン (CSS display:none 隠蔽の代わり)
# benefits リスト (全国 送料無料 / 1 年メーカー保証 / 国内サポート対応) を含む
# <ul class="kbv2-pd__benefits">…</ul> を完全削除。
MARKUP_STRIP_PATTERNS = [
    # <ul class="kbv2-pd__benefits">…</ul> (改行・属性順序・class 追加に頑健)
    (
        re.compile(
            r'<ul[^>]*class="[^"]*\bkbv2-pd__benefits\b[^"]*"[^>]*>'
            r'.*?</ul>',
            re.DOTALL | re.IGNORECASE,
        ),
        "kbv2-pd__benefits",
    ),
    # <section class="kap-reviews"> や類似のレビューセクション
    (
        re.compile(
            r'<section[^>]*class="[^"]*\b(?:kap-reviews|customer-voice|kbv2-reviews)\b[^"]*"[^>]*>'
            r'.*?</section>',
            re.DOTALL | re.IGNORECASE,
        ),
        "review-section",
    ),
]


def _strip_markup(text: str) -> tuple[str, int, list]:
    """禁止 markup を物理 strip. (new_text, removed_count, removed_labels) を返す."""
    if not isinstance(text, str) or not text:
        return text, 0, []
    out = text
    total = 0
    labels: list = []
    for pat, label in MARKUP_STRIP_PATTERNS:
        new_out, n = pat.subn("", out)
        if n > 0:
            total += n
            labels.append({"label": label, "count": n})
            out = new_out
    return out, total, labels


# (old, new) — 長い表現 → 短い表現の順に適用
REPLACEMENTS = [
    # 景表法 / 特商法
    ("全国 送料無料",     ""),
    ("全国送料無料",       ""),
    ("税込・送料無料",     "（税込）"),
    ("税込み・送料無料",   "（税込）"),
    ("税込 送料無料",       "（税込）"),
    ("送料無料",           ""),
    ("1 年メーカー保証",    ""),
    ("1年メーカー保証",     ""),
    ("メーカー1年保証",     ""),
    ("メーカー 1 年保証",    ""),
    ("1 年間のメーカー保証", "保証期間は各販売店規定に準じます"),
    ("1 年間",              "（販売店規定）"),
    ("国内サポート対応",     ""),
    # ステマ規制
    ("CUSTOMER VOICE",       ""),
    ("お客様の声",           ""),
    ("★★★★★",            ""),
    # 薬機法
    ("血流を促進",          "やさしく温める"),
    ("血流を促す",          "じんわり温めて、心地よく"),
    ("血行を促進",          "やさしく温める"),
    ("血行を促す",          "やさしく温める"),
    ("エアバッグ式マッサージ", "エアバッグ式の心地よい刺激"),
    ("マッサージ機",         "リラクゼーション機"),
    ("マッサージ",           "やさしく包み込む刺激"),
    ("疲労回復",             "リフレッシュ感"),
    ("むくみ解消",           "すっきり感"),
    ("むくみ改善",           "すっきり感"),
    ("治療",                 ""),
    ("治癒",                 ""),
    ("効果があります",        "心地よく感じられます"),
    ("効果がある",           "心地よく感じられます"),
    ("効きます",             "心地よく感じられます"),
    ("脂肪燃焼",             "暮らしのリズムに"),
    ("痩せる",               ""),
]


# 連絡先メールも一括統一
EMAIL_REPLACEMENTS = [
    ("support@kapibaran.com", "info@kapibaran.com"),
    ("contact@kapibaran.com", "info@kapibaran.com"),
    ("info@example.com",      "info@kapibaran.com"),
]


def _apply_repl(text: str, repls) -> tuple[str, int]:
    if not isinstance(text, str):
        return text, 0
    n = 0
    out = text
    for old, new in repls:
        if old and old in out:
            cnt = out.count(old)
            out = out.replace(old, new)
            n += cnt
    return out, n


def _walk_collection(rest: WPRest, kind: str):
    """kind ∈ {pages, posts}. status=any で全件取得"""
    per_page = 50
    page = 1
    out = []
    while True:
        r = rest.get(f"/wp/v2/{kind}", {"per_page": per_page, "page": page, "status": "any"})
        if not isinstance(r, list) or not r:
            break
        out.extend(r)
        if len(r) < per_page:
            break
        page += 1
        time.sleep(0.2)
    return out


def run():
    summary = {
        "pages_checked": 0,
        "pages_updated": 0,
        "posts_checked": 0,
        "posts_updated": 0,
        "phrase_replacements": 0,
        "email_replacements": 0,
        "markup_strips": 0,
        "scanned_publish_page_ids": [],
        "missing_expected_publish_pages": [],
        "applied_legacy_draft_page_ids": sorted(LEGACY_DRAFT_PAGE_IDS),
        "applied_expected_publish_page_ids": sorted(EXPECTED_PUBLISH_PAGE_IDS),
        "items": [],
    }
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== v3.1 法令違反表現 全文置換 + DOM strip 開始 =====")

        for kind, label in (("pages", "page"), ("posts", "post")):
            items = _walk_collection(rest, kind)
            _log(f"  {kind}: {len(items)} 件 (status=any)")

            # Codex #2: 公開 page ID リストを explicit 出力
            if kind == "pages":
                publish_ids = sorted({
                    it["id"] for it in items if it.get("status") == "publish"
                })
                summary["scanned_publish_page_ids"] = publish_ids
                _log(f"    → 公開 page IDs: {publish_ids}")
                missing = sorted(EXPECTED_PUBLISH_PAGE_IDS - set(publish_ids))
                if missing:
                    summary["missing_expected_publish_pages"] = missing
                    _log(f"    ⚠️ EXPECTED 公開 page が見つからない: {missing}")

            for it in items:
                pid = it["id"]
                slug = it.get("slug", "")
                status = it.get("status", "")
                title_raw = it.get("title", {}).get("raw")
                if title_raw is None:
                    title_raw = it.get("title", {}).get("rendered", "")
                content_raw = it.get("content", {}).get("raw")
                if content_raw is None:
                    # raw が公開 user に出ないので fetch with context=edit
                    detail = rest.get(f"/wp/v2/{kind}/{pid}", {"context": "edit"})
                    content_raw = (detail or {}).get("content", {}).get("raw", "")
                    title_raw = (detail or {}).get("title", {}).get("raw") or title_raw

                if kind == "pages":
                    summary["pages_checked"] += 1
                else:
                    summary["posts_checked"] += 1

                # 1) markup strip (Codex #1) — scope: draft 状態 or LEGACY_DRAFT_PAGE_IDS のみ
                #    Codex #3 (2 回目): 全 page/post 一律 strip は予想外セクション破壊 risk あり
                strip_eligible = (
                    kind == "pages" and (
                        status == "draft" or pid in LEGACY_DRAFT_PAGE_IDS
                    )
                )
                if strip_eligible:
                    stripped_content, strip_n, strip_labels = _strip_markup(content_raw or "")
                else:
                    stripped_content, strip_n, strip_labels = content_raw or "", 0, []
                # 2) phrase 置換
                new_title, n_t1 = _apply_repl(title_raw or "", REPLACEMENTS)
                new_title, n_t2 = _apply_repl(new_title, EMAIL_REPLACEMENTS)
                new_content, n_c1 = _apply_repl(stripped_content, REPLACEMENTS)
                new_content, n_c2 = _apply_repl(new_content, EMAIL_REPLACEMENTS)

                phrase_total = n_t1 + n_c1
                email_total = n_t2 + n_c2
                if phrase_total == 0 and email_total == 0 and strip_n == 0:
                    continue

                payload = {}
                if new_title != (title_raw or ""):
                    payload["title"] = new_title
                if new_content != (content_raw or ""):
                    payload["content"] = new_content
                if not payload:
                    continue
                r = rest.patch(f"/wp/v2/{kind}/{pid}", payload)
                ok = isinstance(r, dict) and r.get("id") == pid
                summary["phrase_replacements"] += phrase_total
                summary["email_replacements"] += email_total
                summary["markup_strips"] += strip_n
                if kind == "pages":
                    summary["pages_updated"] += 1
                else:
                    summary["posts_updated"] += 1
                summary["items"].append({
                    "kind": kind, "id": pid, "slug": slug, "status": status,
                    "title": title_raw[:60] if title_raw else "",
                    "phrase_replacements": phrase_total,
                    "email_replacements": email_total,
                    "markup_strips": strip_n,
                    "markup_strip_labels": strip_labels,
                    "ok": ok,
                })
                _log(f"  ↻ {label} [{pid}] ({status}) {slug} : "
                     f"phrase={phrase_total} email={email_total} markup_strip={strip_n} ok={ok}")
                time.sleep(0.25)

    # Codex #6 (2 回目): categories / tags の description にも禁止表現がないか scan
    # (PATCH 権限なくても、検出時 log で警告)
    try:
        with wp_browser(headless=True) as (ctx2, page2):
            rest2 = WPRest(ctx2, page2)
            rest2.fetch_nonce()
            tax_warnings = []
            for tax in ("categories", "tags"):
                items = rest2.get(f"/wp/v2/{tax}", {"per_page": 100}) or []
                if not isinstance(items, list):
                    continue
                for t in items:
                    desc = (t.get("description", "") or "")
                    name = t.get("name", "")
                    for forbidden in ("送料無料", "★★★★★", "お客様の声", "1年メーカー保証",
                                       "国内サポート対応", "血流を促", "マッサージ", "むくみ解消"):
                        if forbidden in desc or forbidden in name:
                            tax_warnings.append({
                                "tax": tax, "id": t.get("id"), "slug": t.get("slug"),
                                "found": forbidden,
                            })
            summary["taxonomy_warnings"] = tax_warnings
            if tax_warnings:
                _log(f"  ⚠️ taxonomy 禁止表現検出: {tax_warnings}")
            else:
                _log("  · taxonomy (categories/tags) 禁止表現 0 件")
    except Exception as e:
        summary["taxonomy_warnings"] = [{"error": str(e)}]

    out_file = BASE / "logs" / "compliance_v3_result.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"===== 完了 ===== \n  pages updated: {summary['pages_updated']}/{summary['pages_checked']}\n"
         f"  posts updated: {summary['posts_updated']}/{summary['posts_checked']}\n"
         f"  phrase replacements total: {summary['phrase_replacements']}\n"
         f"  markup strips total: {summary['markup_strips']}\n"
         f"  email replacements total: {summary['email_replacements']}\n"
         f"  公開 page IDs scanned: {summary['scanned_publish_page_ids']}\n"
         f"  log -> {out_file}")
    # Codex #2: EXPECTED 公開 page が欠落していたら ABORT
    if summary["missing_expected_publish_pages"]:
        raise RuntimeError(
            f"EXPECTED 公開 page が scan 結果に欠落: "
            f"{summary['missing_expected_publish_pages']} "
            f"(deploy_v3_pages.py を先に実行してください)"
        )
    return summary


if __name__ == "__main__":
    run()
