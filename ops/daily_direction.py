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
# 月次ランプ (direction_plan.md §1 と一致させる)。ハードコードの「7月」表示で
# 月が変わっても古い月を出し続けていたため、当月キーで引く方式にした。
MONTH_RAMP = {
    "2026-07": 3000,    # 土台
    "2026-08": 5000,    # 8/01 実測で引き直し (旧12000は願望値。7月実績¥1,882×2.7)
    "2026-09": 30000,
    "2026-10": 60000,
    "2026-11": 100000,
}
MONTH_REWARD_TARGET = MONTH_RAMP.get(date.today().strftime("%Y-%m"), 100000)


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


STALE_DAYS = 3   # アフィリ CSV がこれ以上古ければ「現在値」として扱わない


def _month_reward() -> tuple[int, int, int, dict]:
    """アフィリ CSV から当月 報酬/クリック/売上 を返す (取込済スナップショット).

    2026-08-01 修正: CSV は CEO の手動DL依存で、7/21 以降 11日間更新されていない
    のに ¥1,357 を「今月の実績」として毎日報告し続けていた。
    古い数字を現在値として出すのは虚偽報告と同じなので、鮮度と対象月を必ず添える。
    戻り値の4つ目 meta に age_days / period / stale / mismatch を入れる。
    """
    import csv, os
    p = REPO / "rakuten-room" / "bot" / "data" / "affiliate_shop_latest.csv"
    meta = {"path": str(p), "exists": p.exists(), "stale": True,
            "age_days": None, "period": None, "period_mismatch": None}
    try:
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(p))).days
        meta["age_days"] = age
        meta["stale"] = age >= STALE_DAYS

        rows = list(csv.reader(open(p, encoding="utf-8-sig")))
        # 先頭行に "期間別成果: 2026.07" のような対象期間が入っている
        for r in rows[:3]:
            if r and "期間" in r[0]:
                meta["period"] = r[0].split(":")[-1].strip()
                break
        if meta["period"]:
            meta["period_mismatch"] = meta["period"].replace(".", "-") != date.today().strftime("%Y-%m")

        data = [r for r in rows if len(r) >= 5 and r[1].strip().isdigit()]
        return (sum(int(r[1]) for r in data),
                sum(int(r[2]) for r in data),
                sum(int(r[3]) for r in data), meta)
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {e}"[:120]
        return (-1, -1, -1, meta)


def _concept_metrics() -> dict:
    """コンセプト遵守の計測 (CEO 2026-08-01「実行が計画どおりか確認する」)。

    量(投稿数)ではなく質を見る。正典:
    09_INTELLIGENCE/room_growth/concept_and_longterm_plan.md §5
      - 当日投稿のうちコンセプト内の比率
      - クリック / フォロワー (濃さ)
    """
    m = {"posted_today": None, "on_concept": None, "on_concept_rate": None,
         "followers": None, "click_per_follower": None}
    # 1) 当日投稿がコンセプト内か (公開APIの真値 × concept_filter)
    try:
        sys.path.insert(0, str(REPO / "ops" / "vm_v6"))
        from concept_filter import is_on_concept
        d = _api("/collects?limit=50").get("data", [])
        today = str(date.today())
        todays = [p for p in d if str(p.get("created_at", ""))[:10] == today]
        m["posted_today"] = len(todays)
        if todays:
            ok = 0
            for p in todays:
                item = {"genre": "", "name": str(p.get("name") or "")}
                if is_on_concept(item)[0]:
                    ok += 1
            m["on_concept"] = ok
            m["on_concept_rate"] = round(ok / len(todays) * 100, 1)
    except Exception as e:
        m["error"] = f"{type(e).__name__}: {e}"[:100]

    # 2) 濃さ = クリック / フォロワー
    try:
        u = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://room.rakuten.co.jp/api/{OWN_USER_ID}", headers=UA),
            timeout=15).read().decode("utf-8", "replace"))
        ud = u.get("data", u)
        m["followers"] = ud.get("followers")
        m["following"] = ud.get("following_users")
        if m["followers"]:
            m["follow_ratio"] = round((m["following"] or 0) / m["followers"], 2)
    except Exception:
        pass
    return m


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
    reward, clicks, sales, rmeta = _month_reward()
    concept = _concept_metrics()
    if concept.get("followers") and clicks and clicks > 0:
        concept["click_per_follower"] = round(clicks / concept["followers"] * 100, 2)
    return {
        "date": str(date.today()),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "daily": {
            "post": {"actual": post, "target": DAILY_TARGET["post"]},
            "follow": {"actual": follow, "target": DAILY_TARGET["follow"]},
            "like": {"actual": like, "target": DAILY_TARGET["like"]},
        },
        "month": {"reward": reward, "clicks": clicks, "sales": sales,
                  "reward_target": MONTH_REWARD_TARGET, "source": rmeta},
        "concept": concept,
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
    print(f"--- 今月 ({date.today().strftime('%Y-%m')} 目標 ¥{MONTH_REWARD_TARGET:,}) ---")
    if m["reward"] >= 0:
        pct = int(m["reward"] / MONTH_REWARD_TARGET * 100)
        cpc = f"¥{m['reward']/m['clicks']:.0f}" if m["clicks"] else "-"
        print(f"  報酬   : ¥{m['reward']:,}/{MONTH_REWARD_TARGET:,} ({pct}%) "
              f"/ クリック {m['clicks']} / 売上 {m['sales']} / 単価 {cpc}")
        print(f"  10万円まで: {100000/max(m['reward'],1):.0f}x")
        # 鮮度を必ず添える。古い数字を現在値として黙って出さない。
        src = m.get("source") or {}
        if src.get("stale"):
            print(f"  ⚠ このデータは {src.get('age_days')}日前の取込 "
                  f"(期間={src.get('period')})。最新CSVの取込が必要 (CEO手動DL)")
        if src.get("period_mismatch"):
            print(f"  ⚠ CSVの対象期間 {src.get('period')} が当月と不一致。"
                  f"当月の実績は未取得")
    else:
        print("  報酬   : CSV 取得不可 (要取込)")

    # --- コンセプト遵守 (量ではなく質を見る) ---
    c = d.get("concept") or {}
    print("--- コンセプト遵守 (計画どおり実行できているか) ---")
    if c.get("posted_today") is not None:
        r = c.get("on_concept_rate")
        mark = "✅" if (r or 0) >= 90 else ("🟡" if (r or 0) >= 70 else "🔴")
        print(f"  投稿の軸一致: {mark} {c.get('on_concept')}/{c.get('posted_today')} ({r}%)")
    if c.get("followers"):
        cpf = c.get("click_per_follower")
        print(f"  濃さ(クリック/フォロワー): {cpf}%  (フォロワー {c['followers']:,})")
        fr = c.get("follow_ratio")
        if fr is not None:
            fmark = "✅" if fr <= 0.7 else ("🟡" if fr <= 1.2 else "🔴")
            print(f"  フォロー比: {fmark} {fr} (目標 0.5前後 / 1.0超は相互狙いに見える)")
    if c.get("error"):
        print(f"  ⚠ 計測エラー: {c['error']}")

    print("\nコンセプト: 09_INTELLIGENCE/room_growth/concept_and_longterm_plan.md")
    print("計画: 09_INTELLIGENCE/room_growth/direction_plan.md")


if __name__ == "__main__":
    main()
