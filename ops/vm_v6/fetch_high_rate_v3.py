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
try:
    from concept_filter import is_on_concept
except Exception:      # スケジューラ実行で W:\ が sys.path に無い場合の保険
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from concept_filter import is_on_concept

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

# --- 選定基準 (2026-08-02 サイバー判断で変更) ---
# 旧: 料率20%以上のみ (CEO 2026-06-14 指示)
# 変更理由 (7月実測): 成約5件の実効料率は 20% / 4% / 4% / 4% / 2% で、
#   **20%だったのは1件だけ**。全体の実効料率は 8.2%。
#   20%縛りは仕入れ可能な母数の大半を捨てる一方、得られた成果は1件だった。
#   さらに楽天側の仕様変更で rmin= による20%抽出自体が不可能になった。
# 新: 料率はゲートにせず「スコア加点」に降格し、
#   **コンセプト適合 × レビュー実績(信頼) × 単価**で選ぶ。
#   単価を残すのは 報酬 = 単価 × 料率 で単価が効くため。
MIN_RATE = 0.0     # ゲートにしない (スコアで優先度をつける)
MIN_PRICE = 1500   # 低単価すぎると報酬が積み上がらない。ベビー用品の実勢に合わせる
# 2026-08-02 実測: レビュー件数はカード本文に出る商品が少なく (12キーワード回って
# 通過5件)、ゲートにすると供給が枯れる。信頼シグナルとして**スコア加点**に降格し、
# 実際の絞り込みは コンセプト適合 × 単価 で行う。
MIN_REVIEWS = 0
MAX_PRICE = 20000  # これ以上は「ママ友のおすすめ」の範囲を超える (ベビーカー等の高額品)
MAX_PAGES = 8      # 1キーワードあたり (30件/ページ)

# 仕入れ対象外。コンセプト(日常の買い物の参考)に合わないもの
EXCLUDE_WORDS = ("ふるさと納税", "法人限定", "業務用", "中古", "訳あり", "福袋")

EXTRACT_JS = r"""() => {
    const items = [];
    document.querySelectorAll('.raf-product__item').forEach(card => {
        const txt = card.innerText || '';
        // 料率はゲートではないので、非表示の商品も落とさず rate=0 として通す
        // (Codex 指摘5: !rm で return すると母集団が不要に縮む)
        const rm = txt.match(/料率\s*([0-9.]+)\s*[%％]/);
        const pm = txt.match(/([0-9,]+)\s*円/);
        // レビュー件数 (「口コミ5900件」「レビュー1,234件」等) を信頼シグナルとして拾う
        const rv = txt.match(/(?:口コミ|レビュー|review)[^0-9]{0,4}([0-9,]+)\s*件/i);
        const nameEl = card.querySelector('[class*=raf-product__name]');
        const linkEl = card.querySelector('a[href*="item.rakuten.co.jp"]');
        const img = card.querySelector('img');
        items.push({
            rate: rm ? parseFloat(rm[1]) : 0,
            price: pm ? parseInt(pm[1].replace(/,/g, '')) : null,
            reviews: rv ? parseInt(rv[1].replace(/,/g,'')) : 0,
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
            empty_streak = 0
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
                        if (it.get("price") or 0) < MIN_PRICE:
                            continue
                        if (it.get("price") or 0) > MAX_PRICE:
                            continue
                        nm = it.get("name") or ""
                        if any(w in nm for w in EXCLUDE_WORDS):
                            continue
                        # コンセプト適合を仕入れ段階で強制する (投稿側でも二重に効く)
                        ok, why = is_on_concept({"genre": genre, "name": nm})
                        if not ok:
                            continue
                        # スコア: 単価 × 料率 = 想定報酬。レビューで信頼を加味
                        it["est_reward"] = round((it.get("price") or 0) * (it.get("rate") or 0) / 100, 1)
                        it["score"] = round(it["est_reward"] * (1 + min(it.get("reviews", 0), 3000) / 3000), 1)
                        seen.add(u)
                        it["genre"] = genre
                        it["keyword"] = kw
                        out["items"].append(it)
                        added += 1; got += 1
                    print(f"  {kw} p{pg}: +{added} (累計{len(out['items'])})", flush=True)
                    # Codex 指摘3: 料率ゲート撤廃後は価格/除外語で 0件のページが
                    # 途中に混ざりうる。1ページ0件で打ち切らず、連続2回で次へ。
                    empty_streak = empty_streak + 1 if added == 0 else 0
                    if empty_streak >= 2:
                        break
                except Exception as e:
                    msg = str(e)
                    out["errors"].append({"kw": kw, "p": pg, "err": msg[:120]})
                    print(f"  ERR {kw} p{pg}: {e}", flush=True)
                    # 2026-08-02: 初回実行はここでブラウザが落ちて以降全滅した。
                    # Chrome が死んだら作り直して継続する (投稿側と同じ対処)。
                    if "has been closed" in msg or "Target page" in msg:
                        try:
                            try: bm.stop()
                            except Exception: pass
                            bm = BrowserManagerV6(action="post")
                            bm.start()
                            bm.page.goto("https://affiliate.rakuten.co.jp/",
                                         wait_until="domcontentloaded", timeout=40000)
                            time.sleep(3)
                            out["browser_restarts"] = out.get("browser_restarts", 0) + 1
                            print(f"  [recover] ブラウザ再起動 ({out['browser_restarts']}回目)", flush=True)
                            continue
                        except Exception as e2:
                            print(f"  [recover] 失敗: {e2}", flush=True)
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
    # 想定報酬の高い順に並べる (投稿側が先頭から使う想定)
    out["items"].sort(key=lambda x: x.get("score", 0), reverse=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    top = out["items"][:3]
    print(f"DONE total {len(out['items'])} 件 errors={len(out['errors'])}")
    for t in top:
        print(f"  上位: score={t.get('score')} 料率{t.get('rate')}% ¥{t.get('price')} "
              f"レビュー{t.get('reviews')} {t.get('name','')[:30]}")


if __name__ == "__main__":
    main()
