#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コンセプト適合フィルタ (HOST 側からの入口)。

実体は ops/vm_v6/concept_filter.py に置いてある。
理由: VM の共有ドライブは W:=ops/vm_v6 / Z:=rakuten-room/bot までで、
リポジトリ直下の ops/ は VM から見えない。VM 内の投稿処理から import
できる場所に実体を置き、HOST 側はここから再エクスポートする。
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vm_v6"))
from concept_filter import (  # noqa: E402,F401
    GENRE_IN, GENRE_OUT, NG_KEYWORDS, OK_KEYWORDS,
    is_on_concept, filter_items, summarize,
)

if __name__ == "__main__":
    REPO = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else         REPO / "rakuten-room" / "bot" / "data" / "ranking_pool.json"
    r = summarize(target)
    print(f"=== コンセプト適合率: {r['path']} ===")
    print(f"  総数 {r['total']} / 適合 {r['on_concept']} ({r['rate']}%) / 除外 {r['off_concept']}")
    print("  --- 除外理由 ---")
    for why, n in r["drop_reasons"]:
        print(f"    {n:>4}件  {why}")
    print("  --- 通過したジャンル ---")
    for g, n in r["keep_genres"]:
        print(f"    {n:>4}件  {g}")
