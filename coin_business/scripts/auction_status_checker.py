"""
auction_status_checker.py

auction_schedule.json を読み込み、各オークションのステータスと監視頻度を返す。

使い方:
  from scripts.auction_status_checker import get_active_auctions, get_watch_interval_minutes

  actives = get_active_auctions()   # active / imminent のオークション一覧
  for a in actives:
      print(a['name'], a['_status'], a['_interval_min'], '分毎')
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── パス ──────────────────────────────────────────────────────────
_DIR           = Path(__file__).parent
_SCHEDULE_FILE = _DIR.parent / "data" / "auction_schedule.json"


# ── ステータス定義 ───────────────────────────────────────────────

STATUS_UPCOMING  = "upcoming"   # 開催7日超前
STATUS_IMMINENT  = "imminent"   # 開催7日以内（準備フェーズ）
STATUS_ACTIVE    = "active"     # 開催中
STATUS_ENDED     = "ended"      # 終了済み

# 監視間隔（分）  priority=3 × active が最高頻度
_INTERVAL_MAP: dict[tuple[str, int], int] = {
    (STATUS_ACTIVE,   3): 30,    # 最重要開催中 → 30分ごと
    (STATUS_ACTIVE,   2): 60,    # 重要開催中   → 1時間ごと
    (STATUS_ACTIVE,   1): 120,   # 通常開催中   → 2時間ごと
    (STATUS_IMMINENT, 3): 120,   # 最重要直前   → 2時間ごと
    (STATUS_IMMINENT, 2): 240,   # 重要直前     → 4時間ごと
    (STATUS_IMMINENT, 1): 480,   # 通常直前     → 8時間ごと
    (STATUS_UPCOMING, 3): 1440,  # 通常         → 1日1回
    (STATUS_UPCOMING, 2): 1440,
    (STATUS_UPCOMING, 1): 1440,
    (STATUS_ENDED,    3): 0,
    (STATUS_ENDED,    2): 0,
    (STATUS_ENDED,    1): 0,
}
_IMMINENT_DAYS = 14  # 開催何日前から imminent とするか（CEO指示: 7→14日に拡張）


# ── コア関数 ──────────────────────────────────────────────────────

def get_auction_status(auction: dict, today: Optional[date] = None) -> str:
    """
    1件のオークションエントリについてステータス文字列を返す。

    Returns:
      "upcoming"  : 開催 7日超前
      "imminent"  : 開催 7日以内
      "active"    : 開催中
      "ended"     : 終了済み
    """
    today = today or date.today()
    try:
        start = date.fromisoformat(auction["start_date"])
        end   = date.fromisoformat(auction["end_date"])
    except (KeyError, ValueError) as e:
        logger.warning(f"auction date parse error [{auction.get('id')}]: {e}")
        return STATUS_ENDED

    if today > end:
        return STATUS_ENDED
    elif start <= today <= end:
        return STATUS_ACTIVE
    elif (start - today).days <= _IMMINENT_DAYS:
        return STATUS_IMMINENT
    else:
        return STATUS_UPCOMING


def get_watch_interval_minutes(auction: dict, today: Optional[date] = None) -> int:
    """
    監視間隔を分単位で返す（0 = 監視不要）。
    """
    status   = get_auction_status(auction, today)
    priority = int(auction.get("priority", 1))
    priority = max(1, min(3, priority))  # 1〜3 にクランプ
    return _INTERVAL_MAP.get((status, priority), 1440)


def load_schedule() -> list[dict]:
    """auction_schedule.json を読み込んで auctions リストを返す。"""
    if not _SCHEDULE_FILE.exists():
        logger.warning(f"schedule file not found: {_SCHEDULE_FILE}")
        return []
    try:
        data = json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
        return data.get("auctions", [])
    except Exception as e:
        logger.error(f"schedule file read error: {e}")
        return []


def get_all_auctions_with_status(today: Optional[date] = None) -> list[dict]:
    """
    全オークションにステータス・監視間隔を付加して返す。
    ended は含まない（監視不要なため）。
    """
    today = today or date.today()
    result = []
    for a in load_schedule():
        status   = get_auction_status(a, today)
        interval = get_watch_interval_minutes(a, today)
        entry = dict(a)
        entry["_status"]       = status
        entry["_interval_min"] = interval
        entry["_today"]        = today.isoformat()
        result.append(entry)
    return sorted(result, key=lambda x: (x.get("priority", 0), x.get("start_date", "")), reverse=True)


def get_active_auctions(today: Optional[date] = None) -> list[dict]:
    """
    現在 active または imminent なオークションを優先度順に返す。
    これが「今監視すべき対象」。
    """
    return [
        a for a in get_all_auctions_with_status(today)
        if a["_status"] in (STATUS_ACTIVE, STATUS_IMMINENT)
    ]


def get_april_focus_auctions(today: Optional[date] = None) -> list[dict]:
    """april_focus=true のオークションを返す（4月重点監視対象）。"""
    return [
        a for a in get_all_auctions_with_status(today)
        if a.get("april_focus") is True and a["_status"] != STATUS_ENDED
    ]


def should_fetch_now(auction: dict, last_fetched_minutes_ago: int,
                     today: Optional[date] = None) -> bool:
    """
    前回取得から last_fetched_minutes_ago 分経過した場合に、
    今回取得すべきかを判定する。
    """
    interval = get_watch_interval_minutes(auction, today)
    if interval == 0:
        return False
    return last_fetched_minutes_ago >= interval


# ── CLI用サマリー ─────────────────────────────────────────────────

def print_schedule_summary(today: Optional[date] = None) -> None:
    """監視スケジュールのサマリーをコンソール出力。"""
    today = today or date.today()
    all_a = get_all_auctions_with_status(today)

    print(f"\n=== オークション監視スケジュール ({today}) ===\n")

    status_labels = {
        STATUS_ACTIVE:   "🟢 開催中",
        STATUS_IMMINENT: "🟡 直前 (7日以内)",
        STATUS_UPCOMING: "⚪ 開催前",
        STATUS_ENDED:    "⛔ 終了",
    }

    for status in [STATUS_ACTIVE, STATUS_IMMINENT, STATUS_UPCOMING, STATUS_ENDED]:
        group = [a for a in all_a if a["_status"] == status]
        if not group:
            continue
        print(f"{status_labels[status]}:")
        for a in group:
            interval = a["_interval_min"]
            interval_str = f"{interval}分ごと" if interval > 0 else "監視不要"
            start = a.get("start_date", "?")
            end   = a.get("end_date", "?")
            prio  = a.get("priority", 1)
            name  = a.get("name", a.get("id", "?"))[:55]
            print(f"  P{prio} [{interval_str:10}] {name}  ({start}〜{end})")
        print()


# ── スタンドアロン実行 ────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print_schedule_summary()

    actives = get_active_auctions()
    print(f"監視対象: {len(actives)}件 (active/imminent)")
    april   = get_april_focus_auctions()
    print(f"4月重点: {len(april)}件")
