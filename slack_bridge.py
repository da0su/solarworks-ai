"""AI間Slack連携ブリッジ v2.0
キャップさん <-> サイバーさん がSlack経由で構造化メッセージをやり取り

Usage:
    python slack_bridge.py watch --interval 5          # 常時監視（5秒）
    python slack_bridge.py send-task --task test-ping --to cyber
    python slack_bridge.py send-task --task ebay-search --to cyber
    python slack_bridge.py retry-pending                # 未完了タスクのリトライ
    python slack_bridge.py status                       # 現在のタスク状態一覧
    python slack_bridge.py set-sender cap               # 送信者ID設定

    # 旧互換
    python slack_bridge.py send "メッセージ"
    python slack_bridge.py receive
    python slack_bridge.py read
"""
import os
import sys
import time
import json
import uuid
import argparse
import subprocess
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("slack_sdk がインストールされていません。")
    print("pip install slack_sdk を実行してください。")
    sys.exit(1)


# ============================================================
# 定数・設定
# ============================================================
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
AI_BRIDGE_CHANNEL = "C0ALNTMJ2JZ"
CEO_ROOM_CHANNEL = "C0ALSAPMYHY"
BRIDGE_DELIMITER = "---BRIDGE_MSG---"
MSG_VERSION = "1.0"

# タイムアウト設定
ACK_TIMEOUT_SEC = 180       # 3分
DONE_TIMEOUT_SEC = 1800     # 30分
MAX_RETRIES = 2             # 再送最大2回（計3回試行）
RETRY_CHECK_INTERVAL = 30   # 30秒ごとにリトライチェック

# データディレクトリ
DATA_DIR = Path.home() / ".slack_bridge"
TASK_REGISTRY_FILE = DATA_DIR / "task_registry.jsonl"
PENDING_TASKS_FILE = DATA_DIR / "pending_tasks.jsonl"
LAST_SEEN_FILE = DATA_DIR / "last_seen.txt"
SENDER_FILE = DATA_DIR / "sender.txt"
LOG_FILE = DATA_DIR / "bridge.log"
EVENTS_FILE = DATA_DIR / "events.jsonl"
LATEST_MSG_FILE = DATA_DIR / "latest_msg.txt"

# 旧ファイルパス（マイグレーション用）
OLD_SENDER_FILE = Path.home() / ".slack_bridge_sender"
OLD_LAST_SEEN_FILE = Path.home() / ".slack_bridge_last_seen"
OLD_LATEST_MSG_FILE = Path.home() / ".slack_bridge_latest_msg"

# ACK不要のメッセージタイプ（ループ防止）
NO_ACK_TYPES = {"ACK", "DONE", "ERROR", "ESCALATE"}


# ============================================================
# 初期化
# ============================================================
def ensure_data_dir():
    """データディレクトリを作成し、旧ファイルをマイグレーション"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 旧sender → 新sender
    if OLD_SENDER_FILE.exists() and not SENDER_FILE.exists():
        SENDER_FILE.write_text(OLD_SENDER_FILE.read_text().strip(), encoding="utf-8")

    # 旧last_seen → 新last_seen
    if OLD_LAST_SEEN_FILE.exists() and not LAST_SEEN_FILE.exists():
        LAST_SEEN_FILE.write_text(OLD_LAST_SEEN_FILE.read_text().strip(), encoding="utf-8")


ensure_data_dir()


# ============================================================
# ログ
# ============================================================
logger = logging.getLogger("slack_bridge")
logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

_sh = logging.StreamHandler(sys.stdout)
_sh.setLevel(logging.INFO)
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_sh)


def log_event(event_type: str, data: dict):
    """events.jsonlにJSONLイベントを追記"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ============================================================
# Slackクライアント（429バックオフ付き）
# ============================================================
_client = None


def get_client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=SLACK_BOT_TOKEN)
    return _client


def slack_call_with_backoff(func, *args, max_backoff=60, **kwargs):
    """Slack API呼び出し。429時に指数バックオフ"""
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except SlackApiError as e:
            if e.response.status_code == 429:
                wait = min(2 ** attempt, max_backoff)
                retry_after = int(e.response.headers.get("Retry-After", wait))
                wait = max(wait, retry_after)
                logger.warning(f"Rate limited (429). Waiting {wait}s...")
                time.sleep(wait)
                attempt += 1
            else:
                raise


# ============================================================
# 送信者ID管理
# ============================================================
def get_sender() -> str:
    if SENDER_FILE.exists():
        return SENDER_FILE.read_text(encoding="utf-8").strip()
    return "unknown"


def set_sender(sender_id: str):
    SENDER_FILE.write_text(sender_id, encoding="utf-8")
    logger.info(f"送信者IDを '{sender_id}' に設定しました")


# ============================================================
# メッセージ構築・パース
# ============================================================
def build_bridge_message(display_text: str, msg_data: dict) -> str:
    """表示テキスト + BRIDGE_DELIMITER + JSON の2段構成メッセージを構築"""
    json_str = json.dumps(msg_data, ensure_ascii=False, indent=None)
    return f"{display_text}\n{BRIDGE_DELIMITER}\n{json_str}"


def parse_bridge_message(text: str) -> dict | None:
    """BRIDGE_DELIMITER以降をJSONパース。なければNone"""
    if BRIDGE_DELIMITER not in text:
        return None
    parts = text.split(BRIDGE_DELIMITER, 1)
    if len(parts) < 2:
        return None
    try:
        return json.loads(parts[1].strip())
    except json.JSONDecodeError:
        logger.warning(f"JSONパース失敗: {parts[1][:100]}")
        return None


def make_task_msg(from_id: str, to_id: str, task: str, payload: dict = None,
                  msg_type: str = "TASK", task_id: str = None,
                  correlation_id: str = None) -> dict:
    """標準メッセージ構造を生成"""
    return {
        "version": MSG_VERSION,
        "from": from_id,
        "to": to_id,
        "type": msg_type,
        "task": task,
        "task_id": task_id or str(uuid.uuid4()),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }


def make_response_msg(original: dict, msg_type: str, payload: dict = None) -> dict:
    """ACK/DONE/ERROR応答メッセージを生成"""
    return {
        "version": MSG_VERSION,
        "from": get_sender(),
        "to": original["from"],
        "type": msg_type,
        "task": original.get("task", ""),
        "task_id": original["task_id"],
        "correlation_id": original.get("correlation_id", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }


# ============================================================
# Slack送受信（旧互換維持）
# ============================================================
def send_message(text: str, sender: str = None, channel: str = None) -> bool:
    """Slackチャンネルにメッセージを送信（旧互換 + 新形式対応）"""
    client = get_client()
    channel = channel or AI_BRIDGE_CHANNEL
    sender = sender or get_sender()

    # BRIDGE_DELIMITERが含まれていなければ旧形式としてプレフィックス付与
    if BRIDGE_DELIMITER not in text:
        text = f"[{sender}] {text}"

    try:
        slack_call_with_backoff(
            client.chat_postMessage,
            channel=channel,
            text=text,
        )
        logger.info(f"送信完了: {text[:80]}")
        log_event("send", {"channel": channel, "text": text[:200]})
        return True
    except SlackApiError as e:
        logger.error(f"送信エラー: {e.response['error']}")
        return False


def send_bridge_msg(msg_data: dict, channel: str = None) -> bool:
    """構造化ブリッジメッセージを送信"""
    sender = msg_data.get("from", get_sender())
    msg_type = msg_data.get("type", "TASK")
    task = msg_data.get("task", "")
    display = f"[{sender}] [{msg_type}] {task}"
    if msg_type == "DONE":
        display = f"[{sender}] [{msg_type}] {task} 完了"
    elif msg_type == "ERROR":
        display = f"[{sender}] [{msg_type}] {task} エラー"
    elif msg_type == "ACK":
        display = f"[{sender}] [{msg_type}] {task} 受信確認"
    elif msg_type == "ESCALATE":
        display = f"[{sender}] [ESCALATE] {task} エスカレーション"

    full_text = build_bridge_message(display, msg_data)
    return send_message(full_text, sender=sender, channel=channel)


def receive_messages(limit: int = 5, channel: str = None) -> list:
    """チャンネルの最新メッセージを取得（旧互換維持）"""
    client = get_client()
    channel = channel or AI_BRIDGE_CHANNEL
    try:
        result = slack_call_with_backoff(
            client.conversations_history,
            channel=channel,
            limit=limit,
        )
        messages = []
        for msg in result.get("messages", []):
            messages.append({
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
                "user": msg.get("user", "bot"),
            })
        return messages
    except SlackApiError as e:
        logger.error(f"受信エラー: {e.response['error']}")
        return []


# ============================================================
# タスクレジストリ（重複防止）
# ============================================================
def load_task_registry() -> dict:
    """task_id -> {status, timestamp, ...} の辞書をロード"""
    registry = {}
    if TASK_REGISTRY_FILE.exists():
        with open(TASK_REGISTRY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    registry[entry["task_id"]] = entry
                except (json.JSONDecodeError, KeyError):
                    continue
    return registry


def save_task_registry_entry(task_id: str, status: str, extra: dict = None):
    """タスクレジストリに1エントリ追記"""
    entry = {
        "task_id": task_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        entry.update(extra)
    with open(TASK_REGISTRY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log_event("registry_update", entry)


def is_task_processed(task_id: str) -> bool:
    """同一task_idが既に処理済みかチェック"""
    registry = load_task_registry()
    return task_id in registry


# ============================================================
# Pending Tasks管理（リトライ/エスカレーション）
# ============================================================
def load_pending_tasks() -> list:
    """pending_tasks.jsonlを全行ロード"""
    tasks = []
    if PENDING_TASKS_FILE.exists():
        with open(PENDING_TASKS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tasks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return tasks


def save_pending_tasks(tasks: list):
    """pending_tasks.jsonlを上書き保存"""
    with open(PENDING_TASKS_FILE, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def add_pending_task(msg_data: dict):
    """送信タスクをpendingに追加"""
    entry = {
        "task_id": msg_data["task_id"],
        "task": msg_data.get("task", ""),
        "to": msg_data.get("to", ""),
        "msg_data": msg_data,
        "status": "sent",
        "retry_count": 0,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "ack_at": None,
        "done_at": None,
    }
    tasks = load_pending_tasks()
    tasks.append(entry)
    save_pending_tasks(tasks)


def update_pending_task(task_id: str, updates: dict):
    """pending taskのステータスを更新"""
    tasks = load_pending_tasks()
    for t in tasks:
        if t["task_id"] == task_id:
            t.update(updates)
            break
    save_pending_tasks(tasks)


def remove_pending_task(task_id: str):
    """完了/エスカレート済みタスクをpendingから除去"""
    tasks = load_pending_tasks()
    tasks = [t for t in tasks if t["task_id"] != task_id]
    save_pending_tasks(tasks)


# ============================================================
# last_seen_ts管理
# ============================================================
def get_last_seen_ts() -> str:
    if LAST_SEEN_FILE.exists():
        return LAST_SEEN_FILE.read_text(encoding="utf-8").strip()
    return "0"


def save_last_seen_ts(ts: str):
    LAST_SEEN_FILE.write_text(ts, encoding="utf-8")


# ============================================================
# タスクハンドラ
# ============================================================
def handle_test_ping(msg_data: dict) -> dict:
    """疎通テスト。即時DONE返送"""
    return {"result": "pong", "handled_at": datetime.now(timezone.utc).isoformat()}


def handle_ebay_search(msg_data: dict) -> dict:
    """ebay_auction_search.pyをsubprocessで実行し、新規候補があればキャップにebay-review TASK送信"""
    script = Path(__file__).parent / "coin_business" / "scripts" / "ebay_auction_search.py"
    if not script.exists():
        return {"error": f"Script not found: {script}"}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=600, encoding="utf-8",
            cwd=str(script.parent.parent),
        )
        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-500:] if result.stderr else "",
                "returncode": result.returncode,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out after 600s"}
    except Exception as e:
        return {"error": str(e)}

    # 結果ファイル読み込み
    matches_file = Path(__file__).parent / "coin_business" / "data" / "ebay_matches_latest.json"
    if not matches_file.exists():
        return {"error": "Results file not generated", "stdout": result.stdout[-1000:] if result.stdout else ""}

    try:
        with open(matches_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read results: {e}"}

    matches = data.get("matches", [])
    new_matches = [m for m in matches if m.get("is_new", False)]

    # 新規候補があればキャップにebay-review TASK送信
    if new_matches:
        review_msg = make_task_msg(
            from_id=get_sender(),
            to_id="cap",
            task="ebay-review",
            payload={
                "candidates": new_matches,
                "count": len(new_matches),
                "total_matches": len(matches),
                "searched_at": data.get("searched_at", ""),
            },
        )
        send_bridge_msg(review_msg)
        add_pending_task(review_msg)
        logger.info(f"ebay-review TASK送信: 新規{len(new_matches)}件 (全{len(matches)}マッチ)")

    return {
        "total_searched": data.get("total_searched", 0),
        "match_count": len(matches),
        "new_count": len(new_matches),
        "review_sent": len(new_matches) > 0,
    }


def handle_ebay_review(msg_data: dict) -> dict:
    """候補リストを受け取り、ファイルに保存 + コンソール表示"""
    payload = msg_data.get("payload", {})
    candidates = payload.get("candidates", [])
    searched_at = payload.get("searched_at", "")

    # ファイル保存
    review_data = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "searched_at": searched_at,
        "count": len(candidates),
        "candidates": candidates,
    }
    outfile = DATA_DIR / "ebay_review_candidates.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    # コンソールに候補一覧表示
    print(f"\n{'='*60}")
    print(f"eBay仕入れ候補（新規 {len(candidates)}件）")
    print(f"{'='*60}")
    for i, c in enumerate(candidates, 1):
        print(f"\n{i}. #{c.get('mgmt_no','')} | {c.get('db_line1','')} {c.get('db_grader','')} {c.get('db_grade','')}")
        print(f"   仕入上限: USD{c.get('ebay_limit_usd',0):,} ({c.get('ebay_limit_jpy',0):,}円)")
        print(f"   入札: {c.get('bid_count',0)}件")
        print(f"   URL: {c.get('ebay_url','')}")
    print(f"\n{'='*60}")
    print(f"保存先: {outfile}")
    print(f"CEO報告: python slack_bridge.py ceo-report")
    print(f"{'='*60}\n")

    return {"saved_to": str(outfile), "count": len(candidates)}


def handle_git_pull(msg_data: dict) -> dict:
    """git pullを実行"""
    repo_dir = Path(__file__).parent
    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, timeout=60,
            cwd=str(repo_dir), encoding="utf-8",
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"error": str(e)}


def handle_report(msg_data: dict) -> dict:
    """sourcing_report.txtの内容を返す"""
    report_file = Path(__file__).parent / "coin_business" / "data" / "sourcing_report.txt"
    if not report_file.exists():
        return {"error": "sourcing_report.txt not found"}
    content = report_file.read_text(encoding="utf-8")
    # 長すぎる場合は末尾を切り詰め
    if len(content) > 3000:
        content = content[:3000] + "\n... (truncated)"
    return {"report": content}


# ============================================================
# 安全ストア方式 (CTO Pattern B)
# ============================================================
SECURE_STORE_PATHS = [
    Path(__file__).parent / ".secure_env",          # 優先1: Git管理外
    Path(__file__).parent / ".env.local",            # 優先3: Git ignore
]
ALLOWED_SECURE_KEYS = {"EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_DEV_ID"}


def _load_secure_store() -> dict:
    """安全ストアから値を読み込み。優先順: .secure_env > OS環境変数 > .env.local"""
    store = {}
    # ファイルベース（低優先から読み込み、高優先で上書き）
    for path in reversed(SECURE_STORE_PATHS):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key in ALLOWED_SECURE_KEYS:
                    store[key] = val
    # OS環境変数（最高優先）
    for key in ALLOWED_SECURE_KEYS:
        env_val = os.environ.get(key)
        if env_val:
            store[key] = env_val
    return store


def handle_set_env(msg_data: dict) -> dict:
    """安全ストアからキーを取得し、coin_business/.envに反映"""
    payload = msg_data.get("payload", {})
    requested_keys = payload.get("keys", [])

    # 許可キーのみ
    rejected = [k for k in requested_keys if k not in ALLOWED_SECURE_KEYS]
    if rejected:
        return {"error": f"Rejected keys (not in allowlist): {rejected}"}

    # 安全ストアから値取得
    store = _load_secure_store()
    missing = [k for k in requested_keys if k not in store]
    if missing:
        return {"error": f"Keys not found in secure store: {missing}"}

    # coin_business/.env に反映
    env_file = Path(__file__).parent / "coin_business" / ".env"
    if not env_file.exists():
        return {"error": f".env not found: {env_file}"}

    env_content = env_file.read_text(encoding="utf-8")
    updated_keys = []

    for key in requested_keys:
        val = store[key]
        # 既存行を置換 or 追加
        import re as _re
        pattern = _re.compile(rf"^{_re.escape(key)}\s*=.*$", _re.MULTILINE)
        if pattern.search(env_content):
            env_content = pattern.sub(f"{key}={val}", env_content)
        else:
            env_content = env_content.rstrip() + f"\n{key}={val}\n"
        updated_keys.append(key)

    # atomic write
    tmp_file = env_file.with_suffix(".env.tmp")
    tmp_file.write_text(env_content, encoding="utf-8")
    tmp_file.replace(env_file)

    # ログには値を出さない（キー名のみ）
    logger.info(f"set-env完了: {updated_keys}")
    log_event("set_env", {"keys": updated_keys, "status": "updated"})

    return {"status": "updated", "keys": updated_keys}


HANDLERS = {
    "test-ping": handle_test_ping,
    "ebay-search": handle_ebay_search,
    "ebay-review": handle_ebay_review,
    "git-pull": handle_git_pull,
    "report": handle_report,
    "set-env": handle_set_env,
}


# ============================================================
# タスク処理（subprocess分離）
# ============================================================
def dispatch_task(msg_data: dict):
    """ハンドラをバックグラウンドスレッドで実行し、DONE/ERRORを返送"""
    task_name = msg_data.get("task", "")
    task_id = msg_data.get("task_id", "")

    handler = HANDLERS.get(task_name)
    if handler is None:
        logger.warning(f"未知のタスク: {task_name}")
        err_msg = make_response_msg(msg_data, "ERROR", {"error": f"Unknown task: {task_name}"})
        send_bridge_msg(err_msg)
        save_task_registry_entry(task_id, "ERROR", {"task": task_name})
        return

    def _run():
        try:
            logger.info(f"タスク実行開始: {task_name} (id={task_id[:8]})")
            result = handler(msg_data)
            done_msg = make_response_msg(msg_data, "DONE", result)
            send_bridge_msg(done_msg)
            save_task_registry_entry(task_id, "DONE", {"task": task_name})
            logger.info(f"タスク完了: {task_name} (id={task_id[:8]})")
            log_event("done", {"task_id": task_id, "task": task_name})
        except Exception as e:
            logger.error(f"タスク実行エラー: {task_name} - {e}")
            err_msg = make_response_msg(msg_data, "ERROR", {"error": str(e)})
            send_bridge_msg(err_msg)
            save_task_registry_entry(task_id, "ERROR", {"task": task_name, "error": str(e)})
            log_event("error", {"task_id": task_id, "task": task_name, "error": str(e)})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


# ============================================================
# リトライ/エスカレーション チェック
# ============================================================
def check_pending_tasks():
    """pending_tasksを確認し、タイムアウト時にリトライまたはエスカレーション"""
    tasks = load_pending_tasks()
    now = datetime.now(timezone.utc)
    changed = False

    for t in tasks:
        task_id = t["task_id"]
        status = t.get("status", "sent")
        sent_at = datetime.fromisoformat(t["sent_at"])

        if status == "sent":
            # ACK待ち: 3分タイムアウト
            elapsed = (now - sent_at).total_seconds()
            if elapsed > ACK_TIMEOUT_SEC:
                retry_count = t.get("retry_count", 0)
                if retry_count < MAX_RETRIES:
                    # 再送
                    logger.warning(f"ACKタイムアウト。再送 ({retry_count + 1}/{MAX_RETRIES}): {task_id[:8]}")
                    t["retry_count"] = retry_count + 1
                    t["sent_at"] = now.isoformat()
                    send_bridge_msg(t["msg_data"])
                    log_event("retry", {"task_id": task_id, "retry_count": t["retry_count"]})
                    changed = True
                else:
                    # エスカレーション
                    logger.error(f"3回失敗。エスカレーション: {task_id[:8]}")
                    escalate_msg = make_task_msg(
                        from_id=get_sender(),
                        to_id="ceo",
                        task=t.get("task", "unknown"),
                        msg_type="ESCALATE",
                        task_id=task_id,
                        correlation_id=t["msg_data"].get("correlation_id", ""),
                        payload={"reason": "ACK timeout after 3 attempts", "original_to": t.get("to", "")},
                    )
                    send_bridge_msg(escalate_msg, channel=CEO_ROOM_CHANNEL)
                    t["status"] = "escalated"
                    save_task_registry_entry(task_id, "ESCALATED", {"reason": "ACK timeout"})
                    log_event("escalate", {"task_id": task_id, "reason": "ACK timeout"})
                    changed = True

        elif status == "acked":
            # DONE待ち: 30分タイムアウト
            ack_at = datetime.fromisoformat(t["ack_at"])
            elapsed = (now - ack_at).total_seconds()
            if elapsed > DONE_TIMEOUT_SEC:
                logger.error(f"DONE タイムアウト(30分)。エスカレーション: {task_id[:8]}")
                escalate_msg = make_task_msg(
                    from_id=get_sender(),
                    to_id="ceo",
                    task=t.get("task", "unknown"),
                    msg_type="ESCALATE",
                    task_id=task_id,
                    correlation_id=t["msg_data"].get("correlation_id", ""),
                    payload={"reason": "DONE timeout after 30min", "original_to": t.get("to", "")},
                )
                send_bridge_msg(escalate_msg, channel=CEO_ROOM_CHANNEL)
                t["status"] = "escalated"
                save_task_registry_entry(task_id, "ESCALATED", {"reason": "DONE timeout"})
                log_event("escalate", {"task_id": task_id, "reason": "DONE timeout"})
                changed = True

    if changed:
        # escalated/completedを除去
        tasks = [t for t in tasks if t.get("status") not in ("escalated", "completed")]
        save_pending_tasks(tasks)


# ============================================================
# 監視ループ（watch）
# ============================================================
def watch_channel(interval: int = 5):
    """ai-bridgeチャンネルを定期ポーリングで監視"""
    logger.info(f"Slack監視開始 (間隔: {interval}秒, sender: {get_sender()})")

    last_seen_ts = get_last_seen_ts()
    my_sender = get_sender()
    last_retry_check = time.time()

    while True:
        try:
            # --- 1回のconversations.history ---
            messages = receive_messages(limit=10)

            if messages:
                # messagesは新しい順。逆順にして古い方から処理
                new_msgs = []
                for msg in reversed(messages):
                    if msg["ts"] > last_seen_ts:
                        new_msgs.append(msg)

                for msg in new_msgs:
                    text = msg["text"]

                    # 自分が送ったメッセージはスキップ
                    if f"[{my_sender}]" in text:
                        pass
                    else:
                        # ブリッジメッセージのパース
                        bridge_data = parse_bridge_message(text)

                        if bridge_data is not None:
                            _handle_bridge_msg(bridge_data, my_sender)
                        else:
                            # 旧形式メッセージ → latest_msg.txtに保存
                            LATEST_MSG_FILE.write_text(text, encoding="utf-8")
                            logger.info(f"旧形式メッセージ: {text[:80]}")
                            log_event("receive_legacy", {"text": text[:200]})

                # last_seen更新（最新のts）
                newest_ts = messages[0]["ts"]
                if newest_ts > last_seen_ts:
                    last_seen_ts = newest_ts
                    save_last_seen_ts(last_seen_ts)

            # --- リトライチェック（30秒ごと） ---
            now = time.time()
            if now - last_retry_check >= RETRY_CHECK_INTERVAL:
                check_pending_tasks()
                last_retry_check = now

        except Exception as e:
            logger.error(f"監視ループエラー: {e}")

        time.sleep(interval)


def _handle_bridge_msg(bridge_data: dict, my_sender: str):
    """受信したブリッジメッセージを処理"""
    msg_type = bridge_data.get("type", "")
    task_id = bridge_data.get("task_id", "")
    from_id = bridge_data.get("from", "")
    to_id = bridge_data.get("to", "")
    task_name = bridge_data.get("task", "")

    logger.info(f"受信: type={msg_type} task={task_name} from={from_id} id={task_id[:8]}")
    log_event("receive", {"type": msg_type, "task": task_name, "task_id": task_id, "from": from_id})

    # 自分宛てでなければスキップ
    if to_id and to_id != my_sender:
        logger.debug(f"自分宛てではない (to={to_id}). スキップ")
        return

    if msg_type == "TASK":
        # 重複チェック
        if is_task_processed(task_id):
            logger.info(f"重複タスク。スキップ: {task_id[:8]}")
            return

        # ACK返送（ループ防止: TASKのみACKを返す）
        ack_msg = make_response_msg(bridge_data, "ACK")
        send_bridge_msg(ack_msg)
        save_task_registry_entry(task_id, "ACK", {"task": task_name})
        log_event("ack_sent", {"task_id": task_id, "task": task_name})
        logger.info(f"ACK送信: {task_name} (id={task_id[:8]})")

        # ハンドラディスパッチ（スレッドで非同期実行）
        dispatch_task(bridge_data)

    elif msg_type == "ACK":
        # 自分が送ったタスクへのACK
        logger.info(f"ACK受信: {task_name} (id={task_id[:8]})")
        update_pending_task(task_id, {
            "status": "acked",
            "ack_at": datetime.now(timezone.utc).isoformat(),
        })
        log_event("ack_received", {"task_id": task_id})

    elif msg_type == "DONE":
        logger.info(f"DONE受信: {task_name} (id={task_id[:8]})")
        remove_pending_task(task_id)
        save_task_registry_entry(task_id, "DONE_RECEIVED", {"task": task_name})
        log_event("done_received", {"task_id": task_id, "payload": bridge_data.get("payload", {})})

    elif msg_type == "ERROR":
        logger.warning(f"ERROR受信: {task_name} (id={task_id[:8]})")
        remove_pending_task(task_id)
        save_task_registry_entry(task_id, "ERROR_RECEIVED", {"task": task_name})
        log_event("error_received", {"task_id": task_id, "payload": bridge_data.get("payload", {})})

    elif msg_type == "ESCALATE":
        logger.error(f"ESCALATE受信: {task_name} (id={task_id[:8]})")
        log_event("escalate_received", {"task_id": task_id})

    # ACK/DONE/ERROR/ESCALATEに対してACKは返さない（ループ防止）


# ============================================================
# CLIコマンド
# ============================================================
def cmd_send_task(task: str, to: str, payload: dict = None):
    """タスク送信"""
    sender = get_sender()
    msg = make_task_msg(
        from_id=sender,
        to_id=to,
        task=task,
        payload=payload or {},
    )
    success = send_bridge_msg(msg)
    if success:
        add_pending_task(msg)
        logger.info(f"タスク送信完了: {task} -> {to} (id={msg['task_id'][:8]})")
        log_event("task_sent", {"task_id": msg["task_id"], "task": task, "to": to})
    return success


def cmd_retry_pending():
    """未完了タスクを手動リトライ"""
    tasks = load_pending_tasks()
    if not tasks:
        logger.info("未完了タスクなし")
        return

    for t in tasks:
        logger.info(f"リトライ: {t['task']} (id={t['task_id'][:8]}, status={t['status']})")
        t["retry_count"] = 0
        t["sent_at"] = datetime.now(timezone.utc).isoformat()
        t["status"] = "sent"
        send_bridge_msg(t["msg_data"])
        log_event("manual_retry", {"task_id": t["task_id"]})

    save_pending_tasks(tasks)
    logger.info(f"{len(tasks)}件リトライ実行")


def cmd_status():
    """タスク状態一覧表示"""
    # Pending tasks
    pending = load_pending_tasks()
    print("\n=== Pending Tasks ===")
    if not pending:
        print("  なし")
    else:
        for t in pending:
            print(f"  [{t['status']}] {t['task']} -> {t.get('to','')} "
                  f"(id={t['task_id'][:8]}, retry={t.get('retry_count',0)})")

    # Recent registry entries (last 20)
    print("\n=== Recent Registry (last 20) ===")
    registry_entries = []
    if TASK_REGISTRY_FILE.exists():
        with open(TASK_REGISTRY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        registry_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    if not registry_entries:
        print("  なし")
    else:
        for entry in registry_entries[-20:]:
            print(f"  [{entry.get('status','')}] {entry.get('task','')} "
                  f"(id={entry['task_id'][:8]}, {entry.get('timestamp','')})")

    print()


# ============================================================
# 旧互換コマンド
# ============================================================
def cmd_ceo_report(file_path: str = None):
    """ebay_review_candidates.jsonを読み込み、#ceo-roomに承認済み候補を報告"""
    if file_path:
        candidates_file = Path(file_path)
    else:
        candidates_file = DATA_DIR / "ebay_review_candidates.json"

    if not candidates_file.exists():
        logger.error(f"候補ファイルが見つかりません: {candidates_file}")
        print(f"Error: {candidates_file} not found")
        return False

    try:
        with open(candidates_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"ファイル読み込みエラー: {e}")
        print(f"Error: {e}")
        return False

    # candidates はデータ構造に応じてリストまたはdict内のリスト
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        candidates = data.get("candidates", [])
    else:
        print("Error: unexpected data format")
        return False

    if not candidates:
        print("候補なし。報告不要。")
        return True

    # CEO報告メッセージ構築
    lines = []
    lines.append("eBay仕入れ候補（キャップ確認済み）")
    lines.append("")
    for i, c in enumerate(candidates, 1):
        mgmt = c.get("mgmt_no", "???")
        line1 = c.get("db_line1", "")
        grader = c.get("db_grader", "")
        grade = c.get("db_grade", "")
        limit_usd = c.get("ebay_limit_usd", 0)
        limit_jpy = c.get("ebay_limit_jpy", 0)
        bids = c.get("bid_count", 0)
        url = c.get("ebay_url", "")

        lines.append(f"{i}. #{mgmt} | {line1} {grader} {grade}")
        lines.append(f"   仕入上限: USD{limit_usd:,} ({limit_jpy:,}円)")
        lines.append(f"   入札: {bids}件")
        lines.append(f"   URL: {url}")
        lines.append("")

    report_text = "\n".join(lines)

    # #ceo-roomに送信
    success = send_message(report_text, sender=get_sender(), channel=CEO_ROOM_CHANNEL)
    if success:
        logger.info(f"CEO報告送信完了: {len(candidates)}件")
        print(f"CEO報告を#ceo-roomに送信しました（{len(candidates)}件）")
    else:
        logger.error("CEO報告送信失敗")
        print("Error: CEO報告送信失敗")
    return success


def read_latest():
    """最新メッセージファイルを読む（旧互換）"""
    if LATEST_MSG_FILE.exists():
        content = LATEST_MSG_FILE.read_text(encoding="utf-8")
        print(content)
        return content
    else:
        print("まだメッセージがありません")
        return ""


# ============================================================
# main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="AI間Slack連携ブリッジ v2.0")
    subparsers = parser.add_subparsers(dest="command")

    # watch
    watch_p = subparsers.add_parser("watch", help="常時監視モード")
    watch_p.add_argument("--interval", type=int, default=5, help="監視間隔(秒)")

    # send-task
    st_p = subparsers.add_parser("send-task", help="タスク送信")
    st_p.add_argument("--task", required=True, help="タスク名")
    st_p.add_argument("--to", required=True, help="送信先ID (cap/cyber)")
    st_p.add_argument("--payload", default=None, help="JSON payload")

    # retry-pending
    subparsers.add_parser("retry-pending", help="未完了タスクのリトライ")

    # status
    subparsers.add_parser("status", help="タスク状態一覧")

    # set-sender
    ss_p = subparsers.add_parser("set-sender", help="送信者IDを設定")
    ss_p.add_argument("sender_id", help="送信者ID (cap/cyber)")

    # ceo-report
    ceo_p = subparsers.add_parser("ceo-report", help="eBay候補をCEO報告")
    ceo_p.add_argument("--file", default=None, help="候補JSONファイルパス（省略時はデフォルト）")

    # --- 旧互換コマンド ---
    send_p = subparsers.add_parser("send", help="メッセージ送信（旧互換）")
    send_p.add_argument("message", help="送信するメッセージ")
    send_p.add_argument("--sender", help="送信者ID")

    subparsers.add_parser("receive", help="最新メッセージ取得（旧互換）")
    subparsers.add_parser("read", help="最新メッセージファイルを読む（旧互換）")

    args = parser.parse_args()

    if args.command == "watch":
        watch_channel(interval=args.interval)
    elif args.command == "send-task":
        payload = json.loads(args.payload) if args.payload else None
        cmd_send_task(task=args.task, to=args.to, payload=payload)
    elif args.command == "retry-pending":
        cmd_retry_pending()
    elif args.command == "status":
        cmd_status()
    elif args.command == "set-sender":
        set_sender(args.sender_id)
    elif args.command == "ceo-report":
        cmd_ceo_report(file_path=args.file)
    elif args.command == "send":
        send_message(args.message, sender=args.sender)
    elif args.command == "receive":
        messages = receive_messages()
        for msg in messages:
            print(f"  {msg['text'][:100]}")
    elif args.command == "read":
        read_latest()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
