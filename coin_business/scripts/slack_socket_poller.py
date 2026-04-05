"""
coin_business/scripts/slack_socket_poller.py
=============================================
Slack Socket Mode (WebSocket) による常駐プロセス。

【概要】
  conversations.historyポーリングを完全廃止。
  Slack がメッセージをWebSocketでPUSHしてくれる。
  rate limit 429 問題を根本解消。

【前提条件（CEOまたは管理者が設定）】
  1. https://api.slack.com/apps → 対象アプリ → 「Socket Mode」を有効化
  2. 「App-Level Tokens」→ 「Generate Tokens」
     - Token Name: cap-socket
     - Scopes: connections:write
     → xapp-1-... 形式のトークンを発行
  3. 「Event Subscriptions」→ 「Subscribe to bot events」
     - message.channels（または message.groups）を追加
  4. 発行された xapp トークンを .env に追加:
     SLACK_APP_TOKEN=xapp-1-...

【動作】
  - WebSocket接続を維持し、メッセージイベントをリアルタイム受信
  - 新着検知 → pending_instructions.json(unread) に追加
  - :eyes: リアクション付与
  - rate limit 429 一切なし

【起動】
  python scripts/slack_socket_poller.py

【依存】
  pip install websocket-client
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

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")  # xapp-1-... 形式
COIN_CHANNEL    = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")
BOT_USER_ID     = "U0AMM2M9Y48"
SINGLETON_PORT  = 17386   # socket_pollerは17386（historyポーラーは17385）
DEDUPE_TTL_SEC  = 3600

INBOX_LOG       = Path(__file__).parent.parent / "data" / "slack_inbox_log.jsonl"
HEARTBEAT_LOG   = Path(__file__).parent.parent / "data" / "slack_heartbeat.log"
PROCESSED_FILE  = Path(__file__).parent.parent / "data" / "slack_processed_ts.json"
PID_FILE        = Path(__file__).parent.parent / "data" / "slack_socket_poller.pid"
TASK_QUEUE_FILE = Path(__file__).parent.parent / "data" / "task_queue.json"
WORKING_FILE    = Path(__file__).parent.parent / "data" / "working_task.json"

INBOX_LOG.parent.mkdir(exist_ok=True)

_processed_cache: dict[str, float] = {}
_singleton_sock = None
_last_ack_ts = ""

# ────────────────────────────────────────────────────────────────
# 前提条件チェック
# ────────────────────────────────────────────────────────────────

def check_prerequisites() -> bool:
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN 未設定", flush=True)
        return False
    if not SLACK_APP_TOKEN:
        print("ERROR: SLACK_APP_TOKEN 未設定", flush=True)
        print("設定方法:", flush=True)
        print("  1. https://api.slack.com/apps → 対象アプリ", flush=True)
        print("  2. 「Socket Mode」を有効化", flush=True)
        print("  3. 「App-Level Tokens」→ Generate Tokens (scope: connections:write)", flush=True)
        print("  4. .env に SLACK_APP_TOKEN=xapp-1-... を追加", flush=True)
        return False
    if not SLACK_APP_TOKEN.startswith("xapp-"):
        print(f"ERROR: SLACK_APP_TOKEN が xapp- で始まっていません: {SLACK_APP_TOKEN[:10]}...", flush=True)
        return False
    try:
        import websocket  # noqa
    except ImportError:
        print("ERROR: websocket-client 未インストール", flush=True)
        print("  pip install websocket-client", flush=True)
        return False
    return True


# ────────────────────────────────────────────────────────────────
# Slack API (POST/GET)
# ────────────────────────────────────────────────────────────────

def _api_post(endpoint: str, payload: dict, token: str = "") -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{endpoint}", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {token or SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _api_get(path: str, params: dict, token: str = "") -> dict:
    url = f"https://slack.com/api/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token or SLACK_BOT_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_wss_url() -> str | None:
    """Socket Mode用のWebSocket URLを取得"""
    res = _api_post(
        "apps.connections.open", {},
        token=SLACK_APP_TOKEN
    )
    if res.get("ok"):
        return res.get("url")
    print(f"[socket] apps.connections.open 失敗: {res.get('error')}", flush=True)
    return None


# ────────────────────────────────────────────────────────────────
# ユーティリティ
# ────────────────────────────────────────────────────────────────

def log_heartbeat(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with HEARTBEAT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now}] [socket] {msg}\n")
    print(f"[{now}] [socket] {msg}", flush=True)


def classify(text: str) -> str:
    KIND_RULES = [
        ("差し戻し", re.compile(r"差し?戻|NG|修正|やり直|リテイク", re.I)),
        ("完了確認", re.compile(r"完了|確認しました|確認済|問題ない|ありがとう|ok", re.I)),
        ("確認依頼", re.compile(r"確認して|見て|教えて|どう|\?|？", re.I)),
        ("依頼",    re.compile(r"してください|お願い|実装|作成|更新|修正|追加|対応|着手|指示", re.I)),
    ]
    for kind, pat in KIND_RULES:
        if pat.search(text):
            return kind
    return "要手動確認"


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


def load_processed() -> dict[str, float]:
    if not PROCESSED_FILE.exists():
        return {}
    try:
        raw = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        now = time.time()
        if isinstance(raw, dict) and "ts_map" in raw:
            return {ts: ep for ts, ep in raw["ts_map"].items()
                    if now - ep < DEDUPE_TTL_SEC}
    except Exception:
        pass
    return {}


def save_processed(ts_map: dict[str, float]) -> None:
    now = time.time()
    filtered = {ts: ep for ts, ep in ts_map.items() if now - ep < DEDUPE_TTL_SEC}
    limited = dict(sorted(filtered.items(), key=lambda x: x[1])[-1000:])
    PROCESSED_FILE.write_text(
        json.dumps({"ts_map": limited, "format": "v2_epoch"}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def dispatch_message(ts: str, text: str, user: str) -> None:
    """新着メッセージを pending_instructions.json に追加"""
    global _processed_cache, _last_ack_ts

    # 重複チェック
    if ts in _processed_cache:
        age = time.time() - _processed_cache[ts]
        if age < DEDUPE_TTL_SEC:
            log_heartbeat(f"[dedupe] suppressed ts={ts[:10]} age={age:.1f}s")
            return

    _processed_cache[ts] = time.time()
    save_processed(_processed_cache)

    detect_time = datetime.now(timezone.utc)
    kind = classify(text)
    tid  = next_task_id()
    _last_ack_ts = ts

    print(f"[socket] 新着 ts={ts} kind={kind} tid={tid}", flush=True)

    # :eyes: リアクション
    _api_post("reactions.add", {"channel": COIN_CHANNEL, "timestamp": ts, "name": "eyes"})

    # pending_instructions.json に unread で追加
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from unread_check import add_instruction  # type: ignore
        add_instruction(ts=ts, text=text, sender=user, task_id=tid)
        log_heartbeat(f"[dispatch] pending unread ts={ts[:10]} tid={tid}")
    except Exception as e:
        log_heartbeat(f"[dispatch] pending追加スキップ: {e}")

    # inbox log
    log_inbox({
        "task_id": tid, "message_ts": ts, "sender": user,
        "kind": kind, "text_preview": text[:100],
        "received_at": detect_time.isoformat(),
        "status": "RECEIVED_SILENT", "source": "socket_mode",
    })

    # task_queue & working
    _queue_task(ts, text, tid, kind)
    subject_match = re.search(r'【[^】]*?】([^：\n]{0,80})', text)
    subject = subject_match.group(1).strip() if subject_match else text[:60]
    WORKING_FILE.write_text(json.dumps({
        "task_id": tid, "message_ts": ts, "subject": subject,
        "started_at": detect_time.isoformat(),
        "last_progress_at": detect_time.isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _queue_task(ts: str, text: str, tid: str, kind: str) -> None:
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
    })
    TASK_QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# Socket Mode メインループ
# ────────────────────────────────────────────────────────────────

def handle_event(event_data: dict, ws) -> None:
    """Slack Socket Mode から受信したイベントを処理"""
    envelope_id = event_data.get("envelope_id", "")

    # ACK (必須)
    if envelope_id:
        ws.send(json.dumps({"envelope_id": envelope_id}))

    payload = event_data.get("payload", {})
    if not isinstance(payload, dict):
        return

    event = payload.get("event", {})
    if not event:
        return

    event_type = event.get("type", "")
    if event_type != "message":
        return

    # botメッセージ・subtype付きは無視
    user = event.get("user", "")
    if not user or user == BOT_USER_ID:
        return
    if event.get("subtype"):
        return

    channel = event.get("channel", "")
    if channel != COIN_CHANNEL:
        return

    ts   = event.get("ts", "")
    text = event.get("text", "")

    if ts:
        threading.Thread(
            target=dispatch_message, args=(ts, text, user), daemon=True
        ).start()


def run_socket():
    """WebSocket接続を維持するメインループ"""
    import websocket

    reconnect_wait = 5

    while True:
        wss_url = get_wss_url()
        if not wss_url:
            log_heartbeat(f"WebSocket URL取得失敗。{reconnect_wait}秒後に再試行")
            time.sleep(reconnect_wait)
            reconnect_wait = min(reconnect_wait * 2, 300)
            continue

        reconnect_wait = 5  # 成功したらリセット
        log_heartbeat(f"WebSocket接続開始 pid={os.getpid()}")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                if msg_type == "hello":
                    log_heartbeat("接続確立 (hello受信)")
                elif msg_type == "events_api":
                    handle_event(data, ws)
                elif msg_type == "disconnect":
                    log_heartbeat(f"切断通知: {data.get('reason','')}")
                    ws.close()
            except Exception as e:
                log_heartbeat(f"on_message error: {e}")

        def on_error(ws, error):
            log_heartbeat(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            log_heartbeat(f"接続切断 code={close_status_code}")

        def on_open(ws):
            log_heartbeat("WebSocket open")

        ws_app = websocket.WebSocketApp(
            wss_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        try:
            ws_app.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log_heartbeat(f"run_forever error: {e}")

        log_heartbeat(f"切断。{reconnect_wait}秒後に再接続")
        time.sleep(reconnect_wait)


# ────────────────────────────────────────────────────────────────
# シングルトン保証
# ────────────────────────────────────────────────────────────────

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


# ────────────────────────────────────────────────────────────────
# エントリーポイント
# ────────────────────────────────────────────────────────────────

def main():
    global _processed_cache

    if not check_prerequisites():
        print("\n[setup] 上記の手順でSocket Modeを設定してから再起動してください。", flush=True)
        sys.exit(1)

    if not acquire_singleton():
        print(f"[guard] port={SINGLETON_PORT} 既使用。別インスタンスが稼働中。", flush=True)
        sys.exit(1)

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _processed_cache = load_processed()

    log_heartbeat(
        f"slack_socket_poller started pid={os.getpid()} "
        f"singleton_port={SINGLETON_PORT} channel={COIN_CHANNEL}"
    )

    try:
        run_socket()
    except KeyboardInterrupt:
        log_heartbeat("手動停止")
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()


if __name__ == "__main__":
    main()
