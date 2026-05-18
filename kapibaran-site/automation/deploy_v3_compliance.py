# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — 法令違反表現の REST 全文置換 (safety net)

deploy_v3_pages.py が新規 content で全 page を上書きするが、
他にも (旧 v1 ページ・カテゴリ description・サイト基本情報・ジャーナル本文等)
古い表現が残っている可能性があるため、全 page/post を走査して置換する。

ConoHa WAF が DELETE を block するので、ここでは PATCH のみで対応。
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
        "items": [],
    }
    with wp_browser(headless=True) as (ctx, page):
        rest = WPRest(ctx, page)
        rest.fetch_nonce()
        _log("===== v3 法令違反表現 全文置換 開始 =====")

        for kind, label in (("pages", "page"), ("posts", "post")):
            items = _walk_collection(rest, kind)
            _log(f"  {kind}: {len(items)} 件")
            for it in items:
                pid = it["id"]
                slug = it.get("slug", "")
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

                new_title, n_t1 = _apply_repl(title_raw or "", REPLACEMENTS)
                new_title, n_t2 = _apply_repl(new_title, EMAIL_REPLACEMENTS)
                new_content, n_c1 = _apply_repl(content_raw or "", REPLACEMENTS)
                new_content, n_c2 = _apply_repl(new_content, EMAIL_REPLACEMENTS)

                phrase_total = n_t1 + n_c1
                email_total = n_t2 + n_c2
                if phrase_total == 0 and email_total == 0:
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
                if kind == "pages":
                    summary["pages_updated"] += 1
                else:
                    summary["posts_updated"] += 1
                summary["items"].append({
                    "kind": kind, "id": pid, "slug": slug, "title": title_raw[:60] if title_raw else "",
                    "phrase_replacements": phrase_total, "email_replacements": email_total,
                    "ok": ok,
                })
                _log(f"  ↻ {label} [{pid}] {slug} : 置換 phrase={phrase_total} email={email_total} ok={ok}")
                time.sleep(0.25)

    out_file = BASE / "logs" / "compliance_v3_result.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"===== 完了 ===== \n  pages updated: {summary['pages_updated']}/{summary['pages_checked']}\n"
         f"  posts updated: {summary['posts_updated']}/{summary['posts_checked']}\n"
         f"  phrase replacements total: {summary['phrase_replacements']}\n"
         f"  email replacements total: {summary['email_replacements']}\n"
         f"  log -> {out_file}")
    return summary


if __name__ == "__main__":
    run()
