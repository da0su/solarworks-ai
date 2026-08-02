#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""アフィリ検索の正しい導線を特定する (第2次調査)。

第1次で判明: 旧URL `/search?s=7&v=2&g=100533&pmin=..&rmin=20` は
ログイン済みでも商品が1件もレンダリングされない (raf-product__item=0)。

本調査: 実際にUIを操作して検索を成立させ、その時のURLとDOM構造を採取する。
  1. トップの検索窓にキーワードを入れて検索 → 結果DOMのクラス名を採取
  2. 成立したURLを記録 (以後これをテンプレートに使う)

出力: X:\_affiliate_search2.json
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

OUT = Path(r"X:\_affiliate_search2.json")

# 検索結果らしき要素を広く採取する
SNIFF_JS = r"""() => {
  const out = {url: location.href, bodyLen: document.body.innerText.length};
  out.item_links = document.querySelectorAll('a[href*="item.rakuten.co.jp"]').length;
  out.ryaku = (document.body.innerText.match(/料率/g)||[]).length;
  // 商品カードらしき親要素を推定: item.rakuten リンクの祖先で共通クラスを拾う
  const cls = {};
  document.querySelectorAll('a[href*="item.rakuten.co.jp"]').forEach(a => {
    let el = a;
    for (let i = 0; i < 6 && el; i++) {
      const c = el.className;
      if (typeof c === 'string') c.split(/\s+/).forEach(x => { if (x) cls[x] = (cls[x]||0)+1; });
      el = el.parentElement;
    }
  });
  out.ancestor_classes = Object.entries(cls).sort((a,b)=>b[1]-a[1]).slice(0,15);
  // 1件目のカードのテキスト例
  const first = document.querySelector('a[href*="item.rakuten.co.jp"]');
  if (first) {
    let p = first;
    for (let i = 0; i < 4 && p.parentElement; i++) p = p.parentElement;
    out.sample_text = (p.innerText||'').slice(0, 300);
    out.sample_html_head = (p.outerHTML||'').slice(0, 400);
  }
  return out;
}"""


def main():
    out = {"ts": datetime.now().isoformat(), "steps": []}
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        page = bm.page
        page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(4)

        # --- 手順1: トップの検索窓から検索を実行 ---
        typed = page.evaluate("""() => {
            const inputs = [...document.querySelectorAll('input[type=text],input[type=search],input:not([type])')];
            const box = inputs.find(i => (i.placeholder||'').includes('楽天市場') ||
                                          (i.placeholder||'').includes('商品'));
            if (!box) return {ok:false, placeholders: inputs.map(i=>i.placeholder||'').slice(0,8)};
            box.focus();
            box.value = 'ベビー';
            box.dispatchEvent(new Event('input', {bubbles:true}));
            return {ok:true, placeholder: box.placeholder};
        }""")
        out["steps"].append({"type_search": typed})
        print("type_search:", typed, flush=True)
        if typed.get("ok"):
            page.keyboard.press("Enter")
            time.sleep(7)
            out["after_enter"] = page.evaluate(SNIFF_JS)
            print("after_enter:", json.dumps(out["after_enter"], ensure_ascii=False)[:600], flush=True)

        # --- 手順2: 成立したURLがあればジャンル絞り込みを試す ---
        cur = page.url
        out["result_url"] = cur
        try:
            d = Path(r"X:\screenshots") / datetime.now().strftime("%Y-%m-%d")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"affiliate_search2_{datetime.now().strftime('%H%M%S')}.png"
            page.screenshot(path=str(p))
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
