#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_high_rate_v3 が VM 内で正しく import でき、
concept_filter の読込元が特定できるかを確認する (回帰確認用)."""
# 対象モジュールが sys.stdout を差し替えるため、結果はファイルに書く
import sys, json
from pathlib import Path
sys.path.insert(0, "W:\\")

res = {}
try:
    import fetch_high_rate_v3 as m
    res["import"] = "OK"
    res["CONCEPT_FILTER_PATH"] = m.CONCEPT_FILTER_PATH
    res["MIN_PRICE"] = m.MIN_PRICE
    res["MAX_PRICE"] = m.MAX_PRICE
    res["MIN_RATE"] = m.MIN_RATE
    res["KEYWORDS"] = len(m.KEYWORDS)
    ok, why = m.is_on_concept({"genre": "キッズ・ベビー・マタニティ", "name": "ベビー肌着"})
    res["sample"] = [ok, why]
except Exception as e:
    import traceback
    res["import"] = "FAIL"
    res["error"] = f"{type(e).__name__}: {e}"
    res["tb"] = traceback.format_exc()[-600:]

Path(r"X:\_v3_import_probe.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
