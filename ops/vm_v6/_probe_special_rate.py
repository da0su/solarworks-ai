#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高料率(20%)商品の正しい導線を探す。

判明済み (2026-08-02):
  - キーワード検索は動くが表示される料率は 4.0% (標準料率) のみ
  - rmin= を付けた瞬間ページが 0件になる (このパラメータが死んでいる)
  → 20% 商品は別導線にあるはず。フッターの「特別料率一覧」を確認する。

出力: X:\_special_rate_probe.json
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

OUT = Path(r"X:\_special_rate_probe.json")

# ページ内の「特別料率」系リンクを収集する
LINKS_JS = r"""() => {
    const out = [];
    document.querySelectorAll('a[href]').forEach(a => {
        const t = (a.innerText||'').trim();
        if (/特別料率|プレミアム|提供商品|料率アップ|高料率/.test(t)) {
            out.push({text: t.slice(0,40), href: a.href});
        }
    });
    return out;
}"""

SNIFF_JS = r"""() => {
    const rates = [];
    document.querySelectorAll('.raf-product__item').forEach(card => {
        const m = (card.innerText||'').match(/料率\s*([0-9.]+)\s*[%％]/);
        if (m) rates.push(parseFloat(m[1]));
    });
    const all = (document.body.innerText.match(/([0-9.]+)\s*[%％]/g)||[]).slice(0,20);
    return {
        url: location.href,
        title: document.title,
        cards: document.querySelectorAll('.raf-product__item').length,
        item_links: document.querySelectorAll('a[href*="item.rakuten.co.jp"]').length,
        rates: rates.slice(0,20),
        max_rate: rates.length ? Math.max(...rates) : null,
        pct_tokens: all,
        bodyHead: document.body.innerText.slice(0,300),
    };
}"""


def main():
    out = {"ts": datetime.now().isoformat()}
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        page = bm.page
        page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(4)
        out["links_on_home"] = page.evaluate(LINKS_JS)
        print("links:", json.dumps(out["links_on_home"], ensure_ascii=False)[:700], flush=True)

        # 見つかったリンクを順に開いて中身を見る
        out["pages"] = {}
        for lk in (out["links_on_home"] or [])[:4]:
            href = lk.get("href")
            if not href:
                continue
            try:
                page.goto(href, wait_until="domcontentloaded", timeout=40000)
                time.sleep(5)
                info = page.evaluate(SNIFF_JS)
                out["pages"][lk["text"]] = info
                print(f"--- {lk['text']} ---", json.dumps(info, ensure_ascii=False)[:500], flush=True)
            except Exception as e:
                out["pages"][lk["text"]] = {"err": f"{type(e).__name__}: {e}"[:120]}
            time.sleep(1.5)
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
