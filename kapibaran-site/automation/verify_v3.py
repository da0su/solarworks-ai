# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — 法令遵守 + 画像投入 検証

各公開ページを HTTP GET し:
- 禁止表現 (送料無料 / 血流促進 / ★★★★★ / お客様の声 / CUSTOMER VOICE 等) が
  page body に残っていないか
- 各セクションに <img> または background-image: url('...kapibaran.com...') が
  含まれているか
- info@kapibaran.com が Contact / Footer / About に含まれるか
- メーカー希望小売価格 + 注記が商品ページに含まれるか
- 機器分類 (リラクゼーション機器 / ホームフィットネス機器) が商品ページに含まれるか
を検証する。
"""
from __future__ import annotations
import sys
import re
import io
import json
import time
from pathlib import Path
from urllib.request import urlopen, Request

# Windows cp932 環境でも UTF-8 出力
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)


# 禁止表現 — どこに出ても NG
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


CHECKS = [
    {
        "url": "https://www.kapibaran.com/",
        "label": "TOP",
        "must_have": [
            "KB-FC01", "KB-TM01",
            "メーカー希望小売価格",
            "info@kapibaran.com",
            "kapibaran.com",  # 何らかの WP メディア URL を含むはず
        ],
        "min_img_count": 5,   # hero + 4 cat + 2 product = 7 程度
    },
    {
        "url": "https://www.kapibaran.com/products/",
        "label": "Products list",
        "must_have": [
            "KB-FC01", "KB-TM01",
            "メーカー希望小売価格", "各販売店", "Coming Soon",
            "ボディケア", "ボディシェイピング",
        ],
        "min_img_count": 2,
    },
    {
        "url": "https://www.kapibaran.com/products/footcare-kb-fc01/",
        "label": "Footcare detail",
        "must_have": [
            "KB-FC01", "¥33,800", "メーカー希望小売価格",
            "リラクゼーション機器", "医療機器ではありません",
            "ネイビー", "ベージュ",
            "info@kapibaran.com",
            "各販売店",
        ],
        "min_img_count": 1,
    },
    {
        "url": "https://www.kapibaran.com/products/treadmill-kb-tm01/",
        "label": "Treadmill detail",
        "must_have": [
            "KB-TM01", "¥49,800", "メーカー希望小売価格",
            "ホームフィットネス機器", "医療機器ではありません",
            "オレンジ", "ホワイト", "ブルー",
            "info@kapibaran.com",
            "各販売店",
        ],
        "min_img_count": 1,
    },
    {
        "url": "https://www.kapibaran.com/about/",
        "label": "About",
        "must_have": ["KAPIBARAN", "SOLARWORKS", "info@kapibaran.com"],
        "min_img_count": 1,
    },
    {
        "url": "https://www.kapibaran.com/contact/",
        "label": "Contact",
        "must_have": ["info@kapibaran.com", "FAQ", "各販売店"],
        "min_img_count": 0,
    },
    {
        "url": "https://www.kapibaran.com/tokushoho/",
        "label": "Tokushoho",
        "must_have": ["SOLARWORKS", "特定商取引法", "info@kapibaran.com"],
        "min_img_count": 0,
    },
]


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 KAPIBARAN-v3-Verify"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def count_images(html: str) -> int:
    # <img>タグ + background-image: url(...kapibaran.com...)
    img_tags = len(re.findall(r"<img\b", html, flags=re.I))
    bg_urls = len(re.findall(r"background-image\s*:\s*url\(['\"]?https?://[^)]*kapibaran\.com[^)]*\)", html, flags=re.I))
    return img_tags + bg_urls


def run():
    results = []
    print("=" * 64)
    print("KAPIBARAN v3 反映確認")
    print("=" * 64)
    for c in CHECKS:
        url = c["url"]
        label = c["label"]
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  [{label}] FETCH ERROR: {e}")
            results.append({"label": label, "url": url, "ok": False, "error": str(e)})
            continue

        missing = [s for s in c["must_have"] if s not in html]
        # forbidden: GLOBAL_FORBIDDEN を全 URL に適用
        forbidden = [s for s in GLOBAL_FORBIDDEN if s in html]
        img_count = count_images(html)
        min_img = c.get("min_img_count", 0)
        img_ok = img_count >= min_img
        ok = (not missing) and (not forbidden) and img_ok

        print(f"\n[{label}] {url}")
        print(f"  status: {'OK' if ok else 'FAIL'}  images={img_count} (min={min_img})")
        if missing:
            print(f"  ❌ missing: {missing}")
        if forbidden:
            print(f"  ❌ forbidden present: {forbidden}")
        if not img_ok:
            print(f"  ❌ image count {img_count} < required {min_img}")
        if ok:
            print(f"  ✅ all {len(c['must_have'])} required, no forbidden, {img_count} images")

        results.append({
            "label": label, "url": url, "ok": ok,
            "missing": missing, "forbidden": forbidden,
            "image_count": img_count, "min_img_count": min_img,
        })
        time.sleep(0.3)

    out = LOG_DIR / "verify_v3_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 64)
    pass_n = sum(1 for r in results if r.get("ok"))
    print(f"PASS {pass_n}/{len(results)}")
    print(f"結果: {out}")
    return all(r.get("ok") for r in results)


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
