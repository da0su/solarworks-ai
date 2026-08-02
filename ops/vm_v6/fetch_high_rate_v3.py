#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高料率プール取得 v3 — コンセプトのキーワードで仕入れる。

## v2 が壊れた理由 (2026-08-02 実測)
v2 は `/search?s=7&v=2&g=100533&pmin=3000&rmin=20` のように **ジャンル指定のみ**で
検索していたが、この形式では商品が1件もレンダリングされなくなった
(ログイン済み・raf-product__item=0)。セレクタは無事で、URL 形式の方が死んでいた。

実測: `/search?sitem=ベビー` のキーワード検索なら .raf-product__item=90 で取得できる。

## v3 の方針
ジャンルIDではなく**コンセプトのキーワード**で仕入れる。
正典: 09_INTELLIGENCE/room_growth/concept_and_longterm_plan.md
  誰に = 0-6歳を育てるママ / 何を = 子どもの口に入る・肌に触れる・ママをラクにする
これにより「何を仕入れるか」がコンセプトと直結し、
ジャンル指定より狙いが絞れる (v2 は52%がコンセプト外だった)。

料率・価格の絞り込みは URL パラメータに頼らずカード本文から抽出して
Python 側で行う (URL 仕様変更に強くする)。

出力: X:\high_rate_v2.json  (下流の互換のためファイル名は据え置き)
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

OUT = Path(r"X:\high_rate_v2.json")
BACKUP = Path(r"X:\high_rate_v3_prev.json")

# コンセプト直結のキーワード。(キーワード, 記録するジャンル)
# ジャンル名は下流 (concept_filter / collection_appender) と揃える。
KEYWORDS = [
    ("ベビー",           "キッズ・ベビー・マタニティ"),
    ("赤ちゃん",         "キッズ・ベビー・マタニティ"),
    ("離乳食",           "キッズ・ベビー・マタニティ"),
    ("おむつ",           "キッズ・ベビー・マタニティ"),
    ("抱っこ紐",         "キッズ・ベビー・マタニティ"),
    ("キッズ 子供",      "キッズ・ベビー・マタニティ"),
    ("マタニティ 授乳",  "キッズ・ベビー・マタニティ"),
    ("オーガニック 無添加", "食品"),
    ("時短 調理",        "キッチン用品・食器・調理器具"),
    ("洗剤 赤ちゃん",    "日用品雑貨・文房具・手芸"),
    ("タオル 綿100",     "日用品雑貨・文房具・手芸"),
    ("お菓子 詰め合わせ", "スイーツ・お菓子"),
]

MIN_RATE = 20.0    # 料率 20% 以上 (CEO 2026-06-14 指示)
MIN_PRICE = 3000   # ¥3,000 以上
MAX_PAGES = 8      # 1キーワードあたり (30件/ページ)

EXTRACT_JS = r"""() => {
    const items = [];
    document.querySelectorAll('.raf-product__item').forEach(card => {
        const txt = card.innerText || '';
        const rm = txt.match(/料率\s*([0-9.]+)\s*[%％]/);
        if (!rm) return;
        const pm = txt.match(/([0-9,]+)\s*円/);
        const nameEl = card.querySelector('[class*=raf-product__name]');
        const linkEl = card.querySelector('a[href*="item.rakuten.co.jp"]');
        const img = card.querySelector('img');
        items.push({
            rate: parseFloat(rm[1]),
            price: pm ? parseInt(pm[1].replace(/,/g, '')) : null,
            name: nameEl ? (nameEl.innerText||'').trim().slice(0,100) : '',
            url: linkEl ? linkEl.href.split('?')[0] : null,
            img: img ? img.src : null,
        });
    });
    return items;
}"""


def main():
    out = {"ts": datetime.now().isoformat(), "items": [], "errors": [],
           "source": "v3_keyword_search", "by_keyword": {}}
    seen = set()
    bm = BrowserManagerV6(action="post")
    try:
        bm.start()
        bm.page.goto("https://affiliate.rakuten.co.jp/", wait_until="domcontentloaded", timeout=40000)
        time.sleep(3)
        if "ログイン" in (bm.page.title() or "") :
            out["errors"].append({"err": "not_logged_in"})

        for kw, genre in KEYWORDS:
            got = 0
            for pg in range(1, MAX_PAGES + 1):
                url = f"https://affiliate.rakuten.co.jp/search?sitem={quote(kw)}&p={pg}"
                try:
                    bm.page.goto(url, wait_until="domcontentloaded", timeout=40000)
                    time.sleep(3.5)
                    raw = bm.page.evaluate(EXTRACT_JS)
                    if not raw:
                        break
                    added = 0
                    for it in raw:
                        u = it.get("url")
                        if not u or u in seen:
                            continue
                        if (it.get("rate") or 0) < MIN_RATE:
                            continue
                        if (it.get("price") or 0) < MIN_PRICE:
                            continue
                        nm = it.get("name") or ""
                        if "法人限定" in nm or "業務用" in nm:
                            continue
                        seen.add(u)
                        it["genre"] = genre
                        it["keyword"] = kw
                        out["items"].append(it)
                        added += 1; got += 1
                    print(f"  {kw} p{pg}: +{added} (累計{len(out['items'])})", flush=True)
                    if added == 0 and pg >= 3:
                        break   # 料率条件で拾えないページが続いたら次のキーワードへ
                except Exception as e:
                    out["errors"].append({"kw": kw, "p": pg, "err": str(e)[:120]})
                    print(f"  ERR {kw} p{pg}: {e}", flush=True)
                    break
                time.sleep(1.2)
            out["by_keyword"][kw] = got
            print(f"  == {kw}: {got}件 ==", flush=True)
            time.sleep(1.5)
    except Exception as e:
        out["fatal"] = str(e)[:200]
    finally:
        try: bm.stop()
        except Exception: pass

    # 空の結果で既存プールを壊さない (v2 の事故の再発防止)
    if not out["items"]:
        out["aborted"] = "0件のため既存 high_rate_v2.json を上書きしない"
        Path(r"X:\_high_rate_v3_result.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ABORT 0件 (既存を保持) errors={len(out['errors'])}")
        return

    try:
        if OUT.exists():
            BACKUP.write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DONE total {len(out['items'])} 件 errors={len(out['errors'])}")


if __name__ == "__main__":
    main()
