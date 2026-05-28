#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LIKE Watchdog - 15分ごとに実行し、LIKEセッションが stuck していないか監視・復旧する。

設計:
  1. heartbeat_like.json の phase が "shutdown" でない かつ age > STALE_THRESHOLD_MIN
     → セッションが stuck と判定 → VM HTTP /run で新セッション強制起動
  2. 新セッション起動時の BrowserManagerV6._cleanup_locks() が
     孤立 Chrome を kill + SingletonLock を削除 → クリーン起動
  3. 復旧成功 → 10分後に heartbeat を再確認
  4. 依然 stuck → Slack ALARM

動機 (2026-05-28 19:10 hang事案):
  - like_executor の route handler が login.account.rakuten.com (.com ドメイン) を誤 abort
  - feed 5 で 17分+ stuck → 19:20 FB セッションも巻き添え
  - 今回の hostname ベース修正で根本解決済み
  - 本 watchdog は同種の将来 hang に対する safety net

Windows Task Scheduler: 15分ごと (00/15/30/45分) に実行
"""
from __future__ import annotations
import io, sys, json, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT           = Path(__file__).resolve().parents[1]
HEARTBEAT_PATH = ROOT / "rakuten-room" / "bot" / "executor" / "heartbeat_like.json"
WATCHDOG_LOG   = ROOT / "ops" / "like_watchdog.log"

VM_BASE  = "http://localhost:18765"
VM_TOKEN = "rakuten-room-v6-secret"

STALE_THRESHOLD_MIN = 15    # 15分以上 phase != "shutdown" → stuck 判定
VERIFY_WAIT_SEC     = 180   # 復旧後 3分待機して再確認


def wlog(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_heartbeat() -> dict:
    """heartbeat_like.json を読んで dict を返す。失敗時は {} を返す。"""
    try:
        return json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        wlog(f"[WARN] heartbeat 読み込み失敗: {e}")
        return {}


def get_heartbeat_age_sec(hb: dict) -> float:
    """heartbeat の ts から現在までの経過秒を返す。ts が不明なら 99999。"""
    ts_str = hb.get("ts")
    if not ts_str:
        return 99999.0
    try:
        ts = datetime.fromisoformat(ts_str[:19])
        return (datetime.now() - ts).total_seconds()
    except Exception:
        return 99999.0


def is_stuck(hb: dict, age_sec: float) -> bool:
    """セッションが stuck かどうかを判定する。"""
    phase = hb.get("phase", "")
    # shutdown = 正常終了 → not stuck
    if phase == "shutdown":
        return False
    # phase が不明 or startup/running で STALE_THRESHOLD 超過 → stuck
    if age_sec >= STALE_THRESHOLD_MIN * 60:
        return True
    return False


def slack_alarm(msg: str) -> None:
    """Slack に ALARM を送信する。"""
    # ops/notifications/slack_reporter.py を再利用
    reporter = ROOT / "ops" / "notifications" / "slack_reporter.py"
    if not reporter.exists():
        wlog("[WARN] slack_reporter.py が見つからない")
        return
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(reporter), msg],
            capture_output=True, timeout=30
        )
        if r.returncode != 0:
            wlog(f"[WARN] Slack 送信失敗 rc={r.returncode}: {r.stderr[:200]}")
    except Exception as e:
        wlog(f"[WARN] Slack 送信例外: {e}")


def trigger_like_session(limit: int = 100) -> bool:
    """VM HTTP /run に LIKE セッション起動リクエストを送る。成功なら True。"""
    payload = json.dumps({"mode": "like", "limit": limit}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{VM_BASE}/run",
            data=payload,
            headers={
                "Authorization": f"Bearer {VM_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        status = result.get("status", "")
        wlog(f"[VM HTTP /run] response: {result}")
        return status in ("launched", "already_running")
    except urllib.error.HTTPError as e:
        body = e.read()[:300] if hasattr(e, "read") else b""
        wlog(f"[ERROR] VM HTTP /run HTTPError {e.code}: {body}")
        return False
    except Exception as e:
        wlog(f"[ERROR] VM HTTP /run 失敗: {e}")
        return False


def main() -> None:
    wlog("=== like_watchdog start ===")

    # heartbeat ファイル存在チェック (初回・ファイル欠損 → false positive 防止)
    if not HEARTBEAT_PATH.exists():
        wlog(f"[OK] heartbeat ファイル未存在 ({HEARTBEAT_PATH.name}) → skip")
        return

    hb = read_heartbeat()
    if not hb:
        wlog("[OK] heartbeat 読み込み失敗 → skip (false positive 防止)")
        return

    age_sec = get_heartbeat_age_sec(hb)
    age_min = age_sec / 60
    phase = hb.get("phase", "N/A")
    pid   = hb.get("pid", "N/A")

    wlog(f"heartbeat: phase={phase} age={age_min:.1f}min pid={pid} ts={hb.get('ts','?')}")

    if not is_stuck(hb, age_sec):
        wlog(f"[OK] stuck なし (phase={phase}, age={age_min:.1f}min < {STALE_THRESHOLD_MIN}min または shutdown)")
        return

    # ── STUCK 検出 ──────────────────────────────────────────────────────────────
    wlog(f"[STUCK] LIKE セッション stuck 検出: phase={phase}, age={age_min:.1f}min")

    # 復旧試行: VM HTTP で新セッション起動
    wlog("[RECOVER] VM HTTP /run で LIKE セッション再起動...")
    success = trigger_like_session(limit=100)

    if not success:
        wlog("[ALARM] VM HTTP 復旧失敗 → Slack ALARM")
        slack_alarm(
            f"<!channel> 【like_watchdog ALARM】LIKE stuck (phase={phase}, age={age_min:.1f}min) + VM HTTP 復旧失敗。手動確認要。"
        )
        return

    wlog(f"[RECOVER] 起動リクエスト送信完了 → {VERIFY_WAIT_SEC}秒後に再確認...")
    time.sleep(VERIFY_WAIT_SEC)

    # ── 復旧後の検証 ────────────────────────────────────────────────────────────
    hb2    = read_heartbeat()
    age2   = get_heartbeat_age_sec(hb2)
    phase2 = hb2.get("phase", "N/A")
    wlog(f"[VERIFY] post-recovery: phase={phase2} age={age2/60:.1f}min")

    if not is_stuck(hb2, age2):
        wlog("[RECOVER OK] LIKE セッション復旧確認")
        slack_alarm(
            f"【like_watchdog 復旧完了】LIKE stuck (age={age_min:.1f}min) → 新セッション正常起動。"
        )
    else:
        wlog("[ALARM] 復旧後も stuck → Slack ALARM")
        slack_alarm(
            f"<!channel> 【like_watchdog ALARM】LIKE 依然 stuck (phase={phase2}, age={age2/60:.1f}min)。手動確認要。"
        )


if __name__ == "__main__":
    main()
