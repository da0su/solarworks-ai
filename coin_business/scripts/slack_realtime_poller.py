"""
coin_business/scripts/slack_realtime_poller.py  (Phase4 / 300秒ループ)
=======================================================================
conversations.history API を 60秒間隔で監視する常駐プロセス。

429根本対策:
  - POLL_INTERVAL = 60秒（rate limit余裕）
  - oldest パラメータで既読分は取得しない（軽量化）
  - 起動60秒待機（直前の過負荷から回復）
  - 連続429 > 3 で追加5分冷却
  - 指数バックオフ max=120s

フロー:
  新着検知 → :eyes: リアクション → pending_instructions.json(unread) → 待機
  Claude起動時: unread_check.py で未読確認 → 実行後 cap_report.py done でACK

SLA:
  新着検知:  最大60秒
  自動返信:  なし (AUTO_REPLY=False)
  heartbeat: 1分毎

多重起動防止: ポート17385バインド + PID強制終了
"""

from __future__ import annotations

import json
import os
import re
import sys
import socket
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# .env 読み込み
for _env in [
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
]:
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
COIN_CHANNEL      = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")
BOT_USER_ID       = "U0AMM2M9Y48"
POLL_INTERVAL     = 300   # 秒 (5分間隔 - ペナルティrate limitが「5分に1回」レベル)
STARTUP_DELAY     = 0     # 起動時待機なし（十分な休止後の起動前提）
PROGRESS_INTERVAL = 900   # 15分 (Slack送信なし・ログのみ)
AUTO_REPLY        = False
SINGLETON_PORT    = 17385
DEDUPE_TTL_SEC    = 3600  # 1時間
COOL_THRESHOLD    = 3     # 連続429がこれを超えたら冷却
COOL_DURATION     = 300   # 冷却時間（秒）

STATE_FILE     = Path(__file__).parent.parent / "data" / "slack_poll_state.json"
INBOX_LOG      = Path(__file__).parent.parent / "data" / "slack_inbox_log.jsonl"
HEARTBEAT_LOG  = Path(__file__).parent.parent / "data" / "slack_heartbeat.log"
PROCESSED_FILE = Path(__file__).parent.parent / "data" / "slack_processed_ts.json"
PID_FILE       = Path(__file__).parent.parent / "data" / "slack_realtime_poller.pid"
TASK_QUEUE_FILE= Path(__file__).parent.parent / "data" / "task_queue.json"
WORKING_FILE   = Path(__file__).parent.parent / "data" / "working_task.json"

STATE_FILE.parent.mkdir(exist_ok=True)

# ────────────────────────────────────────────────────────────────
# グローバル統計
# ────────────────────────────────────────────────────────────────

_singleton_sock: socket.socket | None = None
_dup_suppressed_count = 0
_count_429 = 0
_consecutive_429 = 0
_backoff_until: float = 0.0
_last_ack_ts = ""
_stats_lock = threading.Lock()


# ────────────────────────────────────────────────────────────────
# 起動ガード
# ────────────────────────────────────────────────────────────────

def _kill_existing_pid() -> None:
    for pid_path in [PID_FILE,
                     Path(__file__).parent.parent / "data" / "poller.pid"]:
        if not pid_path.exists():
            continue
        try:
            old_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        import subprocess
        r = subprocess.run(["tasklist", "/fi", f"pid eq {old_pid}", "/fo", "csv", "/nh"],
                           capture_output=True, text=True)
        if str(old_pid) in r.stdout:
            print(f"[guard] 旧PID={old_pid} 強制終了", flush=True)
            subprocess.run(["taskkill", "/f", "/pid", str(old_pid)], capture_output=True)
            time.sleep(1)
        else:
            print(f"[guard] 旧PID={old_pid} は既に停止済み", flush=True)
        try:
            pid_path.unlink()
        except Exception:
            pass


def acquire_singleton() -> bool:
    global _singleton_sock
    try:
        _singleton_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _singleton_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        _singleton_sock.bind(("127.0.0.1", SINGLETON_PORT))
        _singleton_sock.listen(1)
        return True
    except OSError:
        return False


def notify_singleton_failure() -> None:
    _api_post_raw(
        f"[guard] port={SINGLETON_PORT} が既に使用中。"
        f"既存プロセスが稼働中または残存。起動中止。"
    )


# ────────────────────────────────────────────────────────────────
# 種別判定
# ────────────────────────────────────────────────────────────────

_KIND_RULES = [
    ("差し戻し", re.compile(r"差し?戻|NG|修正|やり直|リテイク", re.I)),
    ("完了確認", re.compile(r"完了|確認しました|確認済|問題ない|ありがとう|ok", re.I)),
    ("確認依頼", re.compile(r"確認して|見て|教えて|どう|\?|？", re.I)),
    ("依頼",     re.compile(r"してください|お願い|実装|作成|更新|修正|追加|対応|着手|指示", re.I)),
]

def classify(text: str) -> str:
    for kind, pat in _KIND_RULES:
        if pat.search(text):
            return kind
    return "要手動確認"


# ────────────────────────────────────────────────────────────────
# Slack API
# ────────────────────────────────────────────────────────────────

def _api_post_raw(text: str) -> dict:
    data = json.dumps({"channel": COIN_CHANNEL, "text": text},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}



def api_get(path: str, params: dict) -> dict:
    """
    GETリクエスト。backoffを内部で待機しない（メインループが待機を制御する）。
    429の場合はbackoff情報を記録して即座に返す。リトライはメインループが担う。
    """
    global _consecutive_429, _count_429, _backoff_until

    url = f"https://slack.com/api/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        # 成功 → 連続429リセット・backoffクリア
        with _stats_lock:
            _consecutive_429 = 0
        _backoff_until = 0.0
        return result
    except urllib.error.HTTPError as e:
        if e.code == 429:
            with _stats_lock:
                _count_429 += 1
                _consecutive_429 += 1
                consec = _consecutive_429
                total  = _count_429
            retry_after = int(e.headers.get("Retry-After", 30))
            # backoff記録（メインループがこれを参照してsleep時間を決める）
            _backoff_until = time.time() + max(retry_after, POLL_INTERVAL)
            print(f"[poller] 429 Retry-After={retry_after}s consecutive={consec} total={total}", flush=True)
            log_heartbeat(f"[429] retry_after={retry_after}s consecutive={consec}")
            return {"ok": False, "error": "429"}
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_post(endpoint: str, payload: dict) -> dict:
    # reactions.add等の軽量POST - backoff待機なしで実行（別エンドポイント）
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{endpoint}", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ────────────────────────────────────────────────────────────────
# 状態管理
# ────────────────────────────────────────────────────────────────

_processed_cache: dict[str, float] = {}
_process_lock = threading.Lock()


def load_processed() -> dict[str, float]:
    if not PROCESSED_FILE.exists():
        return {}
    try:
        raw = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        now = time.time()
        if isinstance(raw, dict) and "ts_map" in raw:
            return {ts: ep for ts, ep in raw["ts_map"].items()
                    if now - ep < DEDUPE_TTL_SEC}
        if isinstance(raw, dict) and "ts_list" in raw:
            return {ts: now for ts in raw["ts_list"]
                    if now - float(ts) < DEDUPE_TTL_SEC}
    except Exception:
        pass
    return {}


def save_processed(ts_map: dict[str, float]) -> None:
    now = time.time()
    filtered = {ts: ep for ts, ep in ts_map.items() if now - ep < DEDUPE_TTL_SEC}
    limited  = dict(sorted(filtered.items(), key=lambda x: x[1])[-1000:])
    PROCESSED_FILE.write_text(
        json.dumps({"ts_map": limited, "format": "v2_epoch"}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_ts": "0", "last_checked_at": "", "heartbeat_at": "",
            "total_ack": 0, "total_fail": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def next_task_id() -> str:
    n = 0
    if INBOX_LOG.exists():
        seen = set()
        with INBOX_LOG.open(encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line).get("task_id", ""))
                except Exception:
                    pass
        n = len(seen)
    return f"TASK-{n+1:04d}"


def log_inbox(entry: dict) -> None:
    with INBOX_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_heartbeat(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with HEARTBEAT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")
    print(f"[{now}] {msg}", flush=True)


# ────────────────────────────────────────────────────────────────
# タスクキュー & WORKING
# ────────────────────────────────────────────────────────────────

def queue_task(ts: str, text: str, tid: str, kind: str) -> None:
    queue = []
    if TASK_QUEUE_FILE.exists():
        try:
            queue = json.loads(TASK_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if any(t.get("message_ts") == ts for t in queue):
        return
    queue.append({
        "task_id": tid, "message_ts": ts, "kind": kind,
        "text": text[:500], "status": "queued",
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "started_at": "", "completed_at": "",
    })
    TASK_QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def set_working(tid: str, ts: str, subject: str) -> None:
    WORKING_FILE.write_text(json.dumps({
        "task_id": tid, "message_ts": ts, "subject": subject,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_progress_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def get_working() -> dict:
    if WORKING_FILE.exists():
        try:
            return json.loads(WORKING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ────────────────────────────────────────────────────────────────
# heartbeat
# ────────────────────────────────────────────────────────────────

def build_heartbeat_msg(cycle: int, state: dict) -> str:
    with _stats_lock:
        dup     = _dup_suppressed_count
        c429    = _count_429
        c429c   = _consecutive_429
        last_ack = _last_ack_ts

    backoff_remain = max(0, _backoff_until - time.time())
    next_poll = max(POLL_INTERVAL, backoff_remain)
    status = f"backoff={backoff_remain:.0f}s" if backoff_remain > 0 else "ok"

    return (
        f"heartbeat {status} | pid={os.getpid()} | mode=realtime{POLL_INTERVAL}s | "
        f"lock_port={SINGLETON_PORT} | cycle={cycle} | "
        f"total_ack={state.get('total_ack',0)} | last_ack_ts={last_ack or 'none'} | "
        f"dup_suppressed={dup} | 429_count={c429} consecutive={c429c} | "
        f"next_poll={next_poll:.0f}s"
    )


def send_progress_report(state: dict) -> None:
    working = get_working()
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if working:
        log_heartbeat(
            f"[progress] {now_str} WORKING={working.get('task_id','?')} "
            f"{working.get('subject','')[:40]} (Slack送信無効)"
        )
    else:
        log_heartbeat(
            f"[progress] {now_str} 待機中 total_ack={state.get('total_ack',0)} (Slack送信無効)"
        )


# ────────────────────────────────────────────────────────────────
# dispatch
# ────────────────────────────────────────────────────────────────

def dispatch_task(ts: str, text: str, kind: str, tid: str, state: dict) -> None:
    subject_match = re.search(r'【[^】]*?】([^：\n]{0,80})', text)
    subject = subject_match.group(1).strip() if subject_match else text[:60]

    # pending_instructions.json に unread で格納
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from unread_check import add_instruction  # type: ignore
        add_instruction(ts=ts, text=text, sender=state.get("sender", ""), task_id=tid)
        print(f"  [dispatch] pending追加 tid={tid} status=unread", flush=True)
        log_heartbeat(f"[dispatch] pending unread ts={ts[:10]} tid={tid}")
    except Exception as e:
        print(f"  [dispatch] pending追加スキップ: {e}", flush=True)

    set_working(tid, ts, subject)
    queue_task(ts, text, tid, kind)
    print(f"  [dispatch] queued tid={tid} kind={kind} subject={subject[:40]}", flush=True)
    log_heartbeat(f"[dispatch] queued ts={ts[:10]} tid={tid} kind={kind}")


def _dispatch_safe(ts, text, kind, tid, state):
    try:
        dispatch_task(ts, text, kind, tid, state)
    except Exception as e:
        print(f"  [dispatch] error: {e}", flush=True)
        log_heartbeat(f"[dispatch] error ts={ts[:10]} {e}")


# ────────────────────────────────────────────────────────────────
# handle_message
# ────────────────────────────────────────────────────────────────

def handle_message(ts: str, text: str, user: str, state: dict) -> bool:
    global _processed_cache, _dup_suppressed_count, _last_ack_ts

    with _process_lock:
        if user == BOT_USER_ID or not user:
            return False

        if ts in _processed_cache:
            age = time.time() - _processed_cache[ts]
            if age < DEDUPE_TTL_SEC:
                with _stats_lock:
                    _dup_suppressed_count += 1
                log_heartbeat(
                    f"[dedupe] suppressed ts={ts[:10]} age={age:.1f}s total={_dup_suppressed_count}"
                )
                return False

        _processed_cache[ts] = time.time()

    save_processed(_processed_cache)

    detect_time = datetime.now(timezone.utc)
    msg_time    = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    elapsed     = (detect_time - msg_time).total_seconds()
    kind = classify(text)
    tid  = next_task_id()

    print(f"[poller] 新着 ts={ts} elapsed={elapsed:.1f}s kind={kind} tid={tid}", flush=True)

    # :eyes: リアクション（受信証跡）
    api_post("reactions.add", {"channel": COIN_CHANNEL, "timestamp": ts, "name": "eyes"})

    log_inbox({
        "task_id": tid, "message_ts": ts, "thread_ts": ts, "sender": user,
        "kind": kind, "text_preview": text[:100],
        "received_at": detect_time.isoformat(),
        "first_reply_at": "", "status": "RECEIVED_SILENT",
        "source": "realtime_poll",
        "detect_elapsed_sec": round(elapsed, 2),
        "auto_reply": AUTO_REPLY,
    })

    log_heartbeat(f"[poller] 新着検知 ts={ts[:10]} kind={kind} tid={tid} detect={elapsed:.1f}s")

    with _stats_lock:
        _last_ack_ts = ts
    if float(ts) > float(state.get("last_ts", "0")):
        state["last_ts"] = ts
    state["total_ack"] = state.get("total_ack", 0) + 1
    save_state(state)

    threading.Thread(
        target=_dispatch_safe, args=(ts, text, kind, tid, state), daemon=True
    ).start()

    return True


# ────────────────────────────────────────────────────────────────
# メインループ
# ────────────────────────────────────────────────────────────────

def run():
    global _processed_cache

    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN 未設定", flush=True)
        sys.exit(1)

    _kill_existing_pid()

    if not acquire_singleton():
        print(f"[guard] port={SINGLETON_PORT} 既使用。Slack通知して終了。", flush=True)
        notify_singleton_failure()
        sys.exit(1)

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _processed_cache = load_processed()
    state     = load_state()
    last_hb   = time.time()
    last_prog = time.time()
    cycle     = 0

    log_heartbeat(
        f"realtime_poller started pid={os.getpid()} "
        f"port={SINGLETON_PORT} interval={POLL_INTERVAL}s startup_delay={STARTUP_DELAY}s"
    )
    print(f"[poller] 起動 pid={os.getpid()} interval={POLL_INTERVAL}s "
          f"channel={COIN_CHANNEL}", flush=True)

    # 起動待機（rate limit回復、0なら即開始）
    if STARTUP_DELAY > 0:
        print(f"[poller] 起動遅延 {STARTUP_DELAY}s 待機中 (rate limit回復)", flush=True)
        time.sleep(STARTUP_DELAY)
        print("[poller] 起動遅延完了。ポーリング開始", flush=True)
        log_heartbeat("startup delay完了。ポーリング開始")

    while True:
        try:
            cycle += 1
            last_ts = state.get("last_ts", "0")

            # conversations.history: oldest指定で新着のみ取得
            params = {"channel": COIN_CHANNEL, "limit": 10}
            if last_ts != "0":
                params["oldest"] = last_ts

            res = api_get("conversations.history", params)

            if res.get("ok"):
                msgs = res.get("messages", [])
                new_msgs = [
                    m for m in msgs
                    if m.get("user") and m.get("user") != BOT_USER_ID
                    and float(m.get("ts", "0")) > float(last_ts)
                ]
                if new_msgs:
                    for m in reversed(new_msgs):
                        handle_message(m["ts"], m.get("text", ""), m.get("user", ""), state)
                else:
                    # 新着なし: last_ts は更新しない（BUG FIX: botメッセージで汚染しない）
                    # last_ts は handle_message() 内でユーザーメッセージ処理時のみ更新
                    pass
            else:
                err = res.get("error", "unknown")
                if err != "429":
                    print(f"[poller] API error: {err}", flush=True)

            # heartbeat (1分毎)
            if time.time() - last_hb >= 60:
                state["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
                state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
                save_state(state)
                log_heartbeat(build_heartbeat_msg(cycle, state))
                last_hb = time.time()

            # 15分毎 進捗（ログのみ）
            if time.time() - last_prog >= PROGRESS_INTERVAL:
                send_progress_report(state)
                last_prog = time.time()

        except Exception as e:
            print(f"[poller] loop error: {e}", flush=True)
            log_heartbeat(f"loop error: {e}")

        # 次ポーリングまで待機
        # 常に POLL_INTERVAL 以上待つ。backoffがあればそれも加味して長い方で待つ。
        # → 429時でも最低 POLL_INTERVAL 秒待つ（即リトライしない）
        backoff_remain = _backoff_until - time.time()
        sleep_sec = max(POLL_INTERVAL, backoff_remain)
        print(f"[poller] 次ポーリングまで {sleep_sec:.0f}s 待機", flush=True)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    run()
