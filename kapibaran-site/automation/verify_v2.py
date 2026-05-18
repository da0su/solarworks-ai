# -*- coding: utf-8 -*-
"""KAPIBARAN v2 反映確認

公開後のサイト各ページを HTTP GET して、
- 5SKU の存在 (KB-FC01 / KB-TM01 / ¥33,800 / ¥49,800)
- Coming Soon の表示
- 旧 v1 価格 (¥48,000 / ¥128,000 / ¥35,000) が「商品ページ」から消えていること
を検証する。
"""
from __future__ import annotations
import sys
import re
import time
import json
from pathlib import Path
from urllib.request import urlopen, Request

BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)


CHECKS = [
    {
        "url": "https://www.kapibaran.com/",
        "label": "TOP",
        "must_have": ["KB-FC01", "KB-TM01", "¥33,800", "¥49,800",
                      "Coming Soon", "フットケア家電", "スマートトレッドミル"],
        "must_not": ["¥48,000", "¥128,000"],
    },
    {
        "url": "https://www.kapibaran.com/products/",
        "label": "Products list",
        "must_have": ["KB-FC01", "KB-TM01", "¥33,800", "¥49,800",
                      "Coming Soon", "ボディケア", "ボディシェイピング"],
        "must_not": ["¥48,000", "¥128,000", "¥35,000"],
    },
    {
        "url": "https://www.kapibaran.com/products/footcare-kb-fc01/",
        "label": "Footcare detail",
        "must_have": ["KB-FC01", "¥33,800", "ネイビー", "ベージュ",
                      "Amazon", "楽天", "Yahoo"],
        "must_not": ["¥48,000"],
    },
    {
        "url": "https://www.kapibaran.com/products/treadmill-kb-tm01/",
        "label": "Treadmill detail",
        "must_have": ["KB-TM01", "¥49,800", "オレンジ", "ホワイト", "ブルー",
                      "Amazon", "楽天", "Yahoo"],
        "must_not": ["¥128,000"],
    },
    {
        "url": "https://www.kapibaran.com/about/",
        "label": "About",
        "must_have": ["KAPIBARAN", "SOLARWORKS"],
        "must_not": [],
    },
    {
        "url": "https://www.kapibaran.com/contact/",
        "label": "Contact",
        "must_have": ["support@kapibaran.com", "FAQ"],
        "must_not": [],
    },
    {
        "url": "https://www.kapibaran.com/tokushoho/",
        "label": "Tokushoho",
        "must_have": ["SOLARWORKS", "特定商取引法"],
        "must_not": [],
    },
]


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 KAPIBARAN-Verify"})
    with urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    return body


def run():
    results = []
    print("=" * 64)
    print("KAPIBARAN v2 反映確認")
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
        forbidden = [s for s in c["must_not"] if s in html]
        ok = (not missing) and (not forbidden)
        print(f"\n[{label}] {url}")
        print(f"  status: {'OK' if ok else 'FAIL'}")
        if missing:
            print(f"  ❌ missing: {missing}")
        if forbidden:
            print(f"  ❌ forbidden present: {forbidden}")
        if ok:
            print(f"  ✅ all {len(c['must_have'])} required strings present, "
                  f"all {len(c['must_not'])} forbidden absent")
        results.append({
            "label": label, "url": url, "ok": ok,
            "missing": missing, "forbidden": forbidden,
        })

    out = LOG_DIR / "verify_v2_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 64)
    pass_n = sum(1 for r in results if r.get("ok"))
    print(f"PASS {pass_n}/{len(results)}")
    print(f"結果: {out}")
    return all(r.get("ok") for r in results)


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
