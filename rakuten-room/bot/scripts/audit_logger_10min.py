"""05_上限分析 タブへ 10分毎に追記する FOLLOW audit logger.

CEO 5/10 指示: 5/11 限定で 10分×144回/24h の高密度 audit.
目的: throttle/cooldown の正確な timing を分単位で記録.

各実行で:
- follow_history.json から直近 10分 / 1h / 当日累計 を計測
- 最新 log から timeout/abort/error を検出
- 05_上限分析 に 1 行追記

Output: スプシ 05_上限分析 タブに 1 行 + state/audit_log_10min.json に履歴
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BOT_DIR = REPO_ROOT / "rakuten-room" / "bot"
sys.path.insert(0, str(BOT_DIR))

HIST_PATH = BOT_DIR / "data" / "follow_history.json"
LOG_DIR = BOT_DIR / "data" / "logs"
STATE_PATH = REPO_ROOT / "state" / "audit_log_10min.json"
GSPREAD_CREDS = REPO_ROOT / "credentials" / "sheets_service_account.json"
SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
SHEET_NAME = "05_上限分析"


def get_recent_log_signals(now: datetime, mins: int = 10) -> dict:
    """直近 N 分の log から timeout / abort / error / harvested 集計"""
    log_file = LOG_DIR / f"{now.strftime('%Y-%m-%d')}.log"
    signals = {
        "timeouts": 0, "page_goto_timeouts": 0, "login_timeouts": 0,
        "skipped_total": 0, "achievement_total": 0, "deadline_count": 0,
        "harvested_lines": 0, "abort_count": 0,
    }
    if not log_file.exists():
        return signals
    cutoff = now - timedelta(minutes=mins)
    cutoff_str = cutoff.strftime("%H:%M:%S")
    pattern_ts = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})')
    today = now.strftime("%Y-%m-%d")
    try:
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                m = pattern_ts.match(line)
                if not m: continue
                if m.group(1) != today: continue
                if m.group(2) < cutoff_str: continue
                low = line.lower()
                if "page.goto" in low and "timeout" in low:
                    signals["page_goto_timeouts"] += 1
                if "ログイン確認中にエラー" in line:
                    signals["login_timeouts"] += 1
                if "timeout" in low:
                    signals["timeouts"] += 1
                if "achievement:" in line:
                    signals["achievement_total"] += 1
                if "[deadline reached]" in line:
                    signals["deadline_count"] += 1
                if "harvested=" in line:
                    signals["harvested_lines"] += 1
                if "abort=" in line:
                    signals["abort_count"] += 1
                m2 = re.search(r'skipped:\s*(\d+)', line)
                if m2:
                    signals["skipped_total"] += int(m2.group(1))
    except Exception:
        pass
    return signals


def main():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # follow_history 読込
    hist = []
    if HIST_PATH.exists():
        try:
            hist = json.loads(HIST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 当日 + 直近 10min / 1h カウント (real follows only)
    cum_today = 0
    last_10min = 0
    last_60min = 0
    last_follow_time = ""
    cutoff_10 = now - timedelta(minutes=10)
    cutoff_60 = now - timedelta(minutes=60)

    for h in hist:
        if not isinstance(h, dict): continue
        if h.get("source") == "skip_discover": continue
        ts = h.get("followed_at", "")
        try:
            dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        if dt.strftime("%Y-%m-%d") == today:
            cum_today += 1
            last_follow_time = ts
            if dt > cutoff_10: last_10min += 1
            if dt > cutoff_60: last_60min += 1

    # gap from last follow (sec)
    gap_sec = -1
    if last_follow_time:
        try:
            dt = datetime.fromisoformat(last_follow_time)
            gap_sec = int((now - dt).total_seconds())
        except Exception:
            pass

    # log signals
    sig = get_recent_log_signals(now, mins=10)

    # throttle 判定 ロジック
    judge = ""
    if last_60min == 0 and cum_today > 0:
        judge = "完全停止 (60分)"
    elif last_10min == 0 and gap_sec > 600:
        judge = f"沈黙 (last={gap_sec}s)"
    elif sig["page_goto_timeouts"] >= 5:
        judge = "page timeout 多発"
    elif sig["login_timeouts"] >= 1:
        judge = "login timeout"
    elif sig["abort_count"] >= 1:
        judge = "abort 検知"
    elif last_10min >= 5:
        judge = "正常稼働"
    elif last_10min == 0:
        judge = "0/10min"
    else:
        judge = f"{last_10min}/10min"

    # スプシ 列順:
    # A:記録日時 B:日付 C:機能 D:区分 E:内部制御名 F:楽天側上限画面文言
    # G:上限検知時URL H:直前成功件数 I:当日累計件数 J:stop_reason
    # K:cooldown開始時刻 L:cooldown終了想定時刻 M:cooldown終了実測時刻
    # N:復帰直後成功件数 O:復帰条件 P:正本ログ名/DB名
    # Q:スクショ/証拠保存場所 R:判定 S:担当 T:備考
    row = [
        now.strftime("%Y-%m-%d %H:%M:%S"),     # A 記録日時
        today,                                  # B 日付
        "FOLLOW",                               # C 機能
        "10min audit",                          # D 区分
        f"audit#{(now.hour*6 + now.minute//10):03d}",  # E 内部制御名
        "",                                     # F 楽天側上限画面文言
        "https://room.rakuten.co.jp",           # G URL
        str(last_10min),                        # H 直前成功件数 (10min)
        str(cum_today),                         # I 当日累計
        "audit_snapshot",                       # J stop_reason
        "", "", "",                             # K-M cooldown
        str(last_60min),                        # N 復帰直後 (60min)
        f"gap_sec={gap_sec}",                   # O 復帰条件
        "follow_history.json",                  # P 正本ログ名
        "",                                     # Q スクショ
        judge,                                  # R 判定
        "サイバー",                             # S 担当
        f"timeouts={sig['page_goto_timeouts']} login_to={sig['login_timeouts']} abort={sig['abort_count']} harvested_lines={sig['harvested_lines']} ach={sig['achievement_total']} deadline={sig['deadline_count']}",  # T 備考
    ]

    # スプシ追記
    try:
        import gspread
        gc = gspread.service_account(filename=str(GSPREAD_CREDS))
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet(SHEET_NAME)
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"[OK] {now.strftime('%H:%M')} cum={cum_today} 10min={last_10min} 60min={last_60min} gap={gap_sec}s judge={judge}")
    except Exception as e:
        print(f"[ERR] sheet append: {e}")
        # ファイル fallback
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if STATE_PATH.exists():
            try: existing = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except: pass
        existing.append({"row": row, "snapshot": {"cum_today": cum_today, "last_10min": last_10min, "last_60min": last_60min, "gap_sec": gap_sec, "judge": judge}})
        STATE_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
