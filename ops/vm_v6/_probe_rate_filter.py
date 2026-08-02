#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""キーワード検索に料率フィルタ(rmin)が効くかを検証する。

v3 初回実行で全キーワード0件。原因は料率で全滅した疑い
(probe では 料率4.0% の商品が並んでいた)。
旧v2 URL には rmin=20 が付いていたので、キーワード検索でも効くか確かめる。

出力: X:\_rate_filter_probe.json
"""
from __future__ import annotations
import sys, io, json, time
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, "W:\\")
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from runner.browser_manager_v6 import BrowserManagerV6

OUT = Path(r"X:\_rate_filter_probe.json")

RATES_JS = r"""() => {
    const rates = [];
    document.querySelectorAll('.raf-product__item').forEach(card => {
        const m = (card.innerText||'').match(/料率\s*([0-9.]+)\s*[%％]/);
        if (m) rates.push(parseFloat(m[1]));
    });
    return {n: document.querySelectorAll('.raf-product__item').length, rates: rates};
}"""

KW = "ベビー"
VARIANTS = [
    ("keyword_only",      f"/search?sitem={quote(KW)}"),
    ("rmin20",            f"/search?sitem={quote(KW)}&rmin=20"),
    ("rmin20_full",       f"/search?sitem={quote(KW)}&s=1&v=2&pmin=3000&pmax=&rmin=20&rmax=&wr="),
    ("rmin20_p2",         f"/search?sitem={quote(KW)}&rmin=20&p=2"),
]


def main():
    out = {"ts": datetime.now().isoformat(), "results": {}}
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        bm.page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(3)
        for label, path in VARIANTS:
            try:
                bm.page.goto("https://affiliate.rakuten.co.jp" + path,
                             wait_until="domcontentloaded", timeout=40000)
                time.sleep(4)
                r = bm.page.evaluate(RATES_JS)
                rates = r.get("rates") or []
                out["results"][label] = {
                    "cards": r.get("n"),
                    "with_rate": len(rates),
                    "min": min(rates) if rates else None,
                    "max": max(rates) if rates else None,
                    "ge20": sum(1 for x in rates if x >= 20),
                    "sample": rates[:10],
                }
                print(label, json.dumps(out["results"][label], ensure_ascii=False), flush=True)
            except Exception as e:
                out["results"][label] = {"err": f"{type(e).__name__}: {e}"[:120]}
                print(label, "ERR", e, flush=True)
            time.sleep(1.5)
    except Exception as e:
        out["fatal"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        try: bm.stop()
        except Exception: pass
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
