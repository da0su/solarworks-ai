# -*- coding: utf-8 -*-
"""KAPIBARAN v3.1 — 法令遵守 + 画像投入 + 陽性 assertion 検証

Codex review (2026-05-18) #3 #6 #7 #8 反映:
- URL リストを §2 固定リストでハードコード (15 URL) — 件数厳密一致
- HTTP 200 + canonical + noindex なし + final URL = original URL を陽性 assert
- 商品ページ: 機器分類 / MSRP label / support-note / 注記 の存在 assert
- 全 URL の HTTP code / 禁止表現 / chunk assertion / 画像数根拠 / sha256 を log

返り値: 1 件でも fail なら overall FAIL.
"""
from __future__ import annotations
import sys
import re
import io
import os as _os
import json
import time
import hashlib
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Windows cp932 環境でも UTF-8 出力 (capture mode 互換)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)


# Codex #3: §2 完成条件 URL を hardcode (15 件 = TOP/about/products/footcare/treadmill
# /contact/tokushoho/terms/privacy/journal-cat/journal個別5)
SITE_ORIGIN = "https://www.kapibaran.com"
JOURNAL_SLUGS = [
    "foot-self-care-five-min",
    "home-fitness-routine",
    "premium-daily-life",
    "material-selection",
    "adult-fitness-habits",
]


# 禁止表現 — どこに出ても NG (Codex #6: 14 種固定)
GLOBAL_FORBIDDEN = [
    "送料無料",
    "全国 送料無料",
    "全国送料無料",
    "★★★★★",
    "CUSTOMER VOICE",
    "お客様の声",
    "血流を促",
    "血行を促",
    "1年メーカー保証",
    "1 年メーカー保証",
    "国内サポート対応",
    "エアバッグ式マッサージ",
    "むくみ解消",
    "脂肪燃焼",
]


# Codex #1: kbv2-pd__benefits markup が DOM に残っていたら隠蔽違反
HIDDEN_MARKUP_PATTERNS = [
    r'<ul[^>]*class="[^"]*kbv2-pd__benefits[^"]*"',
    r'<section[^>]*class="[^"]*kap-reviews[^"]*"',
]

# Codex #2 (5 回目): href="#" は全ページで FAIL 対象 (CTA 外の dummy リンクも禁止).
# SWELL/WP デフォルトの安全 anchor (#top/#content/#respond/#page/#wp-toolbar) を除外.
DUMMY_HASH_HREF_PATTERN = (
    r'<a[^>]+href="#(?!top\b|content\b|respond\b|page\b|wp-|skip-|main\b|menu-|nav-)"'
)
# 全ページ共通の上限 threshold (0 = ゼロ許容, それ以外なら WARN/FAIL 切り分け)
DUMMY_HASH_HREF_MAX_FAIL = int(_os.environ.get("KAPIBARAN_V3_HASH_HREF_MAX", "0"))


def _check(url: str, label: str, must_have: list, min_img: int,
           must_assert: dict | None = None,
           must_not_match: dict | None = None,
           warn_match: dict | None = None,
           must_count: dict | None = None) -> dict:
    """1 URL の検証.
    must_assert: {label: pattern} - 必ず存在 (合致 1 件以上で OK)
    must_not_match: {label: pattern} - 検出されたら FAIL
    warn_match: {label: pattern} - 検出されたら WARNING (FAIL ではない)
    must_count: {label: (pattern, expected_count)} - 出現回数が一致しなければ FAIL
    """
    return {
        "url": url,
        "label": label,
        "must_have": must_have,
        "min_img_count": min_img,
        "must_assert": must_assert or {},
        "must_not_match": must_not_match or {},
        "warn_match": warn_match or {},
        "must_count": must_count or {},
    }


CHECKS = [
    _check(
        f"{SITE_ORIGIN}/",
        "TOP",
        must_have=[
            "KB-FC01", "KB-TM01",
            "メーカー希望小売価格",
            "info@kapibaran.com",
            "kapibaran.com",
        ],
        min_img=5,
        must_assert={
            "canonical_present": r'<link[^>]*rel="canonical"',
        },
    ),
    _check(
        f"{SITE_ORIGIN}/about/",
        "About",
        must_have=["KAPIBARAN", "SOLARWORKS", "info@kapibaran.com"],
        min_img=1,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
    _check(
        f"{SITE_ORIGIN}/products/",
        "Products list",
        must_have=[
            "KB-FC01", "KB-TM01",
            "メーカー希望小売価格", "各販売店", "Coming Soon",
            "ボディケア", "ボディシェイピング",
        ],
        min_img=2,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
    _check(
        f"{SITE_ORIGIN}/products/footcare-kb-fc01/",
        "Footcare detail (KB-FC01)",
        must_have=[
            "KB-FC01", "¥33,800", "メーカー希望小売価格",
            "リラクゼーション機器", "医療機器ではありません",
            "ネイビー", "ベージュ",
            "info@kapibaran.com",
            "各販売店",
        ],
        min_img=1,
        must_assert={
            "classification_badge": "kbv3-pd__classification",
            "msrp_note": r"※実際の販売価格",
            "support_note": "kbv3-pd__support-note",
            "canonical_present": r'<link[^>]*rel="canonical"',
            "ec_disabled_marker": r'data-todo="ec-url-pending"',
            "ec_disabled_atomic_attrs": (
                r'<span[^>]*class="[^"]*kbv3-cta--disabled[^"]*"[^>]*'
                r'aria-disabled="true"[^>]*tabindex="-1"[^>]*'
                r'data-todo="ec-url-pending"'
            ),
        },
        must_not_match={
            "ec_btn_href_hash": r'<a[^>]+class="[^"]*kbv2-ec-btn[^"]*"[^>]*href="#"',
        },
        # Codex #2 (4 回目): CTA disabled span の出現数 = 3 (Amazon/楽天/Yahoo) を厳密確認
        must_count={
            "disabled_cta_count": (
                r'<span[^>]*class="[^"]*kbv3-cta--disabled[^"]*"[^>]*'
                r'aria-disabled="true"[^>]*tabindex="-1"',
                3,
            ),
            # 商品 CTA 内 <a href="#"> 以外も 0 件であることを確認
            "ec_btn_anchor_count": (
                r'<a[^>]+class="[^"]*kbv2-ec-btn[^"]*"',
                0,
            ),
        },
        # Codex #2 (4 回目): WARN-only — page 全体の href="#" 残量 (アンカー UI 等で許容)
        warn_match={
            "any_anchor_href_hash_excl_safe": (
                r'<a[^>]+href="#(?!top\b|content\b|respond\b|page\b)"'
            ),
        },
    ),
    _check(
        f"{SITE_ORIGIN}/products/treadmill-kb-tm01/",
        "Treadmill detail (KB-TM01)",
        must_have=[
            "KB-TM01", "¥49,800", "メーカー希望小売価格",
            "ホームフィットネス機器", "医療機器ではありません",
            "オレンジ", "ホワイト", "ブルー",
            "info@kapibaran.com",
            "各販売店",
        ],
        min_img=1,
        must_assert={
            "classification_badge": "kbv3-pd__classification",
            "msrp_note": r"※実際の販売価格",
            "support_note": "kbv3-pd__support-note",
            "canonical_present": r'<link[^>]*rel="canonical"',
            "ec_disabled_marker": r'data-todo="ec-url-pending"',
            "ec_disabled_atomic_attrs": (
                r'<span[^>]*class="[^"]*kbv3-cta--disabled[^"]*"[^>]*'
                r'aria-disabled="true"[^>]*tabindex="-1"[^>]*'
                r'data-todo="ec-url-pending"'
            ),
        },
        must_not_match={
            "ec_btn_href_hash": r'<a[^>]+class="[^"]*kbv2-ec-btn[^"]*"[^>]*href="#"',
        },
        must_count={
            "disabled_cta_count": (
                r'<span[^>]*class="[^"]*kbv3-cta--disabled[^"]*"[^>]*'
                r'aria-disabled="true"[^>]*tabindex="-1"',
                3,
            ),
            "ec_btn_anchor_count": (
                r'<a[^>]+class="[^"]*kbv2-ec-btn[^"]*"',
                0,
            ),
        },
        warn_match={
            "any_anchor_href_hash_excl_safe": (
                r'<a[^>]+href="#(?!top\b|content\b|respond\b|page\b)"'
            ),
        },
    ),
    _check(
        f"{SITE_ORIGIN}/contact/",
        "Contact",
        must_have=["info@kapibaran.com", "FAQ", "各販売店"],
        min_img=0,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
    _check(
        f"{SITE_ORIGIN}/tokushoho/",
        "Tokushoho",
        must_have=["SOLARWORKS", "特定商取引法", "info@kapibaran.com"],
        min_img=0,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
    _check(
        f"{SITE_ORIGIN}/terms/",
        "Terms",
        must_have=["利用規約", "SOLARWORKS"],
        min_img=0,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
    # Codex #8: privacy を slug-based 正規 URL に
    _check(
        f"{SITE_ORIGIN}/privacy/",
        "Privacy (slug-based)",
        must_have=["プライバシーポリシー", "個人情報", "info@kapibaran.com"],
        min_img=0,
        must_assert={
            "canonical_present": r'<link[^>]*rel="canonical"',
            "canonical_slug_based": r'<link[^>]*rel="canonical"[^>]*href="[^"]*/privacy/',
        },
    ),
    _check(
        f"{SITE_ORIGIN}/category/journal/",
        "Journal category",
        must_have=["KAPIBARAN"],
        min_img=0,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ),
]

# Journal 5 記事 個別検証
for slug in JOURNAL_SLUGS:
    CHECKS.append(_check(
        f"{SITE_ORIGIN}/{slug}/",
        f"Journal post ({slug})",
        must_have=["KAPIBARAN"],
        min_img=1,
        must_assert={"canonical_present": r'<link[^>]*rel="canonical"'},
    ))

# 既定: 15 URL. 運用変更時のみ KAPIBARAN_V3_EXPECTED_URL_COUNT で override 可能 (Codex #5 2 回目)
# Codex #5 (3 回目): 安全帯 13-20 を強制. 範囲外なら強制 FAIL + WARNING log
_DEFAULT_EXPECTED = 10 + len(JOURNAL_SLUGS)  # 15
EXPECTED_URL_COUNT = int(
    _os.environ.get("KAPIBARAN_V3_EXPECTED_URL_COUNT", _DEFAULT_EXPECTED)
)
EXPECTED_URL_COUNT_DEFAULT = _DEFAULT_EXPECTED
EXPECTED_URL_COUNT_SAFE_RANGE = (13, 20)
EXPECTED_URL_COUNT_OVERRIDE = (EXPECTED_URL_COUNT != _DEFAULT_EXPECTED)


def fetch(url: str) -> tuple[int, str, str, dict]:
    """(status_code, body_html, final_url, headers) を返す. 失敗時は status<0."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 KAPIBARAN-v3.1-Verify"})
    try:
        with urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, body, r.geturl(), dict(r.headers)
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body, url, dict(e.headers or {})
    except URLError as e:
        return -1, "", url, {"_error": str(e)}


def count_images(html: str) -> dict:
    """画像数 (img + bg-url) と内訳を返す."""
    img_tags = re.findall(r"<img\b[^>]*>", html, flags=re.I)
    bg_urls = re.findall(
        r"background-image\s*:\s*url\(['\"]?https?://[^)]*kapibaran\.com[^)]*\)",
        html, flags=re.I,
    )
    return {
        "img_tag_count": len(img_tags),
        "bg_url_count": len(bg_urls),
        "total": len(img_tags) + len(bg_urls),
        "img_src_sample": [
            (re.search(r'src="([^"]*)"', t).group(1) if re.search(r'src="([^"]*)"', t) else "")
            for t in img_tags[:3]
        ],
    }


def extract_canonical(html: str) -> str:
    m = re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', html, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r'<link[^>]*href="([^"]+)"[^>]*rel="canonical"', html, flags=re.I)
    if m:
        return m.group(1)
    return ""


def extract_noindex(html: str) -> bool:
    return bool(re.search(
        r'<meta[^>]*name="robots"[^>]*content="[^"]*noindex',
        html, flags=re.I,
    ))


def run():
    results = []
    print("=" * 72)
    print("KAPIBARAN v3.1 検証 (Codex #3 #6 #7 #8 強化版)")
    print(f"検証 URL 件数: {len(CHECKS)} (期待: {EXPECTED_URL_COUNT})")
    print("=" * 72)

    # Codex #5 (3 回目): 安全帯 (13-20) 外なら強制 FAIL
    lo, hi = EXPECTED_URL_COUNT_SAFE_RANGE
    if not (lo <= EXPECTED_URL_COUNT <= hi):
        msg = (f"EXPECTED_URL_COUNT={EXPECTED_URL_COUNT} 安全帯 [{lo}, {hi}] 外. "
               f"範囲外 override は禁止 (false success 防止 / Codex #5).")
        print(f"💥 FATAL: {msg}")
        out = LOG_DIR / "verify_v3_result.json"
        out.write_text(json.dumps({"overall": "FAIL", "error": msg}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return False

    if EXPECTED_URL_COUNT_OVERRIDE:
        print(f"⚠️  WARNING: EXPECTED_URL_COUNT override 中 (default={EXPECTED_URL_COUNT_DEFAULT}, "
              f"current={EXPECTED_URL_COUNT}). 運用変更時のみ使用すること.")

    # Codex #3: 件数厳密一致 assertion
    if len(CHECKS) != EXPECTED_URL_COUNT:
        msg = (f"URL リスト件数不整合: actual={len(CHECKS)} expected={EXPECTED_URL_COUNT}. "
               f"CLAUDE_CODE_INSTRUCTION_v3.md §2 固定リストと一致させてください.")
        print(f"💥 FATAL: {msg}")
        out = LOG_DIR / "verify_v3_result.json"
        out.write_text(json.dumps({"overall": "FAIL", "error": msg}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return False

    for c in CHECKS:
        url = c["url"]
        label = c["label"]
        status, html, final_url, headers = fetch(url)
        body_sha = hashlib.sha256(html.encode("utf-8")).hexdigest() if html else ""

        # 各種 assertion
        assertions: dict = {}
        # 1) HTTP 200
        assertions["http_200"] = (status == 200)
        # 2) final URL = original (redirect なし)
        assertions["no_redirect"] = (final_url.rstrip("/") == url.rstrip("/"))
        # 3) noindex なし
        is_noindex = extract_noindex(html)
        assertions["not_noindex"] = (not is_noindex)
        # 4) canonical 一致 (正規 URL と final が一致)
        canonical = extract_canonical(html)
        assertions["canonical_match"] = (
            canonical.rstrip("/") == url.rstrip("/") if canonical else False
        )

        # 5) must_have
        missing = [s for s in c["must_have"] if s not in html]
        assertions["must_have_all_present"] = (len(missing) == 0)

        # 6) forbidden (GLOBAL_FORBIDDEN 14 種)
        forbidden = [s for s in GLOBAL_FORBIDDEN if s in html]
        assertions["no_forbidden_phrases"] = (len(forbidden) == 0)

        # 7) hidden markup (kbv2-pd__benefits 等) が DOM に残っていない (Codex #3 5 回目: 明示 FAIL)
        hidden_markup_found = []
        hidden_markup_counts = {}
        for pat in HIDDEN_MARKUP_PATTERNS:
            try:
                cnt = len(re.findall(pat, html, flags=re.I))
            except re.error:
                cnt = 0
            if cnt > 0:
                hidden_markup_found.append(pat)
                hidden_markup_counts[pat] = cnt
        assertions["no_hidden_markup"] = (len(hidden_markup_found) == 0)

        # 8) image count
        img_info = count_images(html)
        assertions["image_count_ok"] = (img_info["total"] >= c["min_img_count"])

        # 9) must_assert (chunk regex — 必須存在)
        chunk_results: dict = {}
        for key, pattern in c["must_assert"].items():
            try:
                hit = bool(re.search(pattern, html))
            except re.error:
                hit = pattern in html
            chunk_results[key] = hit
            assertions[f"chunk_{key}"] = hit

        # 10) must_not_match (negative regex — 検出 = FAIL) — Codex #1 (2 回目)
        negative_results: dict = {}
        for key, pattern in c["must_not_match"].items():
            try:
                hit_count = len(re.findall(pattern, html))
            except re.error:
                hit_count = html.count(pattern)
            negative_results[key] = hit_count
            assertions[f"negative_{key}"] = (hit_count == 0)

        # 11) must_count (出現回数厳密一致 — Codex #2 4 回目)
        count_results: dict = {}
        for key, (pattern, expected) in c["must_count"].items():
            try:
                hit_count = len(re.findall(pattern, html))
            except re.error:
                hit_count = html.count(pattern)
            count_results[key] = {"actual": hit_count, "expected": expected}
            assertions[f"count_{key}"] = (hit_count == expected)

        # 12) warn_match (WARN-only — Codex #2 4 回目)
        warn_results: dict = {}
        for key, pattern in c["warn_match"].items():
            try:
                hit_count = len(re.findall(pattern, html))
            except re.error:
                hit_count = html.count(pattern)
            warn_results[key] = hit_count
            # assertions には入れない (FAIL にしない)

        # 13) 全ページ共通 dummy hash href threshold check (Codex #2 5 回目)
        dummy_hash_count = len(re.findall(DUMMY_HASH_HREF_PATTERN, html))
        warn_results["global_dummy_hash_href"] = dummy_hash_count
        # threshold 超過なら FAIL
        assertions["global_dummy_hash_href_under_threshold"] = (
            dummy_hash_count <= DUMMY_HASH_HREF_MAX_FAIL
        )

        ok = all(assertions.values())

        print(f"\n[{label}] {url}")
        print(f"  HTTP={status} final={final_url}  sha256={body_sha[:16]}…")
        print(f"  images: total={img_info['total']} "
              f"(img_tag={img_info['img_tag_count']} + bg={img_info['bg_url_count']}, min={c['min_img_count']})")
        if canonical:
            print(f"  canonical: {canonical}")
        if not assertions["must_have_all_present"]:
            print(f"  ❌ missing: {missing}")
        if not assertions["no_forbidden_phrases"]:
            print(f"  ❌ forbidden: {forbidden}")
        if not assertions["no_hidden_markup"]:
            print(f"  ❌ hidden markup: {hidden_markup_found}")
        if not assertions["http_200"]:
            print(f"  ❌ HTTP != 200 (got {status})")
        if not assertions["not_noindex"]:
            print(f"  ❌ noindex meta tag present")
        if not assertions["canonical_match"]:
            print(f"  ❌ canonical mismatch: got '{canonical}' want '{url}'")
        for key, hit in chunk_results.items():
            if not hit:
                print(f"  ❌ chunk_{key}: assertion failed (pattern not found)")
        for key, cnt in negative_results.items():
            if cnt > 0:
                print(f"  ❌ negative_{key}: 検出 {cnt} 件 (期待 0)")
        for key, info in count_results.items():
            if info["actual"] != info["expected"]:
                print(f"  ❌ count_{key}: actual={info['actual']} expected={info['expected']}")
        for key, cnt in warn_results.items():
            if cnt > 0:
                print(f"  ⚠️  warn_{key}: 検出 {cnt} 件 (WARN-only, FAIL ではない)")
        if ok:
            print(f"  ✅ ALL pass ({len(c['must_have'])} required, {len(c['must_assert'])} chunk, "
                  f"{len(c['must_not_match'])} negative, {len(c['must_count'])} count, "
                  f"{img_info['total']} images)")

        results.append({
            "label": label,
            "url": url,
            "ok": ok,
            "http_status": status,
            "final_url": final_url,
            "canonical": canonical,
            "noindex": is_noindex,
            "missing": missing,
            "forbidden_phrases_found": forbidden,
            "hidden_markup_found": hidden_markup_found,
            "image_count": img_info["total"],
            "image_breakdown": img_info,
            "min_img_count": c["min_img_count"],
            "chunk_assertions": chunk_results,
            "negative_assertions": negative_results,
            "count_assertions": count_results,
            "warn_assertions": warn_results,
            "all_assertions": assertions,
            "body_sha256": body_sha,
            "body_bytes": len(html.encode("utf-8")),
        })
        time.sleep(0.3)

    # Codex #4 (2 回目) + #1 (5 回目): 旧 ?page_id=3 直リンクは
    # (1) HTTP 404 / 410 = 完全除却  OR
    # (2) 301/302 で /privacy/ へ正規 redirect + canonical 一致
    # 上記以外は FAIL (HTTP 200 + 旧 slug 露出 = false success リスク)
    legacy_privacy_url = f"{SITE_ORIGIN}/?page_id=3"
    legacy_status, legacy_body, legacy_final, legacy_headers = fetch(legacy_privacy_url)
    legacy_canonical = extract_canonical(legacy_body) if legacy_body else ""
    legacy_body_has_old_marker = bool(legacy_body) and (
        "/privacy-policy/" in legacy_body
        or "<title>プライバシーポリシー" in legacy_body and "/privacy/" not in legacy_canonical
    )

    # OK 条件 (明示):
    is_404_or_gone = legacy_status in (404, 410)
    is_302_redirected_to_privacy = (
        legacy_status in (301, 302)
        and "/privacy/" in legacy_final
    )
    is_200_canonical_privacy = (
        legacy_status == 200
        and "/privacy/" in legacy_canonical
        and "/privacy-policy/" not in legacy_canonical
    )
    legacy_redirected = is_404_or_gone or is_302_redirected_to_privacy or is_200_canonical_privacy
    legacy_old_slug_exposed = (
        "/privacy-policy/" in legacy_final
        or "privacy-policy" in legacy_canonical
        or legacy_body_has_old_marker
    )
    legacy_ok = legacy_redirected and not legacy_old_slug_exposed

    print(f"\n[Legacy ?page_id=3 explicit check] {legacy_privacy_url}")
    print(f"  HTTP={legacy_status} final={legacy_final}")
    print(f"  canonical={legacy_canonical}")
    print(f"  branches: 404/410={is_404_or_gone} 302->priv={is_302_redirected_to_privacy} "
          f"200+canonical={is_200_canonical_privacy}")
    print(f"  {'✅' if legacy_ok else '❌'} legacy_ok={legacy_ok} "
          f"old_slug_exposed={legacy_old_slug_exposed}")

    legacy_check = {
        "label": "Legacy ?page_id=3 explicit check",
        "url": legacy_privacy_url,
        "ok": legacy_ok,
        "http_status": legacy_status,
        "final_url": legacy_final,
        "canonical": legacy_canonical,
        "branches": {
            "is_404_or_gone": is_404_or_gone,
            "is_302_redirected_to_privacy": is_302_redirected_to_privacy,
            "is_200_canonical_privacy": is_200_canonical_privacy,
        },
        "legacy_redirected": legacy_redirected,
        "old_slug_exposed": legacy_old_slug_exposed,
        "body_has_old_marker": legacy_body_has_old_marker,
    }

    pass_n = sum(1 for r in results if r.get("ok"))
    overall = "PASS" if (pass_n == len(results) and legacy_ok) else "FAIL"
    summary_out = {
        "overall": overall,
        "pass_count": pass_n,
        "total_count": len(results),
        "expected_count": EXPECTED_URL_COUNT,
        "expected_count_default": EXPECTED_URL_COUNT_DEFAULT,
        "expected_count_override_in_effect": EXPECTED_URL_COUNT_OVERRIDE,
        "expected_count_safe_range": list(EXPECTED_URL_COUNT_SAFE_RANGE),
        "count_match": (len(results) == EXPECTED_URL_COUNT),
        "global_forbidden": GLOBAL_FORBIDDEN,
        "legacy_privacy_check": legacy_check,
        "results": results,
    }
    out = LOG_DIR / "verify_v3_result.json"
    out.write_text(json.dumps(summary_out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"OVERALL: {overall}  ({pass_n}/{len(results)} PASS + legacy={'OK' if legacy_ok else 'NG'})")
    print(f"結果: {out}")
    print("=" * 72)
    return overall == "PASS"


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
