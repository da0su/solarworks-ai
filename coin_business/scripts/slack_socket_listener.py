"""
coin_business/scripts/slack_socket_listener.py  (Phase3 Socket Mode)
======================================================================
Slack Socket Mode でリアルタイム受信。

SLA:
  新着検知:    <1秒（Socket Mode WebSocket）
  ACK送信:     検知後 <5秒
  初回返信:    5分以内
  heartbeat:   1分毎にログ更新
  整合性チェック: 5分毎（polling補完）

起動:
    cd coin_business
    python scripts/slack_socket_listener.py

常駐 (bat):
    start_socket_listener.bat

副系 (ポーリング補完):
    CoinSlackPoller1min タスク（1分毎） → 取りこぼし監査
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# .env 読み込み（root / coin_business 両方）
for _env in [
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
    Path(__file__).parent.parent.parent / "bots" / "room_bot" / ".env",
]:
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
COIN_CHANNEL    = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")
BOT_USER_ID     = "U0AMM2M9Y48"

STATE_FILE     = Path(__file__).parent.parent / "data" / "slack_poll_state.json"
INBOX_LOG      = Path(__file__).parent.parent / "data" / "slack_inbox_log.jsonl"
HEARTBEAT_LOG  = Path(__file__).parent.parent / "data" / "slack_heartbeat.log"
PROCESSED_FILE = Path(__file__).parent.parent / "data" / "slack_processed_ts.json"
SOCKET_PID     = Path(__file__).parent.parent / "data" / "slack_socket_listener.pid"

STATE_FILE.parent.mkdir(exist_ok=True)

# ────────────────────────────────────────────────────────────────
# 種別判定
# ────────────────────────────────────────────────────────────────

_KIND_RULES = [
    ("差し戻し",  re.compile(r"差し?戻|NG|修正|やり直|リテイク", re.I)),
    ("完了確認",  re.compile(r"完了|確認しました|確認済|問題ない|ありがとう|ok", re.I)),
    ("確認依頼",  re.compile(r"確認して|見て|教えて|どう|\?|？", re.I)),
    ("依頼",      re.compile(r"してください|お願い|実装|作成|更新|修正|追加|対応|着手|指示", re.I)),
]

def classify(text: str) -> str:
    for kind, pat in _KIND_RULES:
        if pat.search(text):
            return kind
    return "要手動確認"


# ────────────────────────────────────────────────────────────────
# Slack API helper
# ────────────────────────────────────────────────────────────────

def _api_post(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{endpoint}", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_reaction(ts: str) -> bool:
    res = _api_post("reactions.add", {
        "channel": COIN_CHANNEL, "timestamp": ts, "name": "eyes"
    })
    return res.get("ok", False)


def send_thread_reply(ts: str, text: str) -> bool:
    res = _api_post("chat.postMessage", {
        "channel": COIN_CHANNEL, "thread_ts": ts, "text": text
    })
    return res.get("ok", False)


# ────────────────────────────────────────────────────────────────
# 状態管理
# ────────────────────────────────────────────────────────────────

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        try:
            return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")).get("ts_list", []))
        except Exception:
            pass
    return set()


def save_processed(ts_set: set) -> None:
    ts_list = sorted(ts_set)[-1000:]
    PROCESSED_FILE.write_text(
        json.dumps({"ts_list": ts_list}, ensure_ascii=False, indent=2),
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
        with INBOX_LOG.open(encoding="utf-8") as f:
            n = sum(1 for _ in f)
    return f"TASK-{n+1:04d}"


def log_inbox(entry: dict) -> None:
    with INBOX_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_heartbeat(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with HEARTBEAT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")


# ────────────────────────────────────────────────────────────────
# メッセージ処理（Socket & Polling共通）
# ────────────────────────────────────────────────────────────────

_processed_cache: set = load_processed()
_state_lock = threading.Lock()


def handle_message(ts: str, text: str, user: str, source: str = "socket") -> bool:
    """
    新着メッセージを処理。
    Returns True if processed (new), False if skipped (duplicate/bot).
    """
    if user == BOT_USER_ID or not user:
        return False
    if ts in _processed_cache:
        return False

    now_utc = datetime.now(timezone.utc)
    eta = (now_utc + timedelta(minutes=5)).strftime("%H:%M UTC")
    kind = classify(text)
    tid  = next_task_id()

    ts_dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    elapsed = (now_utc - ts_dt).total_seconds()

    print(f"[{source}] 新着 ts={ts} kind={kind} tid={tid} elapsed={elapsed:.1f}s user={user}")
    print(f"  text: {text[:80]}")

    # :eyes: リアクション
    add_reaction(ts)

    # ACK送信
    reply = (
        f"【キャップ⇒マーケ】受領済み\n"
        f"task_id={tid}\n"
        f"種別={kind}\n"
        f"初回報告予定={eta}"
    )
    ack_ok = send_thread_reply(ts, reply)
    ack_elapsed = (datetime.now(timezone.utc) - now_utc).total_seconds()

    print(f"  ACK {'ok' if ack_ok else 'FAIL'} ({ack_elapsed:.1f}s後)")
    log_heartbeat(
        f"[{source}] handle ts={ts[:10]} kind={kind} tid={tid} "
        f"ack={'ok' if ack_ok else 'FAIL'} elapsed={elapsed:.1f}s"
    )

    # INBOX記録
    entry = {
        "task_id": tid, "message_ts": ts, "thread_ts": ts,
        "sender": user, "kind": kind, "text_preview": text[:100],
        "received_at": now_utc.isoformat(),
        "first_reply_at": datetime.now(timezone.utc).isoformat() if ack_ok else "",
        "status": "受領済み" if ack_ok else "ACK_FAIL",
        "error_status": "" if ack_ok else "ACK_FAIL",
        "source": source,
        "detect_elapsed_sec": round(elapsed, 2),
        "ack_elapsed_sec": round(ack_elapsed, 2),
    }
    log_inbox(entry)

    # processed追加・state更新（ACK成功後）
    if ack_ok:
        _processed_cache.add(ts)
        save_processed(_processed_cache)

        with _state_lock:
            state = load_state()
            if float(ts) > float(state.get("last_ts", "0")):
                state["last_ts"] = ts
            state["total_ack"] = state.get("total_ack", 0) + 1
            state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
            state["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
    else:
        with _state_lock:
            state = load_state()
            state["total_fail"] = state.get("total_fail", 0) + 1
            save_state(state)

    return True


# ────────────────────────────────────────────────────────────────
# heartbeat スレッド（1分毎）
# ────────────────────────────────────────────────────────────────

def heartbeat_loop():
    while True:
        time.sleep(60)
        with _state_lock:
            state = load_state()
            state["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
        log_heartbeat(f"heartbeat ok | mode=socket | total_ack={state.get('total_ack',0)}")


# ────────────────────────────────────────────────────────────────
# Socket Mode メイン
# ────────────────────────────────────────────────────────────────

def run_socket_mode():
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=SLACK_BOT_TOKEN)

    @app.event("message")
    def handle_message_event(event, say):
        ts      = event.get("ts", "")
        text    = event.get("text", "")
        user    = event.get("user", "")
        channel = event.get("channel", "")
        subtype = event.get("subtype", "")

        # 対象チャンネル + 人間投稿のみ
        if channel != COIN_CHANNEL:
            return
        if subtype in ("bot_message", "message_changed", "message_deleted"):
            return
        if user == BOT_USER_ID:
            return

        handle_message(ts, text, user, source="socket")

    # 多重起動防止: 既存PIDが生きていれば終了
    if SOCKET_PID.exists():
        old_pid = SOCKET_PID.read_text(encoding="utf-8").strip()
        try:
            os.kill(int(old_pid), 0)  # プロセス存在チェック
            # Windowsではos.kill(pid, 0)が例外出ないので別方法
            import subprocess
            r = subprocess.run(
                ["tasklist", "/fi", f"pid eq {old_pid}"],
                capture_output=True, text=True
            )
            if old_pid in r.stdout:
                print(f"[socket_listener] 既存プロセス(PID={old_pid})を終了します")
                subprocess.run(["taskkill", "/f", "/pid", old_pid])
        except Exception:
            pass

    # PIDファイル書き込み
    SOCKET_PID.write_text(str(os.getpid()), encoding="utf-8")
    log_heartbeat(f"socket_listener started pid={os.getpid()}")
    print(f"[socket_listener] 起動 pid={os.getpid()} channel={COIN_CHANNEL}")

    # heartbeatスレッド開始
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()

    # Socket Mode 接続
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)
    if not SLACK_APP_TOKEN:
        print("ERROR: SLACK_APP_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)

    print(f"[socket_listener] Socket Mode で起動します channel={COIN_CHANNEL}")
    print(f"  BOT_TOKEN: {SLACK_BOT_TOKEN[:20]}...")
    print(f"  APP_TOKEN: {SLACK_APP_TOKEN[:20]}...")
    run_socket_mode()
