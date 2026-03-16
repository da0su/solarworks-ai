"""ROOM BOT Scheduler - Daily Plan Generator

CEO運用思想 + CMOマーケティングルールに基づく daily_plan.json を自動生成する。

配分ルール:
  深夜ブロック (0:05〜1:00):
    - 交流優先: like 40%, follow 40%, post 20%
    - follow/like は深夜で45〜55%を消化
    - post は深夜で20〜35%を消化
  朝以降ブロック (8:00〜22:00):
    - 残りを自然分散
    - 投稿は朝以降に多め配分
  揺らし:
    - セッション件数・時刻ともにランダム化
    - 行動順序も毎日変える
    - 毎日固定パターンにしない

ramp_up スケジュール (Day1〜Day7):
  段階的に最終KPIへ近づける。各日にレンジ内で揺らし。
"""

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SCHEDULER_DATA_DIR = Path(__file__).parent.parent / "data" / "scheduler"

# ================================================================
# ramp_up レンジ定義 (Day1〜Day7)
# ================================================================

RAMP_UP_SCHEDULE = {
    1: {"post": (18, 22),   "follow": (36, 44),   "like": (36, 44)},
    2: {"post": (27, 33),   "follow": (54, 66),   "like": (54, 66)},
    3: {"post": (36, 44),   "follow": (72, 88),   "like": (72, 88)},
    4: {"post": (54, 66),   "follow": (108, 132),  "like": (108, 132)},
    5: {"post": (72, 88),   "follow": (144, 176),  "like": (144, 176)},
    6: {"post": (85, 95),   "follow": (170, 190),  "like": (170, 190)},
    7: {"post": (95, 105),  "follow": (180, 220),  "like": (180, 220)},
}

# Day8以降（通常運用）
NORMAL_RANGE = {"post": (95, 105), "follow": (180, 220), "like": (180, 220)}

# light day は normal の 40%
LIGHT_RATIO = 0.4

# ── CMOルール: アクション別 深夜配分比率 ──
# post は深夜に控えめ（20〜35%）
# follow/like は深夜に多め（45〜55%）
NIGHT_RATIO = {
    "post":   (0.20, 0.35),
    "follow": (0.45, 0.55),
    "like":   (0.45, 0.55),
}

# 朝以降の時間帯ブロック (start_hour, end_hour)
DAY_BLOCKS = [
    (8, 10),    # 朝
    (11, 13),   # 昼前後
    (15, 17),   # 午後
    (19, 21),   # 夕方〜夜
]


def get_ramp_up_day(start_date: str, target_date: str) -> int:
    """ramp_up の何日目かを返す（1始まり、8以上は通常運用）"""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
        delta = (target - start).days + 1  # 開始日をDay1とする
        return max(1, delta)
    except ValueError:
        return 8  # パース失敗時は通常運用


def _determine_targets(day_num: int, day_type: str) -> dict:
    """その日の post/follow/like 総件数を揺らし込みで確定する"""
    if day_type == "off":
        return {"post": 0, "follow": 0, "like": 0}

    # レンジ選択
    if day_num <= 7:
        ranges = RAMP_UP_SCHEDULE[day_num]
    else:
        ranges = NORMAL_RANGE

    targets = {}
    for action in ("post", "follow", "like"):
        lo, hi = ranges[action]
        val = random.randint(lo, hi)
        if day_type == "light":
            val = max(1, round(val * LIGHT_RATIO))
        targets[action] = val

    return targets


def _split_night_day(total: int, action: str) -> tuple[int, int]:
    """total を深夜/日中に分割する（CMOルール反映）"""
    if total <= 0:
        return 0, 0
    lo, hi = NIGHT_RATIO.get(action, (0.45, 0.55))
    ratio = random.uniform(lo, hi)
    night = max(1, round(total * ratio))
    day = total - night
    return night, day


def _distribute_to_sessions(count: int, num_sessions: int,
                             min_per_session: int = 1) -> list[int]:
    """count を num_sessions 個に不均等に分配する（揺らし込み）"""
    if count <= 0 or num_sessions <= 0:
        return []
    if num_sessions == 1:
        return [count]

    # 各セッションに最低保証
    result = [min_per_session] * num_sessions
    remaining = count - min_per_session * num_sessions
    if remaining < 0:
        # 最低保証すら満たせない
        result = []
        left = count
        for _ in range(num_sessions):
            give = min(left, min_per_session) if left > 0 else 0
            result.append(give)
            left -= give
        return [r for r in result if r > 0]

    # 残りをランダムに分配（偏りが出るように）
    for _ in range(remaining):
        idx = random.randint(0, num_sessions - 1)
        result[idx] += 1

    # シャッフルして順序の偏りを消す
    random.shuffle(result)
    return result


def _random_time_in_range(start_hour: int, end_hour: int,
                           start_min: int = 0, end_min: int = 59) -> str:
    """指定範囲内のランダムな HH:MM を生成する"""
    if start_hour == end_hour:
        h = start_hour
        m = random.randint(start_min, end_min)
    else:
        h = random.randint(start_hour, end_hour - 1)
        if h == start_hour:
            m = random.randint(start_min, 59)
        else:
            m = random.randint(0, 59)
    return f"{h:02d}:{m:02d}"


def generate_daily_plan(target_date: str = None,
                         start_date: str = None,
                         day_type: str = None) -> dict:
    """daily_plan.json を生成する

    Args:
        target_date: 対象日 (YYYY-MM-DD)。省略時は今日
        start_date: ramp_up開始日。省略時はconfig から読む
        day_type: 強制指定。省略時は ramp_up 中は "ramp_up"

    Returns:
        daily_plan dict
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ramp_up 判定
    if start_date is None:
        start_date = _load_ramp_up_start()

    day_num = get_ramp_up_day(start_date, target_date)

    if day_type is None:
        if day_num <= 7:
            day_type = "ramp_up"
        else:
            day_type = "normal"

    # 総件数を確定（揺らし込み）
    targets = _determine_targets(day_num, day_type)

    # タスク配列を生成
    tasks = []

    if day_type != "off":
        tasks = _build_tasks(targets, day_num)

    # targets 合計と tasks 合計の整合チェック・補正
    for action in ("post", "follow", "like"):
        task_sum = sum(t["count"] for t in tasks if t["action"] == action)
        diff = targets[action] - task_sum
        if diff != 0:
            last = next(
                (t for t in reversed(tasks) if t["action"] == action), None
            )
            if last:
                last["count"] = max(1, last["count"] + diff)

    plan = {
        "version": "1.0",
        "date": target_date,
        "day_type": day_type,
        "day_num": day_num,
        "generated_at": datetime.now().isoformat(),
        "start_date": start_date,
        "targets": targets,
        "tasks": tasks,
    }

    # 保存
    _save_plan(plan)

    return plan


def _build_tasks(targets: dict, day_num: int) -> list:
    """CMOルール反映: タスク配列を生成する"""
    tasks = []

    # ── 先に各actionの深夜/日中分割を確定（1回だけ計算） ──
    splits = {}
    for action in ("post", "follow", "like"):
        total = targets.get(action, 0)
        if total > 0:
            night, day = _split_night_day(total, action)
            splits[action] = (night, day)

    # ── 深夜ブロックの行動順序をランダム化 ──
    # CMOルール: 深夜は交流優先（like/follow先、post後）
    night_order = ["like", "follow", "post"]
    if random.random() > 0.5:
        night_order = ["follow", "like", "post"]

    # ── 深夜ブロック (0:05〜0:55) ──
    night_base_min = random.randint(5, 15)  # 開始は0:05〜0:15
    night_cursor_min = night_base_min

    for action in night_order:
        if action not in splits:
            continue

        night_count = splits[action][0]
        if night_count <= 0:
            continue

        # 深夜セッション数: 件数に応じて1〜3
        if night_count <= 5:
            n_sessions = 1
        elif night_count <= 20:
            n_sessions = random.randint(1, 2)
        else:
            n_sessions = random.randint(2, 3)

        night_dist = _distribute_to_sessions(night_count, n_sessions)

        for cnt in night_dist:
            if cnt <= 0:
                continue
            if night_cursor_min > 55:
                night_cursor_min = 55
            time_str = f"00:{night_cursor_min:02d}"
            tasks.append({
                "time": time_str,
                "action": action,
                "count": cnt,
                "enabled": True,
            })
            night_cursor_min += random.randint(5, 12)

    # ── 朝以降ブロック (8:00〜22:00) ──
    day_actions = list(splits.keys())
    random.shuffle(day_actions)

    for action in day_actions:
        day_count = splits[action][1]
        if day_count <= 0:
            continue

        # 利用ブロック数: 件数に応じて2〜4
        if day_count <= 10:
            n_blocks = min(2, len(DAY_BLOCKS))
        elif day_count <= 30:
            n_blocks = min(3, len(DAY_BLOCKS))
        else:
            n_blocks = len(DAY_BLOCKS)

        chosen = random.sample(DAY_BLOCKS, n_blocks)
        chosen.sort(key=lambda b: b[0])

        day_dist = _distribute_to_sessions(day_count, n_blocks)

        for (bstart, bend), cnt in zip(chosen, day_dist):
            if cnt <= 0:
                continue
            time_str = _random_time_in_range(bstart, bend)
            tasks.append({
                "time": time_str,
                "action": action,
                "count": cnt,
                "enabled": True,
            })

    # 時刻順にソート
    tasks.sort(key=lambda t: t["time"])

    return tasks


def _save_plan(plan: dict) -> Path:
    """daily_plan.json を保存する"""
    SCHEDULER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = SCHEDULER_DATA_DIR / "daily_plan.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return path


def load_plan(target_date: str = None) -> dict | None:
    """daily_plan.json を読み込む。日付不一致ならNone"""
    path = SCHEDULER_DATA_DIR / "daily_plan.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (json.JSONDecodeError, Exception):
        return None

    if target_date and plan.get("date") != target_date:
        return None

    return plan


def _load_ramp_up_start() -> str:
    """ramp_up 開始日を読み込む（なければ今日を設定）"""
    config_path = SCHEDULER_DATA_DIR / "scheduler_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("ramp_up_start", datetime.now().strftime("%Y-%m-%d"))
        except Exception:
            pass

    # 初回: 今日を開始日として保存
    start = datetime.now().strftime("%Y-%m-%d")
    _save_ramp_up_start(start)
    return start


def _save_ramp_up_start(start_date: str) -> None:
    """ramp_up 開始日を保存する"""
    config_path = SCHEDULER_DATA_DIR / "scheduler_config.json"
    SCHEDULER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    cfg = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    cfg["ramp_up_start"] = start_date
    cfg["updated_at"] = datetime.now().isoformat()

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def format_plan(plan: dict) -> str:
    """daily_plan を人間が読みやすい形式で返す"""
    targets = plan.get("targets", {})
    tasks = plan.get("tasks", [])

    lines = [
        f"[scheduler] Daily Plan: {plan['date']}",
        f"  day_type: {plan.get('day_type', '?')} (Day{plan.get('day_num', '?')})",
        f"  targets: post={targets.get('post', 0)} follow={targets.get('follow', 0)} like={targets.get('like', 0)}",
        f"  tasks: {len(tasks)}件",
    ]

    for t in tasks:
        enabled = "" if t.get("enabled", True) else " [SKIP]"
        lines.append(f"    {t['time']} {t['action']:6s} x{t['count']}{enabled}")

    return "\n".join(lines)


# ================================================================
# CLI テスト用
# ================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD)")
    parser.add_argument("--start", default=None, help="ramp_up開始日")
    parser.add_argument("--day-type", default=None, help="day_type強制指定")
    args = parser.parse_args()

    plan = generate_daily_plan(
        target_date=args.date,
        start_date=args.start,
        day_type=args.day_type,
    )
    print(format_plan(plan))
    print(f"\n保存先: {SCHEDULER_DATA_DIR / 'daily_plan.json'}")
