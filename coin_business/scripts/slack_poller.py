"""
coin_business/scripts/slack_poller.py  (Phase3 / 1分監視)
===========================================================
#coin-cap-marke の自動ポーリング。

機能:
  - 1分毎に新着メッセージを検出 (Phase3)
  - :eyes: リアクション + スレッドACK (1分SLA)
  - 初回返信予定: 5分以内
  - processed_tsセットで重複防止
  - ACK後にlast_ts更新 (Phase0ホットフィックス)
  - ACK失敗時 error_status 記録
  - 5分毎に Slack実件数とInbox件数を突合
  - NO-WAIT: 新着がなければ AUTONOMOUS_BACKLOG へ遷移
  - heartbeat停止時 Slack警告通知

タスクスケジューラ登録（1分毎）:
  schtasks /create /tn CoinSlackPoller1min /tr "python.exe -X utf8 C:\\...\\slack_poller.py" /sc minute /mo 1 /f

手動実行:
    cd coin_business
    python scripts/slack_poller.py
    python scripts/slack_poller.py --watchdog
    python scripts/slack_poller.py --rescan    # 直近100件強制再走査
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# .env 読み込み
for _env in [Path(__file__).parent.parent / ".env",
             Path(__file__).parent.parent.parent / ".env"]:
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
COIN_CHANNEL    = os.environ.get("SLACK_COIN_CHANNEL", "C0AMLJU2GRW")
BOT_USER_ID     = "U0AMM2M9Y48"
STATE_FILE      = Path(__file__).parent.parent / "data" / "slack_poll_state.json"
INBOX_LOG       = Path(__file__).parent.parent / "data" / "slack_inbox_log.jsonl"
HEARTBEAT_LOG   = Path(__file__).parent.parent / "data" / "slack_heartbeat.log"
PROCESSED_FILE  = Path(__file__).parent.parent / "data" / "slack_processed_ts.json"

for p in [STATE_FILE.parent]:
    p.mkdir(exist_ok=True)

# ────────────────────────────────────────────────────────────────
# 種別判定
# ────────────────────────────────────────────────────────────────

_KIND_RULES = [
    ("差し戻し",   re.compile(r"差し?戻|NG|修正|やり直|リテイク", re.I)),
    ("完了確認",   re.compile(r"完了|確認しました|確認済|問題ない|ありがとう|おｋ|ok", re.I)),
    ("確認依頼",   re.compile(r"確認して|見て|教えて|どう|どうなって|\?|？", re.I)),
    ("依頼",       re.compile(r"してください|お願い|実装|作成|更新|修正|追加|対応|着手|進め|指示", re.I)),
]

def classify(text: str) -> str:
    for kind, pat in _KIND_RULES:
        if pat.search(text):
            return kind
    return "要手動確認"


# ────────────────────────────────────────────────────────────────
# task_id 採番
# ────────────────────────────────────────────────────────────────

def next_task_id() -> str:
    n = 0
    if INBOX_LOG.exists():
        with INBOX_LOG.open(encoding="utf-8") as f:
            n = sum(1 for _ in f)
    return f"TASK-{n+1:04d}"


# ────────────────────────────────────────────────────────────────
# Slack API
# ────────────────────────────────────────────────────────────────

def _api_get(path: str, params: dict | None = None) -> dict:
    url = f"https://slack.com/api/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _api_post(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{endpoint}", data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def send_thread_reply(ts: str, text: str) -> bool:
    res = _api_post("chat.postMessage", {
        "channel": COIN_CHANNEL,
        "thread_ts": ts,
        "text": text,
    })
    return res.get("ok", False)


def add_reaction(ts: str, emoji: str = "eyes") -> bool:
    res = _api_post("reactions.add", {
        "channel": COIN_CHANNEL,
        "timestamp": ts,
        "name": emoji,
    })
    return res.get("ok", False)


# ────────────────────────────────────────────────────────────────
# 処理済みTS管理（重複防止）
# ────────────────────────────────────────────────────────────────

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
            return set(data.get("ts_list", []))
        except Exception:
            pass
    return set()


def save_processed(ts_set: set) -> None:
    # 最新1000件のみ保持（古いものは削除）
    ts_list = sorted(ts_set)[-1000:]
    PROCESSED_FILE.write_text(
        json.dumps({"ts_list": ts_list}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ────────────────────────────────────────────────────────────────
# 状態管理
# ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_ts": "0",
        "last_checked_at": "",
        "heartbeat_at": "",
        "total_ack": 0,
        "total_fail": 0,
        "last_reconcile_at": "",
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def log_inbox(entry: dict) -> None:
    with INBOX_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_heartbeat(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with HEARTBEAT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")


# ────────────────────────────────────────────────────────────────
# AUTONOMOUS_BACKLOG（NO-WAIT運用）
# ────────────────────────────────────────────────────────────────

AUTONOMOUS_BACKLOG = [
    {"id": "BL-A", "action": "マーケ待ちコイン圧縮一覧更新",       "owner": "cap", "priority": 1},
    {"id": "BL-B", "action": "CEO_FASTLANE並び替え・要約強化",      "owner": "cap", "priority": 2},
    {"id": "BL-C", "action": "NG候補への理由コード付与",             "owner": "cap", "priority": 3},
    {"id": "BL-D", "action": "INVESTIGATION→MARKETING_REVIEW昇格候補抽出", "owner": "cap", "priority": 4},
    {"id": "BL-E", "action": "PRICE_NEEDED更新準備",                "owner": "cap", "priority": 5},
    {"id": "BL-F", "action": "SALES_PIPELINE先行整備",              "owner": "cap", "priority": 6},
    {"id": "BL-G", "action": "SLA超過案件の洗い出し",               "owner": "cap", "priority": 7},
]


def get_next_backlog(state: dict) -> dict | None:
    last_bl = state.get("last_backlog_id", "")
    ids = [b["id"] for b in AUTONOMOUS_BACKLOG]
    if last_bl in ids:
        idx = (ids.index(last_bl) + 1) % len(AUTONOMOUS_BACKLOG)
    else:
        idx = 0
    return AUTONOMOUS_BACKLOG[idx]


# ────────────────────────────────────────────────────────────────
# 突合チェック（5分毎）
# ────────────────────────────────────────────────────────────────

def reconcile(state: dict) -> None:
    """Slack実件数とInbox件数を突合してズレを記録"""
    now = datetime.now(timezone.utc)
    last_rec = state.get("last_reconcile_at", "")
    if last_rec:
        try:
            elapsed = (now - datetime.fromisoformat(last_rec)).total_seconds()
            if elapsed < 290:  # 5分未満はスキップ
                return
        except Exception:
            pass

    # Slack実件数（直近100件から人間投稿だけカウント）
    try:
        res = _api_get("conversations.history", {
            "channel": COIN_CHANNEL,
            "limit": 100,
        })
        slack_human = [
            m for m in res.get("messages", [])
            if m.get("user") and m.get("user") != BOT_USER_ID
        ]
        slack_count = len(slack_human)
    except Exception:
        slack_count = -1

    # Inbox件数
    inbox_count = 0
    if INBOX_LOG.exists():
        with INBOX_LOG.open(encoding="utf-8") as f:
            inbox_count = sum(1 for _ in f)

    diff = slack_count - inbox_count if slack_count >= 0 else 0
    msg = f"reconcile | slack_human(100)={slack_count} inbox_total={inbox_count} diff={diff}"
    log_heartbeat(msg)
    state["last_reconcile_at"] = now.isoformat()

    if diff > 0:
        warn = f"⚠️ 突合ズレ: Slack={slack_count} > Inbox={inbox_count} (diff={diff})"
        log_heartbeat(warn)
        print(warn)


# ────────────────────────────────────────────────────────────────
# メイン: ポーリング
# ────────────────────────────────────────────────────────────────

def poll(rescan: bool = False) -> int:
    if not SLACK_BOT_TOKEN:
        print("[slack_poller] SLACK_BOT_TOKEN 未設定", file=sys.stderr)
        return 0

    state      = load_state()
    processed  = load_processed()
    last_ts    = "0" if rescan else state.get("last_ts", "0")
    now_utc    = datetime.now(timezone.utc)
    now_iso    = now_utc.isoformat()
    ack_ok     = 0
    ack_fail   = 0

    # 履歴取得（oldest指定で見逃し防止、limit=100）
    # rescanでも24時間以内に限定（古い誤ACK防止）
    if rescan:
        oldest_rescan = str(float((now_utc - timedelta(hours=24)).timestamp()))
        params = {"channel": COIN_CHANNEL, "limit": 100, "oldest": oldest_rescan}
    else:
        params = {"channel": COIN_CHANNEL, "limit": 100}
        if last_ts != "0":
            params["oldest"] = last_ts

    res = _api_get("conversations.history", params)
    if not res.get("ok"):
        print(f"[slack_poller] API error: {res.get('error')}", file=sys.stderr)
        log_heartbeat(f"API error: {res.get('error')}")
        return 0

    messages = res.get("messages", [])

    # 人間投稿かつ未処理のもの
    new_human = [
        m for m in messages
        if m.get("user") and m.get("user") != BOT_USER_ID
        and m.get("ts") not in processed
        and float(m.get("ts", "0")) > float(last_ts)
    ]

    if new_human:
        print(f"[slack_poller] 新着 {len(new_human)}件")
        for m in reversed(new_human):  # 古い順に処理
            ts    = m.get("ts", "")
            text  = m.get("text", "")
            kind  = classify(text)
            tid   = next_task_id()
            # 初回返信予定 = 5分以内 (Phase3 SLA)
            eta   = (now_utc + timedelta(minutes=5)).strftime("%H:%M UTC")

            print(f"  [{ts}] kind={kind} tid={tid} text={text[:60]}")

            # :eyes: リアクション
            add_reaction(ts, "eyes")

            # スレッドACK
            reply = (
                f"【キャップ⇒マーケ】受領済み\n"
                f"task_id={tid}\n"
                f"種別={kind}\n"
                f"初回報告予定={eta}"
            )
            ok = send_thread_reply(ts, reply)
            if ok:
                ack_ok += 1
                # ACK成功後にprocessed追加・last_ts更新（Phase0ホットフィックス）
                processed.add(ts)
                if float(ts) > float(state.get("last_ts", "0")):
                    state["last_ts"] = ts
                print(f"  → ACK ok (last_ts={ts})")
            else:
                ack_fail += 1
                print(f"  → ACK FAIL")

            # INBOX_LOGに記録
            entry = {
                "task_id":        tid,
                "message_ts":     ts,
                "thread_ts":      ts,
                "sender":         m.get("user", ""),
                "kind":           kind,
                "text_preview":   text[:100],
                "received_at":    now_iso,
                "first_reply_at": now_iso if ok else "",
                "status":         "受領済み" if ok else "ACK_FAIL",
                "error_status":   "" if ok else "ACK_FAIL",
                "evidence_url":   "",
                "completed_at":   "",
            }
            log_inbox(entry)

    else:
        print("[slack_poller] 新着なし → AUTONOMOUS_BACKLOG遷移")
        next_bl = get_next_backlog(state)
        if next_bl:
            state["last_backlog_id"]        = next_bl["id"]
            state["active_backlog_action"]  = next_bl["action"]
            state["no_wait_flag"]           = True
            print(f"  次のバックログ: [{next_bl['id']}] {next_bl['action']}")

    # 突合チェック（5分毎）
    reconcile(state)

    # 統計・heartbeat更新
    state["last_checked_at"] = now_iso
    state["heartbeat_at"]    = now_iso
    state["total_ack"]       = state.get("total_ack", 0) + ack_ok
    state["total_fail"]      = state.get("total_fail", 0) + ack_fail
    save_state(state)
    save_processed(processed)

    log_heartbeat(
        f"poll ok | new={len(new_human)} ack={ack_ok} fail={ack_fail} "
        f"last_ts={state['last_ts'][:10]} total_ack={state['total_ack']}"
    )
    return len(new_human)


# ────────────────────────────────────────────────────────────────
# 停止検知
# ────────────────────────────────────────────────────────────────

def check_watchdog() -> bool:
    state = load_state()
    last  = state.get("last_checked_at", "")
    if not last:
        return True
    try:
        last_dt  = datetime.fromisoformat(last)
        elapsed  = (datetime.now(timezone.utc) - last_dt).total_seconds()
        if elapsed > 7200:
            stopped_min = int(elapsed // 60)
            msg = f"⚠️ ポーラー停止検知: {stopped_min}分間停止中 (最終={last})"
            print(msg)
            log_heartbeat(msg)
            # Slack通知
            try:
                _api_post("chat.postMessage", {
                    "channel": COIN_CHANNEL,
                    "text": msg + "\n再起動またはTask Scheduler確認が必要です",
                })
            except Exception:
                pass
            return False
    except Exception:
        pass
    return True


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchdog", action="store_true", help="停止検知モード")
    parser.add_argument("--rescan",   action="store_true", help="直近100件強制再走査")
    args = parser.parse_args()

    if args.watchdog:
        ok = check_watchdog()
        sys.exit(0 if ok else 1)
    else:
        count = poll(rescan=args.rescan)
        print(f"[slack_poller] 完了: new={count} state={STATE_FILE.name}")
