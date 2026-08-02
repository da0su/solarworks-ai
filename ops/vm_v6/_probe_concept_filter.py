#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM 内から concept_filter を import できるかの確認 (棚卸し/回帰用)."""
import sys, io, json
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, "W:\\")

from concept_filter import filter_items, is_on_concept

samples = [
    {"genre": "キッズ・ベビー・マタニティ", "name": "ベビー肌着 綿100%"},
    {"genre": "レディースファッション", "name": "レディース ワンピース"},
    {"genre": "レディースファッション", "name": "マタニティ パジャマ 授乳口付き"},
    {"genre": "ダイエット・健康", "name": "ダイエット サプリ"},
    {"genre": "食品", "name": "有機 離乳食 无添加セット"},
]
keep, drop = filter_items(samples)
print(f"VM IMPORT OK  keep={len(keep)} drop={len(drop)}")
for s in samples:
    ok, why = is_on_concept(s)
    print(f"  {'PASS' if ok else 'DROP'} | {s['genre'][:12]:<12} | {s['name'][:22]:<22} | {why}")

# 実プールでも通るか
try:
    pool = json.loads(open(r"X:\ranking_pool.json", encoding="utf-8").read())
    items = pool if isinstance(pool, list) else pool.get("items", [])
    k, d = filter_items(items)
    print(f"REAL POOL: total={len(items)} keep={len(k)} drop={len(d)}")
except Exception as e:
    print("REAL POOL check skipped:", type(e).__name__, e)
