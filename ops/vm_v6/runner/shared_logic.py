#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 共通モジュール: heartbeat / rate_limit / fail_reason / session_log

Plan v4 P1 (VB 完結化) で 4機能 (post/like/follow/followback) が共有する基盤。

【共通機能】
- HeartbeatPusher: heartbeat の atomic write + HOST への HTTP push
- RateLimitDetector: DOM ベース rate_limit 検知 (色判定 NG)
- FailReasonClassifier: shared/fail_reason_taxonomy.py を活用
- SessionLogger: action 別 log file への append
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


# 環境判定: VM 内 vs HOST 側
def is_vm_env() -> bool:
    """VM 内で実行されているか判定."""
    return os.environ.get("USERNAME", "").lower() == "cyber"


# パス決定
if is_vm_env():
    BASE_DIR = Path(r"C:\Users\cyber\Desktop\rakuten_room_bot")
else:
    BASE_DIR = Path(r"C:\Users\infoa\Documents\solarworks-ai\ops\vm_v6")

DATA_DIR = BASE_DIR / "data" if is_vm_env() else (Path(__file__).resolve().parents[2] / "rakuten-room" / "bot" / "data")
LOG_DIR = BASE_DIR / "logs" if is_vm_env() else (Path(__file__).resolve().parents[2] / "ops" / "vm_v6" / "logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HeartbeatPusher
# ============================================================

class HeartbeatPusher:
    """heartbeat 書き込み + HOST へ HTTP push.

    schema_version=2 (shared/heartbeat_writer.py と互換).
    30秒スロットル。force=True で即時書き込み。
    """

    DEFAULT_THROTTLE = 30
    HOST_WEBHOOK_URL = "http://10.0.2.2:18766/heartbeat"  # VirtualBox NAT で HOST へ

    def __init__(self, action: str, throttle_sec: int = None):
        self.action = action
        self.throttle = throttle_sec or self.DEFAULT_THROTTLE
        self._last_write = 0.0

        # 書き込み先
        if is_vm_env():
            self.local_path = BASE_DIR / "data" / f"heartbeat_{action}.json"
            self.share_path = Path(r"\\VBOXSVR\share") / f"heartbeat_{action}.json"
        else:
            # HOST 側: テスト時のフォールバック
            self.local_path = LOG_DIR / f"heartbeat_{action}.json"
            self.share_path = None
        self.local_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, phase: str = "running", current_target: str = "",
              success: int = 0, fail: int = 0, skip: int = 0,
              extra: dict = None, force: bool = False) -> bool:
        now = time.time()
        if not force and (now - self._last_write) < self.throttle:
            return False

        payload = {
            "schema_version": 2,
            "ts": datetime.now().isoformat(),
            "pid": os.getpid(),
            "action": self.action,
            "phase": phase,
            "current_target": current_target,
            "metrics": {
                "success_count": int(success),
                "fail_count": int(fail),
                "skip_count": int(skip),
            },
            "extra": extra or {},
        }
        json_str = json.dumps(payload, ensure_ascii=False)

        # local atomic write
        try:
            tmp = self.local_path.with_suffix(".tmp")
            tmp.write_text(json_str, encoding="utf-8")
            tmp.replace(self.local_path)
        except Exception:
            return False

        # share folder write (VM only)
        if self.share_path is not None:
            try:
                tmp_s = self.share_path.with_suffix(".tmp")
                tmp_s.write_text(json_str, encoding="utf-8")
                tmp_s.replace(self.share_path)
            except Exception:
                pass

        # HTTP push to HOST (VM only, optional)
        if is_vm_env():
            try:
                import requests
                requests.post(self.HOST_WEBHOOK_URL, json=payload, timeout=2)
            except Exception:
                pass  # webhook 失敗は致命的ではない

        self._last_write = now
        return True


# ============================================================
# RateLimitDetector (DOM ベース)
# ============================================================

class RateLimitDetector:
    """楽天 ROOM の rate_limit を DOM 検知.

    旧来の色判定 (is_pink) ではなく、DOM テキストとセレクタで判定。
    誤検知率を大幅削減。
    """

    RATE_LIMIT_TEXT = "ご利用上限数に達しています"

    def is_rate_limited(self, page) -> bool:
        """Playwright page で rate_limit 判定."""
        try:
            # 1. テキストベース
            if page.locator(f"text={self.RATE_LIMIT_TEXT}").count() > 0:
                return True
            # 2. URL リダイレクト判定
            if "ratelimit" in page.url or "limit_reached" in page.url:
                return True
            # 3. modal セレクタ (将来の楽天 UI 変更対応用)
            if page.locator("[data-rate-limit-modal]").count() > 0:
                return True
        except Exception:
            pass
        return False


# ============================================================
# FailReasonClassifier
# ============================================================

class FailReasonClassifier:
    """fail_reason taxonomy (shared/fail_reason_taxonomy.py) のラッパ."""

    def __init__(self):
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR.parent.parent if not is_vm_env() else BASE_DIR.parent))
            from shared.fail_reason_taxonomy import classify
            self._classify = classify
        except Exception:
            self._classify = lambda r: "unknown"

    def classify(self, reason: str) -> str:
        return self._classify(reason)


# ============================================================
# SessionLogger
# ============================================================

class SessionLogger:
    """action 別 log file へ append (debug 用)."""

    def __init__(self, action: str):
        self.action = action
        self.log_file = LOG_DIR / f"{action}_session.log"

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        print(line, end="", flush=True)


# ============================================================
# 自己テスト
# ============================================================

if __name__ == "__main__":
    print(f"is_vm_env: {is_vm_env()}")
    print(f"BASE_DIR: {BASE_DIR}")
    print(f"LOG_DIR: {LOG_DIR}")
    hb = HeartbeatPusher("test")
    print(f"write1: {hb.write(phase='startup', force=True)}")
    print(f"write2 (throttle): {hb.write(phase='running', success=5)}")
    log = SessionLogger("test")
    log.log("self-test ok")
