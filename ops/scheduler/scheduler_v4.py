"""Solar Works スケジューラー v4.0 — ランキング起点設計

v3→v4変更点:
  - 投稿: 楽天API検索 → ランキングTOP50×5ジャンル（250件固定・5日入替）
  - フォロー: 候補DB → ランキング1位ユーザーのフォロワー直接クリック
  - 件数: 投稿50/フォロー500/ライク500
  - フォロー1回60件MAX（楽天上限70件に余裕）
  - 「ご利用上限数に達しています」検知→即停止
  - 5日サイクルでプール入替

使い方:
  python scheduler_v4.py
  python scheduler_v4.py --show
"""

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent.parent
LOGS_DIR = BASE_DIR / "logs"
ROOM_BOT_DIR = REPO_ROOT / "rakuten-room" / "bot"
DAILY_PLAN_PATH = ROOM_BOT_DIR / "data" / "daily_plan.json"
POOL_META = ROOM_BOT_DIR / "data" / "pool_meta.json"
CREDENTIALS_PATH = REPO_ROOT / "credentials" / "sheets_service_account.json"

PYTHON = sys.executable
SUBPROCESS_ENV = os.environ.copy()
SUBPROCESS_ENV["PYTHONIOENCODING"] = "utf-8"

# === v4設定 ===
# 投稿: 8回（0/8/10/12/14/16/20/22時）
POST_HOURS = [0, 8, 10, 12, 14, 16, 20, 22]
# フォロー: 16回（8〜23時 毎時）
FOLLOW_HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
# ライク: 8回（投稿と同タイミング）
LIKE_HOURS = [0, 8, 10, 12, 14, 16, 20, 22]
# パトロール（投稿/ライクの合間）
PATROL_HOURS = [9, 11, 13, 15, 17, 21]

POST_DAILY = 50
FOLLOW_DAILY = 1440
LIKE_DAILY = 500
FOLLOW_PER_SESSION = 90  # 楽天上限100に余裕

POOL_CYCLE_DAYS = 5

SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
SHEET_NAME = "楽天ROOM_デイリーログ"

LOOP_INTERVAL = 30

GENRES = [
    "キッズ・ベビー・マタニティ",
    "日用品雑貨・文房具・手芸",
    "スイーツ・お菓子",
    "バッグ・小物・ブランド雑貨",
    "キッチン用品・食器・調理器具",
]


# === ログ ===
def setup_logger():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("scheduler_v4")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(LOGS_DIR / "scheduler.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    return logger

logger = setup_logger()

def log(level, msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [{level}] {msg}")
    getattr(logger, level.lower(), logger.info)(msg)


# === daily_plan ===
def load_daily_plan():
    if not DAILY_PLAN_PATH.exists(): return None
    try: return json.loads(DAILY_PLAN_PATH.read_text(encoding="utf-8"))
    except: return None

def save_daily_plan(plan):
    tmp = DAILY_PLAN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DAILY_PLAN_PATH)


def generate_plan_v4(target_date: str, post_goal=POST_DAILY, follow_goal=FOLLOW_DAILY, like_goal=LIKE_DAILY):
    def distribute(total, n):
        base = total // n
        rem = total % n
        return [base + (1 if i < rem else 0) for i in range(n)]

    n_post = len(POST_HOURS)
    n_follow = len(FOLLOW_HOURS)
    n_like = len(LIKE_HOURS)

    post_counts = distribute(post_goal, n_post)
    follow_counts = [min(FOLLOW_PER_SESSION, follow_goal // n_follow + 1)] * n_follow
    like_counts = distribute(like_goal, n_like)

    # 全時間帯をマージ
    all_hours = sorted(set(POST_HOURS + FOLLOW_HOURS + LIKE_HOURS))
    batches = []
    post_idx = 0
    follow_idx = 0
    like_idx = 0

    for hour in all_hours:
        minute = random.randint(0, 25)
        pc = post_counts[post_idx] if hour in POST_HOURS else 0
        fc = follow_counts[follow_idx] if hour in FOLLOW_HOURS else 0
        lc = like_counts[like_idx] if hour in LIKE_HOURS else 0

        if hour in POST_HOURS: post_idx += 1
        if hour in FOLLOW_HOURS: follow_idx += 1
        if hour in LIKE_HOURS: like_idx += 1

        batches.append({
            "id": f"exec_{hour:02d}",
            "start": f"{hour:02d}:{minute:02d}",
            "post_count": pc,
            "follow_count": fc,
            "like_count": lc,
            "genre_index": (follow_idx - 1) % len(GENRES) if fc > 0 else 0,
            "status": "pending",
        })

    plan = {
        "version": "4.0",
        "date": target_date,
        "generated_at": datetime.now().isoformat(),
        "goals": {"post": post_goal, "follow": follow_goal, "like": like_goal},
        "execution_batches": batches,
        "patrol_hours": PATROL_HOURS,
    }
    save_daily_plan(plan)
    log("INFO", f"v4計画: {target_date} post={post_goal} follow={follow_goal} like={like_goal}")
    for b in batches:
        if b["post_count"] or b["follow_count"] or b["like_count"]:
            log("INFO", f"  {b['id']} {b['start']} post={b['post_count']} follow={b['follow_count']} like={b['like_count']}")
    return plan


# === 実行 ===
def run_post(count, target_date=None):
    cmd = [PYTHON, "run.py", "execute", "--limit", str(count), "--min-wait", "8", "--max-wait", "15"]
    if target_date: cmd.extend(["--date", target_date])
    try:
        r = subprocess.run(cmd, cwd=str(ROOM_BOT_DIR), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=max(600, count*15*2), env=SUBPROCESS_ENV)
        posted = 0
        for line in (r.stdout or "").splitlines():
            if "成功:" in line:
                try: posted = int(line.split("成功:")[1].strip().split("件")[0])
                except: pass
        return {"posted": posted, "exit_code": r.returncode}
    except Exception as e:
        return {"posted": 0, "exit_code": -1, "error": str(e)}


def run_follow_direct(count, genre_index=0):
    cmd = [PYTHON, str(ROOM_BOT_DIR / "executor" / "follow_direct.py"),
           "--limit", str(min(count, FOLLOW_PER_SESSION)),
           "--genre-index", str(genre_index)]
    try:
        r = subprocess.run(cmd, cwd=str(ROOM_BOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, env=SUBPROCESS_ENV)
        followed = 0
        for line in (r.stdout or "").splitlines():
            if "成功:" in line:
                import re
                m = re.search(r'成功:\s*(\d+)', line)
                if m: followed = int(m.group(1))
        return {"followed": followed, "exit_code": r.returncode}
    except Exception as e:
        return {"followed": 0, "exit_code": -1, "error": str(e)}


def run_like(count):
    cmd = [PYTHON, "run.py", "like", "--limit", str(count)]
    try:
        r = subprocess.run(cmd, cwd=str(ROOM_BOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, env=SUBPROCESS_ENV)
        liked = 0
        for line in (r.stdout or "").splitlines():
            if "成功:" in line:
                import re
                m = re.search(r'成功:\s*(\d+)', line)
                if m: liked = int(m.group(1))
        return {"liked": liked, "exit_code": r.returncode}
    except Exception as e:
        return {"liked": 0, "exit_code": -1, "error": str(e)}


# === プール管理 ===
def check_pool_refresh():
    """5日経過→ランキング再取得"""
    if not POOL_META.exists():
        log("INFO", "プールメタなし → ランキング取得")
        _refresh_pool()
        return
    meta = json.loads(POOL_META.read_text(encoding="utf-8"))
    created = meta.get("created_at", "")
    if not created:
        _refresh_pool()
        return
    created_dt = datetime.fromisoformat(created)
    days = (datetime.now() - created_dt).days
    if days >= POOL_CYCLE_DAYS:
        log("INFO", f"プール{days}日経過 → ランキング再取得")
        _refresh_pool()
    else:
        log("INFO", f"プール{days}日目（{POOL_CYCLE_DAYS}日サイクル）")

def _refresh_pool():
    cmd = [PYTHON, str(ROOM_BOT_DIR / "executor" / "ranking_scraper.py"), "--force"]
    try:
        subprocess.run(cmd, cwd=str(ROOM_BOT_DIR), timeout=1800, env=SUBPROCESS_ENV,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        log("INFO", "ランキングプール更新完了")
    except Exception as e:
        log("ERROR", f"ランキング取得エラー: {e}")


# === Chrome lock待機 ===
def _kill_bot_chrome():
    """BOT用Chromeプロセスを強制停止"""
    import subprocess as sp
    try:
        sp.run('taskkill /IM chrome.exe /F', shell=True, capture_output=True, timeout=10)
        time.sleep(3)
    except:
        pass

def _wait_chrome_free(max_wait=60):
    import glob
    lock = str(ROOM_BOT_DIR / "data" / "chrome_profile" / "SingletonLock")
    for i in range(max_wait // 5):
        if not glob.glob(lock): return True
        log("INFO", f"  Chrome lock待機... {i*5}秒")
        time.sleep(5)
    # 待っても空かなければ強制停止
    _kill_bot_chrome()
    return True


# === バッチ実行 ===
def execute_batch(batch, target_date):
    batch_id = batch["id"]
    log("INFO", f"=== 実行: {batch_id} ({batch['start']}) ===")

    # 投稿
    if batch["post_count"] > 0:
        _wait_chrome_free()
        post_r = run_post(batch["post_count"], target_date)
        log("INFO", f"  投稿: {post_r.get('posted',0)}/{batch['post_count']}")
        _kill_bot_chrome()  # Chrome完全停止
    else:
        post_r = {"posted": 0}

    # フォロー（直接クリック方式）
    if batch["follow_count"] > 0:
        _wait_chrome_free()
        follow_r = run_follow_direct(batch["follow_count"], batch.get("genre_index", 0))
        log("INFO", f"  フォロー: {follow_r.get('followed',0)}/{batch['follow_count']}")
        _kill_bot_chrome()  # Chrome完全停止
    else:
        follow_r = {"followed": 0}

    # ライク
    if batch["like_count"] > 0:
        _wait_chrome_free()
        like_r = run_like(batch["like_count"])
        log("INFO", f"  ライク: {like_r.get('liked',0)}/{batch['like_count']}")
        _kill_bot_chrome()  # Chrome完全停止
    else:
        like_r = {"liked": 0}

    batch["status"] = "completed"
    batch["result"] = {"post": post_r, "follow": follow_r, "like": like_r,
                       "finished_at": datetime.now().isoformat()}
    return batch


# === スプシ連携（v3から引継ぎ） ===
def write_daily_log_to_sheet(target_date):
    if not CREDENTIALS_PATH.exists(): return
    try:
        import gspread, sqlite3
        gc = gspread.service_account(filename=str(CREDENTIALS_PATH))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet(SHEET_NAME)

        # 実績取得
        import sqlite3
        db = ROOM_BOT_DIR / "data" / "room_bot.db"
        posted = 0
        if db.exists():
            conn = sqlite3.connect(str(db))
            row = conn.execute("SELECT posted FROM daily_summary WHERE summary_date=?", (target_date,)).fetchone()
            if row: posted = row[0]
            conn.close()

        fh_path = ROOM_BOT_DIR / "data" / "follow_history.json"
        followed = 0
        if fh_path.exists():
            fh = json.loads(fh_path.read_text(encoding="utf-8"))
            followed = sum(1 for e in fh if e.get("followed_at","")[:10] == target_date)

        lh_path = ROOM_BOT_DIR / "data" / "like_history.json"
        liked = 0
        if lh_path.exists():
            lh = json.loads(lh_path.read_text(encoding="utf-8"))
            liked = sum(1 for e in lh if e.get("liked_at","")[:10] == target_date)

        col_a = ws.col_values(1)
        target_slash = target_date.replace("-", "/")
        row = None
        for i, val in enumerate(col_a):
            if target_slash in str(val): row = i + 1; break
        if row:
            ws.update_cell(row, 3, posted)
            ws.update_cell(row, 6, followed)
            ws.update_cell(row, 9, liked)
            log("INFO", f"[SHEET] {target_date} post={posted} follow={followed} like={liked}")
    except Exception as e:
        log("ERROR", f"[SHEET] {e}")


def read_goals_from_sheet(target_date):
    if not CREDENTIALS_PATH.exists(): return None
    try:
        import gspread
        gc = gspread.service_account(filename=str(CREDENTIALS_PATH))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet(SHEET_NAME)
        col_a = ws.col_values(1)
        target_slash = target_date.replace("-", "/")
        row = None
        for i, val in enumerate(col_a):
            if target_slash in str(val): row = i + 1; break
        if not row: return None
        return {
            "post": int(ws.cell(row, 2).value or POST_DAILY),
            "follow": int(ws.cell(row, 5).value or FOLLOW_DAILY),
            "like": int(ws.cell(row, 8).value or LIKE_DAILY),
        }
    except: return None


# === パトロール ===
def run_patrol(plan, target_date):
    import sqlite3
    goals = plan.get("goals", {})
    # 実績取得
    db = ROOM_BOT_DIR / "data" / "room_bot.db"
    posted = 0
    if db.exists():
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT posted FROM daily_summary WHERE summary_date=?", (target_date,)).fetchone()
        if row: posted = row[0]
        conn.close()
    fh = json.loads((ROOM_BOT_DIR / "data" / "follow_history.json").read_text(encoding="utf-8")) if (ROOM_BOT_DIR / "data" / "follow_history.json").exists() else []
    followed = sum(1 for e in fh if e.get("followed_at","")[:10] == target_date)
    lh = json.loads((ROOM_BOT_DIR / "data" / "like_history.json").read_text(encoding="utf-8")) if (ROOM_BOT_DIR / "data" / "like_history.json").exists() else []
    liked = sum(1 for e in lh if e.get("liked_at","")[:10] == target_date)

    ratio = min(1.0, datetime.now().hour / 22.0)
    log("INFO", f"[PATROL] post={posted}/{int(goals.get('post',0)*ratio)} follow={followed}/{int(goals.get('follow',0)*ratio)} like={liked}/{int(goals.get('like',0)*ratio)}")


# === メイン ===
def main():
    parser = argparse.ArgumentParser(description="Solar Works Scheduler v4.0")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    log("INFO", "=" * 60)
    log("INFO", "Solar Works Scheduler v4.0 起動")
    log("INFO", f"  投稿{POST_DAILY}/フォロー{FOLLOW_DAILY}/ライク{LIKE_DAILY}")
    log("INFO", f"  フォロー1回{FOLLOW_PER_SESSION}件MAX / 5日プール入替")
    log("INFO", "=" * 60)

    if args.show:
        plan = load_daily_plan()
        if plan: print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    today = datetime.now().strftime("%Y-%m-%d")
    executed = set()
    patrolled = set()
    sheet_written = False
    plan_generated = False

    # 起動時プラン確認
    plan = load_daily_plan()
    if not plan or plan.get("date") != today or plan.get("version") != "4.0":
        plan = generate_plan_v4(today)
    else:
        for b in plan.get("execution_batches", []):
            if b["status"] == "completed":
                executed.add(int(b["start"].split(":")[0]))

    # プール確認
    check_pool_refresh()

    # 投稿計画
    try:
        subprocess.run([PYTHON, "run.py", "plan"], cwd=str(ROOM_BOT_DIR), timeout=120,
                       capture_output=True, env=SUBPROCESS_ENV)
    except: pass

    log("INFO", f"メインループ開始 ({LOOP_INTERVAL}秒間隔)")

    try:
        while True:
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_hour = now.hour
            current_minute = now.minute

            if current_date != today:
                log("INFO", f"日付変更: {today} → {current_date}")
                today = current_date
                executed.clear(); patrolled.clear()
                sheet_written = False; plan_generated = False
                plan = generate_plan_v4(today)
                check_pool_refresh()
                try:
                    subprocess.run([PYTHON, "run.py", "plan"], cwd=str(ROOM_BOT_DIR), timeout=120,
                                   capture_output=True, env=SUBPROCESS_ENV)
                except: pass

            # 実行回（投稿/フォロー/ライクいずれかの時間帯）
            all_exec_hours = sorted(set(POST_HOURS + FOLLOW_HOURS + LIKE_HOURS))
            if current_hour in all_exec_hours and current_hour not in executed:
                if plan:
                    for batch in plan.get("execution_batches", []):
                        bh = int(batch["start"].split(":")[0])
                        bm = int(batch["start"].split(":")[1])
                        if bh == current_hour and current_minute >= bm and batch["status"] == "pending":
                            execute_batch(batch, today)
                            save_daily_plan(plan)
                            executed.add(current_hour)
                            break

            # パトロール
            if current_hour in PATROL_HOURS and current_hour not in patrolled:
                if plan: run_patrol(plan, today)
                patrolled.add(current_hour)

            # 23:30 スプシ
            if current_hour == 23 and current_minute >= 30 and not sheet_written:
                write_daily_log_to_sheet(today)
                sheet_written = True

            # 23:50 翌日計画
            if current_hour == 23 and current_minute >= 50 and not plan_generated:
                tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                goals = read_goals_from_sheet(tomorrow)
                if goals:
                    generate_plan_v4(tomorrow, goals["post"], goals["follow"], goals["like"])
                else:
                    generate_plan_v4(tomorrow)
                plan_generated = True

            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        log("INFO", "Scheduler v4.0 停止")
        sys.exit(0)


if __name__ == "__main__":
    main()
