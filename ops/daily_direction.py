# -*- coding: utf-8 -*-
"""日次方向性スコアカード — 計画 (direction_plan.md) 対比で当日実績を採点する.

CEO 2026-07-23「月/週/日の方向性を示し、実行監視せよ」への実装。
毎日スケジュール実行し、計画対比の達成率を出す。オフトラックは可視化し、
基盤障害(全機能停止など)は patrol の critical 経路で別途 Slack 通知される。

真値の取り方 (虚偽を混ぜない):
- POST   : 公開 ROOM API /collects の当日 created_at
- FOLLOW : 公開 API /following_users を ID 突合した follow_history (検証済)
- LIKE   : like_history.json 当日
- FB     : followback 実績 (room_status 経由の真値)
- クリック: アフィリ CSV (手動DL依存・取込時のみ)

usage: python ops/daily_direction.py [--json]
"""
from __future__ import annotations
import json
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OWN_USER_ID = "1000006606047125"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 今週の日次目標 (direction_plan.md §3 と一致させる)
DAILY_TARGET = {"post": 40, "follow": 300, "like": 150, "followback": 50, "clicks": 10}
MONTH_REWARD_TARGET = 3000   # 7月 (残8日の土台月)


def _api(path: str):
    url = f"https://room.rakuten.co.jp/api/{OWN_USER_ID}{path}"
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace"))


def _today_posts() -> int:
    try:
        d = _api("/collects?limit=50").get("data", [])
        return Counter(str(p.get("created_at", ""))[:10] for p in d).get(str(date.today()), 0)
    except Exception:
        return -1


def _today_follows() -> int:
    """検証済 follow_history の当日件数 (ID突合済みの真値のみ記録されている)."""
    p = REPO / "rakuten-room" / "bot" / "data" / "follow_history.json"
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
        t = str(date.today())
        return sum(1 for e in h if str(e.get("followed_at", "")).startswith(t))
    except Exception:
        return -1


def _today_likes() -> int:
    p = REPO / "rakuten-room" / "bot" / "data" / "like_history.json"
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
        t = str(date.today())
        return sum(1 for e in h if str(e.get("liked_at", "")).startswith(t))
    except Exception:
        return -1


def _month_reward() -> tuple[int, int, int]:
    """アフィリ CSV から当月 報酬/クリック/売上 を返す (取込済スナップショット)."""
    import csv
    p = REPO / "rakuten-room" / "bot" / "data" / "affiliate_shop_latest.csv"
    try:
        rows = list(csv.reader(open(p, encoding="utf-8-sig")))
        data = [r for r in rows if len(r) >= 5 and r[1].strip().isdigit()]
        return (sum(int(r[1]) for r in data),
                sum(int(r[2]) for r in data),
                sum(int(r[3]) for r in data))
    except Exception:
        return (-1, -1, -1)


def _bar(actual: int, target: int) -> str:
    if actual < 0:
        return "取得不可"
    pct = int(actual / target * 100) if target else 0
    mark = "✅" if pct >= 100 else ("🟡" if pct >= 60 else "🔴")
    return f"{mark} {actual}/{target} ({pct}%)"


def build() -> dict:
    post = _today_posts()
    follow = _today_follows()
    like = _today_likes()
    reward, clicks, sales = _month_reward()
    return {
        "date": str(date.today()),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "daily": {
            "post": {"actual": post, "target": DAILY_TARGET["post"]},
            "follow": {"actual": follow, "target": DAILY_TARGET["follow"]},
            "like": {"actual": like, "target": DAILY_TARGET["like"]},
        },
        "month": {"reward": reward, "clicks": clicks, "sales": sales,
                  "reward_target": MONTH_REWARD_TARGET},
    }


def main():
    d = build()
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(f"=== 日次方向性スコアカード {d['date']} ===")
    print("--- 今日 (今週目標対比) ---")
    print("  POST   :", _bar(d["daily"]["post"]["actual"], DAILY_TARGET["post"]))
    print("  FOLLOW :", _bar(d["daily"]["follow"]["actual"], DAILY_TARGET["follow"]))
    print("  LIKE   :", _bar(d["daily"]["like"]["actual"], DAILY_TARGET["like"]))
    m = d["month"]
    print(f"--- 今月 (7月 土台目標 ¥{MONTH_REWARD_TARGET:,}) ---")
    if m["reward"] >= 0:
        pct = int(m["reward"] / MONTH_REWARD_TARGET * 100)
        cpc = f"¥{m['reward']/m['clicks']:.0f}" if m["clicks"] else "-"
        print(f"  報酬   : ¥{m['reward']:,}/{MONTH_REWARD_TARGET:,} ({pct}%) "
              f"/ クリック {m['clicks']} / 売上 {m['sales']} / 単価 {cpc}")
        print(f"  10万円まで: {100000/max(m['reward'],1):.0f}x")
    else:
        print("  報酬   : CSV 取得不可 (要取込)")
    print("\n計画: 09_INTELLIGENCE/room_growth/direction_plan.md")


if __name__ == "__main__":
    main()
