#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""料率アップ商品ページ (/recommend/uplist) の DOM 構造を採取する。

判明済み: 20%商品の導線は検索ではなく
  /recommend/uplist        料率アップ商品を探す
  /promo/special_rate      特別料率一覧
既存セレクタ .raf-product__item では 0件 = 別マークアップ。

出力: X:\_uplist_dom.json
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

OUT = Path(r"X:\_uplist_dom.json")

DUMP_JS = r"""() => {
    const out = {url: location.href, title: document.title,
                 bodyLen: document.body.innerText.length};
    const links = document.querySelectorAll('a[href*="item.rakuten.co.jp"], a[href*="freelink"]');
    out.product_links = links.length;
    // 料率表記の分布
    const rateTokens = (document.body.innerText.match(/料率[^0-9]{0,4}([0-9.]+)\s*[%％]/g)||[]);
    out.rate_tokens = rateTokens.slice(0, 15);
    const nums = rateTokens.map(t => parseFloat((t.match(/([0-9.]+)/)||[])[1])).filter(x=>!isNaN(x));
    out.rate_max = nums.length ? Math.max(...nums) : null;
    out.rate_ge20 = nums.filter(x => x >= 20).length;
    // カード候補のクラス名 (商品リンクの祖先)
    const cls = {};
    links.forEach(a => {
        let el = a;
        for (let i = 0; i < 6 && el; i++) {
            const c = el.className;
            if (typeof c === 'string') c.split(/\s+/).forEach(x => { if (x) cls[x] = (cls[x]||0)+1; });
            el = el.parentElement;
        }
    });
    out.card_classes = Object.entries(cls).sort((a,b)=>b[1]-a[1]).slice(0,15);
    // 1件目の周辺テキスト
    if (links.length) {
        let p = links[0];
        for (let i = 0; i < 4 && p.parentElement; i++) p = p.parentElement;
        out.sample_text = (p.innerText||'').slice(0, 350);
    }
    out.bodyHead = document.body.innerText.slice(0, 350);
    return out;
}"""

TARGETS = [
    ("uplist",       "https://affiliate.rakuten.co.jp/recommend/uplist"),
    ("special_rate", "https://affiliate.rakuten.co.jp/promo/special_rate"),
    ("promo",        "https://affiliate.rakuten.co.jp/promo"),
]


def main():
    out = {"ts": datetime.now().isoformat(), "pages": {}}
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        page = bm.page
        page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(3)
        for label, url in TARGETS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(6)
                for _ in range(2):                    # 遅延ロード対策
                    page.evaluate("window.scrollBy(0, 1500)")
                    time.sleep(3)
                info = page.evaluate(DUMP_JS)
                out["pages"][label] = info
                print(f"--- {label} ---", json.dumps(info, ensure_ascii=False)[:600], flush=True)
                try:
                    d = Path(r"X:\screenshots") / datetime.now().strftime("%Y-%m-%d")
                    d.mkdir(parents=True, exist_ok=True)
                    p = d / f"uplist_{label}_{datetime.now().strftime('%H%M%S')}.png"
                    page.screenshot(path=str(p))
                    info["shot"] = str(p)
                except Exception:
                    pass
            except Exception as e:
                out["pages"][label] = {"err": f"{type(e).__name__}: {e}"[:140]}
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
