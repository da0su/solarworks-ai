"""follow_history.json 集計ヘルパー (SSOT).

【背景】 2026-05-12 CEO 「スプシ FOLLOW 数値が乖離している」ご指摘で発覚:
follow_history.json は schema 「全 entry が "実フォロー" を意味する」と
仮定する古い reader 群があり、CEO スプシに **skip_discover を含めた合計** を
書き込んでいた (1376 vs 真値 539 = 837 inflate).

【schema 規約】 source 値の意味:
  - "seed_followers": follow_via_seeds.py で実フォロー成功
  - "host_*"        : 旧 HOST 経由実フォロー
  - "cli"           : 手動 CLI 実フォロー
  - "daily_plan"    : 旧 daily_plan 実フォロー
  - "skip_discover" : 【NOT a real follow】Rakuten 側で既フォロー判定の検知 (再試行回避用)
  - "<no source>"   : 旧 entry (legacy・実フォロー扱い)

【使い方】 follow_history を読む全 module はこの helper を使う:
    from shared.follow_history_reader import count_real_follows_on
    n = count_real_follows_on("2026-05-12")  # 実フォロー数を返す

【再発防止】 新しい source 値追加時は本ファイル冒頭の表に記載 + UNIT TEST 必須.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
HIST_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "follow_history.json"

# source が実フォローではないと判定する set
NON_FOLLOW_SOURCES: set[str] = {"skip_discover"}


def _load_history() -> list[dict]:
    if not HIST_PATH.exists():
        return []
    try:
        data = json.loads(HIST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def is_real_follow(entry: dict) -> bool:
    """エントリーが実フォローかを判定."""
    if not isinstance(entry, dict):
        return False
    src = entry.get("source", "")
    if src in NON_FOLLOW_SOURCES:
        return False
    return True


def count_real_follows_on(date_str: str) -> int:
    """指定日 (YYYY-MM-DD) の実フォロー数を返す.

    skip_discover (再試行回避用記録) は除外する.
    """
    n = 0
    for entry in _load_history():
        if not is_real_follow(entry):
            continue
        if str(entry.get("followed_at", ""))[:10] == date_str:
            n += 1
    return n


def count_by_source_on(date_str: str) -> dict[str, int]:
    """指定日の source 別分布."""
    from collections import Counter
    c: Counter[str] = Counter()
    for entry in _load_history():
        if str(entry.get("followed_at", ""))[:10] == date_str:
            c[entry.get("source", "<no_source>")] += 1
    return dict(c)


def list_real_follows_on(date_str: str) -> list[dict]:
    """指定日の実フォロー entry を返す (debug / verify 用)."""
    return [e for e in _load_history()
            if is_real_follow(e) and str(e.get("followed_at", ""))[:10] == date_str]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        d = sys.argv[1]
    else:
        from datetime import datetime
        d = datetime.now().strftime("%Y-%m-%d")
    print(f"=== {d} ===")
    print(f"real follows: {count_real_follows_on(d)}")
    print(f"by source: {count_by_source_on(d)}")
