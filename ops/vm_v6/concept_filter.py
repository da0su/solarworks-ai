#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コンセプト適合フィルタ — 「空くんと新米ママ」の軸に合う商品だけを通す。

正典: 09_INTELLIGENCE/room_growth/concept_and_longterm_plan.md

なぜ必要か (2026-08-01 実測):
  投稿の供給源 ranking_pool.json の構成は
    キッズ・ベビー 28.5% / スイーツ 19.5%  = コンセプト内 47.9%
    レディースファッション 26.2% / ダイエット健康 25.8% = コンセプト外 52.1%
  7月の成約は「子ども向けオーガニック」(料率20%) の1件のみ。
  一方 コンセプト外ジャンルは ミューズラボ15clk / BAMBI WATER 12clk /
  オーガランド7clk / aquagarage 5clk とクリックはされるが**成約ゼロ**。
  → 供給の半分が「クリックされても売れないもの」で占められていた。

使い方:
  from ops.concept_filter import is_on_concept, filter_items, summarize
  ok, reason = is_on_concept(item)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# このファイルは ops/vm_v6/ に置く (VM から W:\ で import できる場所)。
# VM では W:\concept_filter.py として直下に見えるため parents[2] が存在しない。
# REPO は HOST 側の CLI 実行でしか使わないので、取れなければ None にする。
def _repo_root():
    try:
        return Path(__file__).resolve().parents[2]
    except IndexError:
        return None

REPO = _repo_root()

# --- 誰に/何を (§2) をジャンル粒度で表現 -------------------------------
# 「子どもの口に入る・肌に触れるもの / ママ自身をラクにするもの」
GENRE_IN = {
    "キッズ・ベビー・マタニティ",
    "食品",
    "スイーツ・お菓子",
    "日用品雑貨・文房具・手芸",
    "キッチン用品・食器・調理器具",
    "インテリア・寝具・収納",
}
# 明示的に出さないジャンル (7月に成約ゼロだった層)
GENRE_OUT = {
    "レディースファッション",
    "ダイエット・健康",
    "メンズファッション",
    "美容・コスメ・香水",
    "家電",
    "スマートフォン・タブレット",
    "車・バイク",
}

# ジャンルが「食品」等で広い場合に、大人向けへ流れるのを止めるキーワード
NG_KEYWORDS = (
    "ダイエット", "痩せ", "バストアップ", "育毛", "白髪", "シワ", "たるみ",
    "青汁", "酵素", "コラーゲン", "プロテイン", "着圧", "補正下着",
    "メンズ", "紳士", "ゴルフ", "タバコ", "電子たばこ",
)
# 子ども・ママ文脈を強く示すキーワード (ジャンルが曖昧な時の救済)
OK_KEYWORDS = (
    "ベビー", "赤ちゃん", "新生児", "離乳食", "おむつ", "オムツ", "抱っこ紐",
    "child", "キッズ", "子供", "子ども", "こども", "マタニティ", "授乳",
    "무첨가", "無添加", "オーガニック", "有機", "国産", "アレルギー",
)


def is_on_concept(item: dict) -> tuple[bool, str]:
    """商品1件がコンセプト内か判定する。戻り (可否, 理由)。"""
    genre = str(item.get("genre") or "")
    name = str(item.get("name") or "")

    for ng in NG_KEYWORDS:
        if ng in name:
            return False, f"NGキーワード:{ng}"

    if genre in GENRE_OUT:
        # 子ども文脈が明確なら救済する (例: レディース枠のマタニティ服)
        for ok in OK_KEYWORDS:
            if ok in name:
                return True, f"ジャンル外だが子ども文脈:{ok}"
        return False, f"対象外ジャンル:{genre}"

    if genre in GENRE_IN:
        return True, f"対象ジャンル:{genre}"

    for ok in OK_KEYWORDS:
        if ok in name:
            return True, f"子ども文脈:{ok}"
    return False, f"判定不能ジャンル:{genre or '(空)'}"


def filter_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(通過, 除外) に分ける。"""
    keep, drop = [], []
    for it in items:
        ok, why = is_on_concept(it)
        it = dict(it)
        it["_concept_reason"] = why
        (keep if ok else drop).append(it)
    return keep, drop


def summarize(path: Path) -> dict:
    """プールファイルのコンセプト適合率を出す (変更は加えない)。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else d.get("items", [])
    keep, drop = filter_items(items)
    from collections import Counter
    return {
        "path": str(path),
        "total": len(items),
        "on_concept": len(keep),
        "off_concept": len(drop),
        "rate": round(len(keep) / len(items) * 100, 1) if items else None,
        "drop_reasons": Counter(x["_concept_reason"] for x in drop).most_common(6),
        "keep_genres": Counter(x.get("genre") for x in keep).most_common(8),
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        REPO / "rakuten-room" / "bot" / "data" / "ranking_pool.json"
    r = summarize(target)
    print(f"=== コンセプト適合率: {r['path']} ===")
    print(f"  総数 {r['total']} / 適合 {r['on_concept']} ({r['rate']}%) / 除外 {r['off_concept']}")
    print("  --- 除外理由 ---")
    for why, n in r["drop_reasons"]:
        print(f"    {n:>4}件  {why}")
    print("  --- 通過したジャンル ---")
    for g, n in r["keep_genres"]:
        print(f"    {n:>4}件  {g}")
