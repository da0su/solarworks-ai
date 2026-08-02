#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高料率クロールが 0件になった原因調査 — アフィリ検索ページの DOM を実地確認。

背景 (2026-08-02): fetch_high_rate_v2 が items=[] errors=[] を返し、
high_rate_v2.json が空になった。例外なしで0件 = EXTRACT_JS の
`.raf-product__item` が1つも取れていない = セレクタ変更 or 未ログイン の疑い。

出力: X:\_affiliate_dom_probe.json
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
from runner.browser_manager_v6 import BrowserManagerV6

OUT = Path(r"X:\_affiliate_dom_probe.json")
URL = ("https://affiliate.rakuten.co.jp/search?s=7&v=2&sitem=&g=100533"
       "&pmin=3000&pmax=&rmin=20&rmax=&wr=&p=1")

DIAG_JS = r"""() => {
  const cnt = (sel) => document.querySelectorAll(sel).length;
  // 商品カードらしき要素を広く探す
  const classes = {};
  document.querySelectorAll('div,li,article,section').forEach(el => {
    const c = el.className;
    if (typeof c === 'string' && /product|item|card|result/i.test(c)) {
      c.split(/\s+/).forEach(x => { if (x) classes[x] = (classes[x]||0)+1; });
    }
  });
  const top = Object.entries(classes).sort((a,b)=>b[1]-a[1]).slice(0,20);
  return {
    url: location.href,
    title: document.title,
    bodyLen: document.body.innerText.length,
    bodyHead: document.body.innerText.slice(0, 400),
    bodyTail: document.body.innerText.slice(-600),
    noResult: /該当|見つかり|ありません|0件/.test(document.body.innerText),
    raf_product_item: cnt('.raf-product__item'),
    raf_any: cnt('[class*=raf-]'),
    item_links: cnt('a[href*="item.rakuten.co.jp"]'),
    ryaku_text: (document.body.innerText.match(/料率/g)||[]).length,
    login_hint: /ログイン|サインイン|login/i.test(document.body.innerText),
    top_classes: top,
  };
}"""


def main():
    out = {"ts": datetime.now().isoformat()}
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        bm.page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(4)
        out["home"] = {"url": bm.page.url, "title": bm.page.title()}
        print("home:", out["home"], flush=True)

        bm.page.goto(URL, wait_until="domcontentloaded", timeout=40000)
        time.sleep(6)
        out["search_6s"] = bm.page.evaluate(DIAG_JS)
        print("6s:", out["search_6s"]["raf_product_item"], out["search_6s"]["item_links"], flush=True)
        # 遅延ロード対策: スクロール + 追加待機
        for _ in range(3):
            bm.page.evaluate("window.scrollBy(0, 2000)")
            time.sleep(4)
        out["search"] = bm.page.evaluate(DIAG_JS)
        print(json.dumps(out["search"], ensure_ascii=False, indent=1)[:1500], flush=True)

        try:
            d = Path(r"X:\screenshots") / datetime.now().strftime("%Y-%m-%d")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"affiliate_probe_{datetime.now().strftime('%H%M%S')}.png"
            bm.page.screenshot(path=str(p))
            out["shot"] = str(p)
        except Exception:
            pass
    except Exception as e:
        out["fatal"] = f"{type(e).__name__}: {e}"[:200]
        print("FATAL", out["fatal"], flush=True)
    finally:
        try: bm.stop()
        except Exception: pass
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
