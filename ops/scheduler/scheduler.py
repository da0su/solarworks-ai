"""Solar Works スケジューラー v2.0 - 人間らしい揺らぎスケジュール

daily_plan.json に基づいて、3バッチの投稿を動的にスケジュール実行する。

使い方:
  python scheduler.py            # 本番モード
  python scheduler.py --test     # テストモード（即座に1バッチ試験実行）
  python scheduler.py --show     # 今日のスケジュールを表示して終了
  python scheduler.py --generate # 今日のスケジュールを生成して終了

停止:
  Ctrl+C

動作フロー:
  1. 起動時に daily_plan.json を読み込む（なければ当日分を生成）
  2. 30秒ごとにメインループ
     - 23:50 → 翌日分の daily_plan.json を生成 + run.py plan で投稿計画をSQLiteに登録
     - 各バッチ開始時刻 → run.py execute --limit N --min-wait X --max-wait Y を実行
  3. 実行済みバッチは daily_plan.json 内で completed にマーク（二重実行防止）
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# === 設定 ===
BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent.parent
LOGS_DIR = BASE_DIR / "logs"
ROOM_BOT_DIR = REPO_ROOT / "rakuten-room" / "bot"
DAILY_PLAN_PATH = ROOM_BOT_DIR / "data" / "daily_plan.json"

# room_bot コマンド
PYTHON = sys.executable

# subprocess 用の環境変数（cp932問題対策）
SUBPROCESS_ENV = os.environ.copy()
SUBPROCESS_ENV["PYTHONIOENCODING"] = "utf-8"


# === ログ設定 ===
def setup_logger():
    """scheduler 用ロガーを作成する"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "scheduler.log"

    logger = logging.getLogger("scheduler")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


logger = setup_logger()


def log(level, message):
    """ログファイル + PowerShell 両方に出力する"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{level}] {message}"
    print(line, flush=True)

    if level == "ERROR":
        logger.error(message)
    elif level == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


# === daily_plan.json 操作 ===
def load_daily_plan() -> dict | None:
    """daily_plan.json を読み込む"""
    if not DAILY_PLAN_PATH.exists():
        return None
    try:
        with open(DAILY_PLAN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("WARNING", f"daily_plan.json 読み込みエラー: {e}")
        return None


def save_daily_plan(plan: dict) -> None:
    """daily_plan.json を保存する"""
    DAILY_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DAILY_PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def mark_batch_status(plan: dict, batch_id: str, status: str,
                      result: dict = None) -> dict:
    """バッチのステータスを更新して保存する"""
    for batch in plan["post"]["batches"]:
        if batch["id"] == batch_id:
            batch["status"] = status
            if result:
                batch["result"] = result
            batch["updated_at"] = datetime.now().isoformat()
            break
    save_daily_plan(plan)
    return plan


# === スケジュール生成 ===
def generate_schedule(target_date: str = None) -> dict:
    """daily_schedule.py を呼び出してスケジュールを生成する"""
    cmd = [PYTHON, "-c", (
        "import sys; sys.path.insert(0, '.'); "
        "from planner.daily_schedule import generate_daily_schedule, format_schedule_report; "
        f"plan = generate_daily_schedule({repr(target_date)}); "
        "print(format_schedule_report(plan))"
    )]

    log("INFO", f"=== スケジュール生成: {target_date or '翌日'} ===")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOM_BOT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
        )
        if result.returncode == 0:
            log("INFO", "スケジュール生成成功")
            if result.stdout:
                for line in result.stdout.strip().splitlines():
                    log("INFO", f"  {line}")
        else:
            log("ERROR", f"スケジュール生成失敗 (exit: {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().splitlines()[-5:]:
                    log("ERROR", f"  {line}")
    except Exception as e:
        log("ERROR", f"スケジュール生成中にエラー: {e}")

    return load_daily_plan()


def generate_post_plan(target_date: str = None) -> bool:
    """run.py plan でSQLiteキューに投稿計画を登録する"""
    cmd = [PYTHON, "run.py", "plan"]
    if target_date:
        cmd.extend(["--date", target_date])

    log("INFO", f"=== 投稿計画生成: {target_date or '今日'} ===")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOM_BOT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
        )
        if result.returncode == 0:
            log("INFO", "投稿計画生成成功")
            if result.stdout:
                for line in result.stdout.strip().splitlines()[-10:]:
                    log("INFO", f"  {line}")
            return True
        else:
            log("ERROR", f"投稿計画生成失敗 (exit: {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().splitlines()[-5:]:
                    log("ERROR", f"  {line}")
            return False
    except Exception as e:
        log("ERROR", f"投稿計画生成中にエラー: {e}")
        return False


# === バッチ実行 ===
def execute_batch(batch: dict, target_date: str = None) -> dict:
    """1バッチ分の投稿を実行する

    run.py execute --limit <count> --min-wait <min> --max-wait <max> を呼び出す。
    """
    batch_id = batch["id"]
    count = batch["count"]
    interval_min = batch["interval_min"]
    interval_max = batch["interval_max"]

    cmd = [
        PYTHON, "run.py", "execute",
        "--limit", str(count),
        "--min-wait", str(interval_min),
        "--max-wait", str(interval_max),
    ]
    if target_date:
        cmd.extend(["--date", target_date])

    # タイムアウト: 件数 × 最大間隔 × 1.5 + 300秒（起動・ログイン確認分）
    timeout_sec = int(count * interval_max * 1.5) + 300
    timeout_sec = max(600, timeout_sec)  # 最低10分

    log("INFO", f"=== バッチ実行: {batch_id} ===")
    log("INFO", f"  件数: {count}, 間隔: {interval_min}-{interval_max}秒")
    log("INFO", f"  タイムアウト: {timeout_sec}秒 ({timeout_sec // 60}分)")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOM_BOT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
        )

        # 最後の行から結果を抽出
        posted = 0
        failed = 0
        skipped = 0

        if result.stdout:
            for line in result.stdout.strip().splitlines():
                if "成功:" in line or "成功" in line:
                    try:
                        posted = int(line.split("成功:")[1].strip().split("件")[0].strip())
                    except (IndexError, ValueError):
                        pass
                if "失敗:" in line or "失敗" in line:
                    try:
                        failed = int(line.split("失敗:")[1].strip().split("件")[0].strip())
                    except (IndexError, ValueError):
                        pass
                if "スキップ:" in line or "スキップ" in line:
                    try:
                        skipped = int(line.split("スキップ:")[1].strip().split("件")[0].strip())
                    except (IndexError, ValueError):
                        pass

        batch_result = {
            "exit_code": result.returncode,
            "posted": posted,
            "failed": failed,
            "skipped": skipped,
            "finished_at": datetime.now().isoformat(),
        }

        if result.returncode == 0:
            log("INFO", f"バッチ {batch_id} 完了: posted={posted} failed={failed} skipped={skipped}")
        else:
            log("WARNING", f"バッチ {batch_id} 終了 (exit: {result.returncode})")

        if result.stdout:
            for line in result.stdout.strip().splitlines()[-5:]:
                log("INFO", f"  stdout: {line}")
        if result.stderr and result.returncode != 0:
            for line in result.stderr.strip().splitlines()[-3:]:
                log("ERROR", f"  stderr: {line}")

        return batch_result

    except subprocess.TimeoutExpired:
        log("ERROR", f"バッチ {batch_id} タイムアウト ({timeout_sec}秒)")
        return {
            "exit_code": -1,
            "posted": 0, "failed": 0, "skipped": 0,
            "error": "timeout",
            "finished_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log("ERROR", f"バッチ {batch_id} 実行中にエラー: {e}")
        return {
            "exit_code": -1,
            "posted": 0, "failed": 0, "skipped": 0,
            "error": str(e),
            "finished_at": datetime.now().isoformat(),
        }


# === メインループ ===
def is_time_reached(target_time_str: str) -> bool:
    """現在時刻が target_time_str (HH:MM) を過ぎているか"""
    now = datetime.now()
    hour, minute = map(int, target_time_str.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= target


def main():
    parser = argparse.ArgumentParser(description="Solar Works スケジューラー v2.0")
    parser.add_argument("--test", action="store_true",
                        help="テストモード: 即座にnight バッチ相当を3件だけ試験実行")
    parser.add_argument("--show", action="store_true",
                        help="今日のスケジュールを表示して終了")
    parser.add_argument("--generate", action="store_true",
                        help="今日のスケジュールを生成して終了")
    args = parser.parse_args()

    mode_label = "TEST" if args.test else "PRODUCTION"

    log("INFO", "=" * 60)
    log("INFO", f"Solar Works スケジューラー v2.0 起動 [{mode_label}]")
    log("INFO", f"Python: {PYTHON}")
    log("INFO", f"room_bot: {ROOM_BOT_DIR}")
    log("INFO", "=" * 60)

    # --- --generate: スケジュール生成のみ ---
    if args.generate:
        today = datetime.now().strftime("%Y-%m-%d")
        plan = generate_schedule(today)
        if plan:
            log("INFO", "スケジュール生成完了")
        return

    # --- --show: スケジュール表示のみ ---
    if args.show:
        plan = load_daily_plan()
        if plan:
            sys.path.insert(0, str(ROOM_BOT_DIR))
            from planner.daily_schedule import format_schedule_report
            print(format_schedule_report(plan))
        else:
            print("daily_plan.json が見つかりません。--generate で生成してください。")
        return

    # --- --test: テスト実行 ---
    if args.test:
        today = datetime.now().strftime("%Y-%m-%d")
        log("INFO", "[TEST] テストモード: 少数件のテスト実行")

        # 投稿計画を生成（なければ）
        log("INFO", "[TEST] 投稿計画を確認中...")
        generate_post_plan(today)

        # テスト用の小バッチを作成
        test_batch = {
            "id": "test",
            "count": 3,
            "interval_min": 5,
            "interval_max": 10,
        }
        log("INFO", "[TEST] テストバッチ実行: 3件, 間隔5-10秒")
        result = execute_batch(test_batch, today)
        log("INFO", f"[TEST] テスト完了: {result}")
        log("INFO", "[TEST] 本番スケジュールには影響しません。")
        return

    # === 本番モード: メインループ ===
    today = datetime.now().strftime("%Y-%m-%d")
    plan_generated_for = set()  # スケジュール生成済みの日付
    plan_registered_for = set()  # 投稿計画(SQLite)登録済みの日付

    # 起動時に今日のプランを確認
    plan = load_daily_plan()
    if plan and plan.get("date") == today:
        log("INFO", f"今日のスケジュールあり: {today}")
        for batch in plan["post"]["batches"]:
            log("INFO", f"  {batch['id']}: {batch['start']} ({batch['count']}件) [{batch['status']}]")
    else:
        log("INFO", f"今日のスケジュールなし → 生成します")
        plan = generate_schedule(today)
        if plan:
            plan_generated_for.add(today)
            # 投稿計画もSQLiteに登録
            generate_post_plan(today)
            plan_registered_for.add(today)

    log("INFO", "メインループ開始 (30秒間隔)...")
    log("INFO", "Ctrl+C で停止")

    try:
        while True:
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            # --- 日付変更検知 ---
            if current_date != today:
                log("INFO", f"日付変更: {today} -> {current_date}")
                today = current_date
                plan = load_daily_plan()

            # --- 23:50: 翌日のスケジュール生成 ---
            if current_time >= "23:50":
                tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                if tomorrow not in plan_generated_for:
                    log("INFO", f"翌日スケジュール生成: {tomorrow}")
                    generate_schedule(tomorrow)
                    plan_generated_for.add(tomorrow)
                    # 翌日の投稿計画もSQLiteに登録
                    generate_post_plan(tomorrow)
                    plan_registered_for.add(tomorrow)

            # --- 当日プランの投稿計画がまだなら登録 ---
            if today not in plan_registered_for:
                plan = load_daily_plan()
                if plan and plan.get("date") == today:
                    generate_post_plan(today)
                    plan_registered_for.add(today)

            # --- バッチ実行チェック ---
            plan = load_daily_plan()
            if plan and plan.get("date") == today:
                for batch in plan["post"]["batches"]:
                    if batch["status"] != "pending":
                        continue

                    # 開始時刻を過ぎているか
                    if is_time_reached(batch["start"]):
                        log("INFO", f"バッチ開始時刻到達: {batch['id']} ({batch['start']})")

                        # running に更新
                        plan = mark_batch_status(plan, batch["id"], "running")

                        # 実行
                        result = execute_batch(batch, today)

                        # 結果を記録
                        status = "completed" if result.get("exit_code", -1) == 0 else "failed"
                        plan = mark_batch_status(plan, batch["id"], status, result)

                        log("INFO", f"バッチ {batch['id']} -> {status}")

            # 30秒待機
            time.sleep(30)

    except KeyboardInterrupt:
        log("INFO", "スケジューラー停止 (Ctrl+C)")
        sys.exit(0)


if __name__ == "__main__":
    main()
