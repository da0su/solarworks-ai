#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patrol_hourly.py + スプシ自動同期ラッパー
2026-04-26 CEO承認: patrol毎にスプシ row を必ず更新するルール実装

実行手順:
  1. patrol_hourly.py を呼び出し（観測+問題検知）
  2. canonical_counts.md準拠で当日の正本値を取得
     - POST: post_queue (status='posted', DATE(posted_at)=today)
     - FOLLOW: follow_rpa_log.json (timestamp prefix=today / success合計)
     - LIKE: like_history.json (liked_at prefix=today)
     - FB: follow_log (action='followback', DATE(followed_at)=today)
  3. スプシ「新ROOM_デイリーログ」(gid=1447646534) の当日 row を batch_update
     - 列: C=POST F=FOLLOW I=LIKE P=FB N=備考(タイムスタンプ+状況)
  4. patrol_log.txt に sheet_synced=YES を追記

Cron 用: python ops/patrol_with_sheet_sync.py
"""
from __future__ import annotations
import io
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
PATROL_PY = REPO_ROOT / "ops" / "patrol_hourly.py"
PATROL_LOG = REPO_ROOT / "ops" / "patrol_log.txt"
ROOM_BOT_DB = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot.db"
ROOM_BOT_V5_DB = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"
FOLLOW_RPA_LOG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
FOLLOW_HOST_LOG = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_host_log.json"  # follow_host_runner.py 用
LIKE_HISTORY = REPO_ROOT / "rakuten-room" / "bot" / "data" / "like_history.json"
CRED = REPO_ROOT / "credentials" / "sheets_service_account.json"

SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
SHEET_GID = 1447646534
LIMIT_ANALYSIS_GID = 1121983993  # 05_上限分析 (RL event リアルタイム記録先)
DAILY_TARGETS_CACHE = REPO_ROOT / "ops" / "scheduler" / "daily_targets.json"

# 情報収集フェーズ: RL検知済状態ファイル (repeat write 防止 + 回復時間バックフィル用)
RL_EVENT_STATE = REPO_ROOT / "ops" / "rl_event_state.json"

# cooldown定数: config.FOLLOW_RL_COOLDOWN_MIN から読み込む (実データ確定値=69min)
try:
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "rakuten-room" / "bot"))
    import config as _roombot_cfg
    FOLLOW_RL_COOLDOWN_MIN = _roombot_cfg.FOLLOW_RL_COOLDOWN_MIN
except Exception:
    FOLLOW_RL_COOLDOWN_MIN = 69  # fallback

# 行マッピング: シート上の A列日付を実際に検索して行番号を特定（5月以降は空行があるため計算式は使わない）
def date_to_row(target_date: date, ws=None) -> int:
    """シートのA列を検索して対象日の行番号を返す。ws が None の場合は近似計算を使用。"""
    if ws is not None:
        try:
            col_a = ws.col_values(1)
            target_str = target_date.strftime("%Y/%m/%d")
            target_str2 = target_date.isoformat()
            for i, val in enumerate(col_a):
                if target_str in str(val) or target_str2 in str(val):
                    return i + 1
        except Exception:
            pass
    # フォールバック: 近似計算（4/10=row12基準。月境界に空行がある場合はずれる可能性あり）
    delta = (target_date - date(2026, 4, 10)).days
    return 12 + delta


def get_post_today(target_date: date) -> int:
    if not ROOM_BOT_DB.exists():
        return 0
    try:
        c = sqlite3.connect(str(ROOM_BOT_DB))
        row = c.execute(
            "SELECT COUNT(*) FROM post_queue WHERE status='posted' AND DATE(posted_at)=?",
            (target_date.isoformat(),)
        ).fetchone()
        c.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"WARN post: {e}")
        return 0


def get_follow_today(target_date: date) -> int:
    """VM log (follow_rpa_log.json) + Host log (follow_host_log.json) の合算。
    VM bot は \\VBOXSVR\\share へ replace-write するため別ファイルで保護。"""
    prefix = target_date.isoformat()
    total = 0
    for log_path in [FOLLOW_RPA_LOG, FOLLOW_HOST_LOG]:
        if not log_path.exists():
            continue
        try:
            logs = json.loads(log_path.read_text(encoding="utf-8"))
            total += sum(e.get("success", 0) for e in logs if e.get("timestamp", "").startswith(prefix))
        except Exception as e:
            print(f"WARN follow ({log_path.name}): {e}")
    return total


def get_like_today(target_date: date) -> int:
    if not LIKE_HISTORY.exists():
        return 0
    try:
        data = json.loads(LIKE_HISTORY.read_text(encoding="utf-8"))
        prefix = target_date.isoformat()
        return sum(1 for e in data if e.get("liked_at", "").startswith(prefix))
    except Exception as e:
        print(f"WARN like: {e}")
        return 0


def get_fb_today(target_date: date) -> tuple[int, int]:
    """returns (today_count, pending_count)"""
    if not ROOM_BOT_V5_DB.exists():
        return 0, 0
    try:
        c = sqlite3.connect(str(ROOM_BOT_V5_DB))
        today = c.execute(
            "SELECT COUNT(*) FROM follow_log WHERE action='followback' AND DATE(followed_at)=?",
            (target_date.isoformat(),)
        ).fetchone()[0]
        pending = c.execute(
            "SELECT COUNT(*) FROM followback_queue WHERE status='pending'"
        ).fetchone()[0]
        c.close()
        return today, pending
    except Exception as e:
        print(f"WARN fb: {e}")
        return 0, 0


# 2026-05-05 礎: cmd window flash 抑制
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def run_patrol() -> tuple[bool, str]:
    """patrol_hourly.py を実行。(any_problem, captured_output) を返す。"""
    try:
        r = subprocess.run(
            ["python", str(PATROL_PY)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, creationflags=_NO_WINDOW,
        )
        out = (r.stdout or "") + (r.stderr or "")
        any_problem = "any_problem=True" in out
        return any_problem, out
    except Exception as e:
        return True, f"PATROL_FAIL: {e}"


def recent_rate_limit_check(scan_count: int = 12) -> tuple[bool, float | None]:
    """直近 scan_count エントリで rate_limit_detected を探す。
    (is_in_cooldown, age_min) を返す。
    age < FOLLOW_RL_COOLDOWN_MIN(=69min) なら新規 launcher 発火を絶対回避。
    """
    try:
        if not FOLLOW_RPA_LOG.exists():
            return False, None
        logs = json.loads(FOLLOW_RPA_LOG.read_text(encoding="utf-8"))
        if not logs:
            return False, None
        for entry in reversed(logs[-scan_count:]):
            if entry.get("stop_reason") == "rate_limit_detected":
                ts = datetime.fromisoformat(entry.get("timestamp", "").split(".")[0])
                age_min = (datetime.now() - ts).total_seconds() / 60
                if age_min < FOLLOW_RL_COOLDOWN_MIN:
                    return True, age_min
                return False, age_min
        return False, None
    except Exception as e:
        print(f"WARN rate_limit_check: {e}")
        return False, None


# ---------------------------------------------------------------------------
# 情報収集フェーズ計装ヘルパー
# ---------------------------------------------------------------------------

def rolling_sum(logs: list, before_dt: datetime, window_hours: float) -> int:
    """before_dt 直前 window_hours 時間の success 合計を計算"""
    from datetime import timedelta
    cutoff = before_dt - timedelta(hours=window_hours)
    total = 0
    for e in logs:
        try:
            ts = datetime.fromisoformat(e.get("timestamp", "").split(".")[0])
            if cutoff <= ts < before_dt:
                total += e.get("success", 0)
        except Exception:
            pass
    return total


def load_rl_state() -> dict | None:
    """RL検知状態ファイルを読む。{rl_ts, ts_fmt, recovery_written}"""
    if not RL_EVENT_STATE.exists():
        return None
    try:
        return json.loads(RL_EVENT_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_rl_state(state: dict) -> None:
    try:
        RL_EVENT_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"WARN save_rl_state: {e}")


def append_rl_event_to_sheet(rl_entry: dict, logs: list) -> None:
    """RL検知時に05_上限分析へリアルタイム行追記 (情報収集フェーズ計装)
    列構成: A=記録日時 B=日付 C=機能 D=区分 E=上限超過名 F=備考
            G=URL H=session_success I=24h累積 J=stop_reason
            K=1h rolling L=6h rolling M=24h rolling N=時刻帯(0-23)
            O=recovery_time_min(後で埋める) P=ログ元 Q=スクショ R=信頼度 S=担当 T=
    """
    try:
        import gspread
    except ImportError:
        return
    if not CRED.exists():
        return
    try:
        rl_ts_str = rl_entry.get("timestamp", "")
        rl_ts = datetime.fromisoformat(rl_ts_str.split(".")[0])
        r1h  = rolling_sum(logs, rl_ts, 1)
        r6h  = rolling_sum(logs, rl_ts, 6)
        r24h = rolling_sum(logs, rl_ts, 24)
        ts_fmt   = rl_ts.strftime("%Y-%m-%d %H:%M")
        date_fmt = rl_ts.strftime("%Y-%m-%d")
        session_ok = rl_entry.get("success", 0)
        note = (
            f"リアルタイム検知 session={session_ok}件 "
            f"prev1h={r1h} prev6h={r6h} prev24h={r24h} hour={rl_ts.hour}"
        )
        row = [
            ts_fmt,                  # A 記録日時
            date_fmt,                # B 日付
            "FOLLOW",                # C 機能
            "VM専用機",              # D 区分
            "rate_limit_detected",   # E 上限超過名
            note,                    # F 備考
            "https://room.rakuten.co.jp/my/followers",  # G URL
            str(session_ok),         # H セッション成功件数
            str(r24h),               # I 24h累積
            "rate_limit_detected",   # J stop_reason
            str(r1h),                # K 1h rolling
            str(r6h),                # L 6h rolling
            str(r24h),               # M 24h rolling (I列と同値)
            str(rl_ts.hour),         # N 時刻帯(0-23)
            "",                      # O recovery_time_min (後で埋める)
            "patrol_realtime",       # P ログ元
            "",                      # Q スクショ
            "実測",                  # R 信頼度
            "サイバー",              # S 担当
            "",                      # T
        ]
        gc = gspread.service_account(filename=str(CRED))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.get_worksheet_by_id(LIMIT_ANALYSIS_GID)
        ws.append_rows([row], value_input_option="USER_ENTERED")
        print(f"[RL_EVENT→SHEET] {ts_fmt} 24h={r24h} 6h={r6h} 1h={r1h} session={session_ok}")
        save_rl_state({"rl_ts": rl_ts_str, "ts_fmt": ts_fmt, "recovery_written": False})
    except Exception as e:
        print(f"WARN append_rl_event_to_sheet: {e}")


def try_backfill_recovery_time(logs: list) -> None:
    """直前RL後に成功runが来たら、05_上限分析のO列(recovery_time_min)を遡り更新"""
    state = load_rl_state()
    if not state or state.get("recovery_written"):
        return
    rl_ts_str = state.get("rl_ts", "")
    if not rl_ts_str:
        return
    try:
        rl_ts = datetime.fromisoformat(rl_ts_str.split(".")[0])
        success_after = [
            e for e in logs
            if (datetime.fromisoformat(e.get("timestamp", "").split(".")[0]) > rl_ts
                and e.get("success", 0) > 0)
        ]
        if not success_after:
            return  # まだ回復していない
        first_ok = min(success_after, key=lambda e: e.get("timestamp", ""))
        rec_ts  = datetime.fromisoformat(first_ok["timestamp"].split(".")[0])
        rec_min = (rec_ts - rl_ts).total_seconds() / 60
        print(f"[RL_RECOVERY] RL={rl_ts_str[:16]} → 回復={first_ok['timestamp'][:16]} ({rec_min:.1f}min)")
        # Sheet O列 (列番号15) を更新
        try:
            import gspread
            gc = gspread.service_account(filename=str(CRED))
            sh = gc.open_by_key(SPREADSHEET_ID)
            ws = sh.get_worksheet_by_id(LIMIT_ANALYSIS_GID)
            search_key = state.get("ts_fmt", rl_ts.strftime("%Y-%m-%d %H:%M"))
            col_a = ws.col_values(1)
            for i, val in enumerate(col_a):
                if search_key in str(val):
                    row_num = i + 1
                    ws.update_cell(row_num, 15, f"{rec_min:.1f}")  # O列=15
                    print(f"[RL_RECOVERY→SHEET] row={row_num} O={rec_min:.1f}min")
                    break
        except Exception as e2:
            print(f"WARN backfill_sheet: {e2}")
        state["recovery_written"] = True
        save_rl_state(state)
    except Exception as e:
        print(f"WARN try_backfill_recovery_time: {e}")


def _check_tasks_state(task_names: list) -> str:
    """Task Scheduler 状態確認 (ACTIVE=Enabled true / STOPPED=Enabled false)
    schtasks /xml はバイトASCIIで出力 (XML内の encoding=UTF-16 宣言は誤り)"""
    import subprocess
    states = []
    for tn in task_names:
        try:
            r = subprocess.run(["schtasks", "/query", "/tn", tn, "/xml"], creationflags=_NO_WINDOW,
                              capture_output=True, timeout=10)
            if r.returncode != 0:
                states.append("MISSING")
                continue
            data = r.stdout
            if b"<Enabled>false</Enabled>" in data:
                states.append("STOPPED")
            elif b"<Enabled>true</Enabled>" in data:
                states.append("ACTIVE")
            else:
                # tag省略時は default true (=ACTIVE)
                states.append("ACTIVE")
        except Exception:
            states.append("UNKNOWN")
    if all(s == "STOPPED" for s in states):
        return "STOPPED"
    if any(s == "ACTIVE" for s in states):
        return "ACTIVE"
    return "MIX"


def _disable_tasks(task_names: list) -> None:
    """Task Scheduler 無効化"""
    import subprocess
    for tn in task_names:
        try:
            subprocess.run(["schtasks", "/end", "/tn", tn], capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
            subprocess.run(["schtasks", "/change", "/tn", tn, "/disable"], capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        except Exception:
            pass


def _enable_tasks(task_names: list) -> None:
    """Task Scheduler 有効化 (毎朝の reset 用)"""
    import subprocess
    for tn in task_names:
        try:
            subprocess.run(["schtasks", "/change", "/tn", tn, "/enable"], capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        except Exception:
            pass


def read_targets_from_sheet(target_date: date) -> dict | None:
    """スプシ K列(FB目標), B(POST), E(FOLLOW), H(LIKE) から本日目標を取得 (CTO指示 K-M列正本)"""
    try:
        import gspread
    except ImportError:
        return None
    if not CRED.exists():
        return None
    try:
        gc = gspread.service_account(filename=str(CRED))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.get_worksheet_by_id(SHEET_GID)
        row = date_to_row(target_date, ws)
        cells = ws.range(f"B{row}:K{row}")
        # B=col2, E=col5, H=col8, K=col11 → in B:K range positions: B=0, E=3, H=6, K=9
        def _to_int(v):
            try:
                return int(str(v).replace(",", "").strip()) if v else 0
            except Exception:
                return 0
        return {
            "post":   _to_int(cells[0].value),  # B
            "follow": _to_int(cells[3].value),  # E
            "like":   _to_int(cells[6].value),  # H
            "fb":     _to_int(cells[9].value),  # K
        }
    except Exception as e:
        print(f"WARN read_targets: {e}")
        return None


def update_sheet(target_date: date, post: int, follow: int, like: int, fb: int, fb_pending: int, problem: bool, patrol_summary: str) -> bool:
    try:
        import gspread
    except ImportError:
        print("ERROR: gspread not installed")
        return False
    if not CRED.exists():
        print(f"ERROR: credentials not found: {CRED}")
        return False
    try:
        gc = gspread.service_account(filename=str(CRED))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.get_worksheet_by_id(SHEET_GID)
        row = date_to_row(target_date, ws)
        ts = datetime.now().strftime("%H:%M")
        status_label = "PROBLEM" if problem else "OK"
        rate_limit_tag = ""
        try:
            in_cd, rl_age = recent_rate_limit_check()
            if in_cd:
                rate_limit_tag = f" [RATE_LIMIT cooldown {rl_age:.0f}min前検知]"
        except Exception:
            pass
        # 既存 N列の備考を残しつつタイムスタンプ更新（短い1行サマリ）
        note = f"[{ts} patrol={status_label}{rate_limit_tag}] POST={post}/200 FOLLOW={follow}/2095 LIKE={like}/383 FB={fb} pending={fb_pending} | {patrol_summary[:100]}"
        ranges = [
            {"range": f"C{row}", "values": [[post]]},
            {"range": f"F{row}", "values": [[follow]]},
            {"range": f"I{row}", "values": [[like]]},
            {"range": f"L{row}", "values": [[fb]]},   # L=実績フォローバック数 (修正: 旧P→L)
        ]
        ws.batch_update(ranges, value_input_option="USER_ENTERED")
        print(f"[OK] sheet row {row} ({target_date}) updated: C={post} F={follow} I={like} L={fb}")
        return True
    except Exception as e:
        print(f"ERROR sheet update failed: {e}")
        return False


def append_patrol_log(synced: bool, post: int, follow: int, like: int, fb: int, problem: bool, summary: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"[{ts}] PATROL+SYNC problem={problem} POST={post} FOLLOW={follow} LIKE={like} FB={fb} sheet_synced={'YES' if synced else 'NO'} | {summary[:80]}\n"
        with open(PATROL_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"WARN log append: {e}")


def extract_summary(patrol_output: str) -> str:
    """patrol_hourly.py 出力から1行サマリを抽出"""
    lines = patrol_output.split("\n")
    summary_parts = []
    for line in lines:
        if "[FOLLOW" in line or "[POST" in line or "[LIKE" in line or "[FOLLOWBK" in line:
            # 短くする
            for token in ("log_age=", "today_posted=", "today_liked=", "today_fb="):
                if token in line:
                    idx = line.find(token)
                    summary_parts.append(line[idx:idx+30].split()[0])
                    break
    return " ".join(summary_parts)


def main():
    target_date = date.today()
    print(f"=== PATROL+SYNC start: {target_date} ===")

    # 1. patrol 実行
    problem, patrol_out = run_patrol()
    print(patrol_out)

    # 1.5 rate_limit cooldown チェック + 情報収集フェーズ計装
    in_cooldown, rl_age = recent_rate_limit_check()
    if in_cooldown:
        print(f"\n[!! RATE_LIMIT COOLDOWN !!] 直近 {rl_age:.1f}min 前に rate_limit 検知。新規launcher発火は絶対回避。観測のみ継続。")
        # 情報収集フェーズ計装: 新規RL検知をsheetへリアルタイム記録 (重複書込み防止付き)
        try:
            _logs = json.loads(FOLLOW_RPA_LOG.read_text(encoding="utf-8")) if FOLLOW_RPA_LOG.exists() else []
            _last_rl = next(
                (e for e in reversed(_logs) if e.get("stop_reason") == "rate_limit_detected"), None
            )
            if _last_rl:
                _cur_state = load_rl_state()
                _already_logged = _cur_state and _cur_state.get("rl_ts") == _last_rl.get("timestamp", "")
                if not _already_logged:
                    append_rl_event_to_sheet(_last_rl, _logs)
        except Exception as _e:
            print(f"WARN rl_event_instrumentation: {_e}")
    else:
        # RL解除 or 非RL状態 → 回復時間バックフィル試行
        try:
            _logs = json.loads(FOLLOW_RPA_LOG.read_text(encoding="utf-8")) if FOLLOW_RPA_LOG.exists() else []
            try_backfill_recovery_time(_logs)
        except Exception as _e:
            print(f"WARN backfill: {_e}")

    # 1.6 seeds_done 枯渇検知 (Investigation BB/HH 2026-05-02)
    # 直近10セッションのうち all_seeds_done が3件以上 → シードプール枯渇警告
    try:
        _logs_all = []
        for _p in [FOLLOW_RPA_LOG, FOLLOW_HOST_LOG]:
            if _p.exists():
                _d = json.loads(_p.read_text(encoding="utf-8"))
                _logs_all.extend(_d if isinstance(_d, list) else [])
        _logs_all.sort(key=lambda x: x.get("timestamp", ""))
        _recent10 = _logs_all[-10:] if len(_logs_all) >= 10 else _logs_all
        _seeds_done_cnt = sum(1 for e in _recent10 if e.get("stop_reason") == "all_seeds_done")
        if _seeds_done_cnt >= 3:
            _total_seeds_done = sum(1 for e in _logs_all[-30:]
                                    if e.get("stop_reason") == "all_seeds_done")
            print(f"\n[!! SEED_POOL EXHAUSTING !!] 直近10件中 {_seeds_done_cnt}件 all_seeds_done。"
                  f"30件中合計={_total_seeds_done}件。"
                  f"推奨: python follow_rpa_vm.py --scrape (シードプール再取得)")
    except Exception as _e:
        print(f"WARN seeds_done_check: {_e}")

    # 2. 正本値収集
    post = get_post_today(target_date)
    follow = get_follow_today(target_date)
    like = get_like_today(target_date)
    fb, fb_pending = get_fb_today(target_date)

    print(f"\n[CANONICAL] {target_date}: POST={post} FOLLOW={follow} LIKE={like} FB={fb} FB_pending={fb_pending}")

    # 2.5 スプシから本日目標値取得 (CTO指示 K列=FB目標 必須)
    targets = read_targets_from_sheet(target_date)
    if targets:
        # daily_targets.json キャッシュ更新 (orchestrator_v5 runner_like が参照)
        try:
            import json as _json
            cache = {
                "date": target_date.isoformat(),
                "targets": targets,
                "updated_at": datetime.now().isoformat(),
            }
            DAILY_TARGETS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            DAILY_TARGETS_CACHE.write_text(
                _json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as _ce:
            print(f"WARN daily_targets_cache: {_ce}")
        post_t = targets.get("post", 0)
        follow_t = targets.get("follow", 0)
        like_t = targets.get("like", 0)
        fb_t = targets.get("fb", 0)
        # 残時間 (24:00まで)
        now = datetime.now()
        eod = now.replace(hour=23, minute=59, second=59, microsecond=0)
        remaining_h = max(0.1, (eod - now).total_seconds() / 3600)
        # CTO指定 優先順位: FOLLOW > POST > LIKE > FOLLOWBACK
        priority_map = {"FOLLOW": 1, "POST": 2, "LIKE": 3, "FB": 4}
        # Task Scheduler名 (auto stop用)
        task_map = {
            "POST": ["RoomBot_POST_Batch1", "RoomBot_POST_Batch2", "RoomBot_POST_Batch3"],
            "LIKE": ["RoomBot_LIKE_Hourly"],
            "FB":   ["RoomBot_FOLLOWBACK_Hourly", "RoomBot_FB_SourceFeed_4h"],
            # FOLLOW (host_runner) は最優先のため自動停止しない
        }
        print(f"\n[KPI] 4実装目標達成見込 (残{remaining_h:.1f}h):")
        for nm, cur, tgt in [("FOLLOW", follow, follow_t), ("POST", post, post_t),
                              ("LIKE", like, like_t), ("FB", fb, fb_t)]:
            if tgt <= 0:
                continue
            gap = tgt - cur
            pct = cur * 100.0 / tgt
            req_h = gap / remaining_h if gap > 0 else 0
            achieved = cur >= tgt
            yes_no = "YES" if achieved else "NO"
            # Task停止状態確認
            stopped_yn = "N/A"
            if nm in task_map:
                stopped_yn = _check_tasks_state(task_map[nm])
            need_more = "NO" if achieved else "YES"
            prio = priority_map.get(nm, 9)
            print(f"  P{prio} {nm:6s}: {cur}/{tgt} ({pct:.1f}%) gap={gap} 必要{req_h:.1f}/h "
                  f"達成={yes_no} 停止={stopped_yn} 追加要={need_more}")
            # CTO指示: 100%到達したら自動停止
            if achieved and nm in task_map and stopped_yn == "ACTIVE":
                print(f"    → CTO指示により {nm} の Task Scheduler を停止 (100%到達後)")
                _disable_tasks(task_map[nm])

    # 3. スプシ更新
    summary = extract_summary(patrol_out)
    synced = update_sheet(target_date, post, follow, like, fb, fb_pending, problem, summary)

    # 4. patrol_log 追記
    append_patrol_log(synced, post, follow, like, fb, problem, summary)

    # 5. 達成率出力 (FOLLOW)
    follow_t = (targets or {}).get("follow", 2095) or 2095
    follow_pct = follow * 100 / follow_t
    print(f"\n[FOLLOW達成率] {follow}/{follow_t} = {follow_pct:.1f}% (最低必達 2000)")
    if follow < 2000 and datetime.now().hour >= 23:
        print(f"[ALERT] 23時以降で 2000未達 ({follow}件)")

    return 0 if not problem else 1


if __name__ == "__main__":
    sys.exit(main())
