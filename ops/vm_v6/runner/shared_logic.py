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
import shutil
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
# Emergency Disk Cleanup (2026-05-26 added)
# VM disk fills up → Chrome subprocess EPIPE → executor failure.
# Each v6 executor calls this at startup to keep free space > 500MB.
# ============================================================

def emergency_disk_cleanup_once(force_below_mb: int = 500) -> int:
    """VM disk が free_mb 以下なら緊急 cleanup. 24h で 1 回・500MB 未満時は強制.

    Returns: freed bytes (skip 時 0)
    """
    if not is_vm_env():
        return 0  # VM 以外では何もしない
    flag = Path(r"C:\Users\cyber\AppData\Local\Temp\_emer_cleanup_done")
    try:
        free_mb = shutil.disk_usage("C:\\").free / 1024 / 1024
    except Exception:
        free_mb = 9999
    try:
        if flag.exists() and free_mb > force_below_mb:
            return 0  # 500MB 以上 free あれば skip
        if flag.exists() and (time.time() - flag.stat().st_mtime) < 3600:
            if free_mb > 200:
                return 0  # 1h 以内かつ 200MB 以上ならskip
    except Exception:
        return 0
    user_root = Path(os.environ.get("USERPROFILE", r"C:\Users\cyber"))
    targets = [
        Path(os.environ.get("TEMP", r"C:\Windows\Temp")),
        user_root / "AppData" / "Local" / "Temp",
        user_root / "AppData" / "Local" / "pip" / "cache",
        user_root / "AppData" / "Local" / "Microsoft" / "Windows" / "INetCache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_post" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_post" / "Default" / "Code Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_like" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_like" / "Default" / "Code Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_follow" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_followback" / "Default" / "Cache",
        user_root / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
    ]
    cleaned = 0
    for d in targets:
        if not d.exists():
            continue
        try:
            for item in d.iterdir():
                try:
                    if item.is_dir():
                        for f in item.rglob("*"):
                            if f.is_file():
                                try:
                                    cleaned += f.stat().st_size
                                except Exception:
                                    pass
                        shutil.rmtree(item, ignore_errors=True)
                    elif item.is_file():
                        try:
                            cleaned += item.stat().st_size
                        except Exception:
                            pass
                        try:
                            item.unlink()
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            continue
    # shared folder ce_*.log の古いもの削除 (旧 comment_edit logs)
    try:
        share = Path(r"\\vboxsvr\vm_data")
        if share.exists():
            ce_logs = sorted(share.glob("ce_*.log"), key=lambda p: p.stat().st_mtime)
            for old in ce_logs[:-3]:
                try:
                    cleaned += old.stat().st_size
                    old.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    except Exception:
        pass
    print(f"[disk_cleanup] freed ~{cleaned/1024/1024:.1f} MB (was free={free_mb:.0f}MB)")
    return cleaned


# ============================================================
# Credential loader (Plan v6 Phase A-3)
# ============================================================

# .env_vm の場所:
#   VM 内: C:\Users\cyber\Desktop\rakuten_room_bot\data\.env_vm
#   HOST: rakuten-room/bot/data/.env_vm  (VBox shared folder vm_data 経由 VM が読む)
ENV_VM_PATH = DATA_DIR / ".env_vm"

_env_vm_cache: dict[str, str] | None = None


def _load_env_vm() -> dict[str, str]:
    """data/.env_vm を読んで dict で返す (簡易 dotenv)。

    Format:
        # comment
        KEY=value
        KEY2="value with spaces"
    """
    global _env_vm_cache
    if _env_vm_cache is not None:
        return _env_vm_cache
    out: dict[str, str] = {}
    if ENV_VM_PATH.exists():
        try:
            for line in ENV_VM_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                out[k] = v
        except Exception:
            pass
    _env_vm_cache = out
    return out


def get_credential(key: str, default: str = "") -> str:
    """credential 取得: 環境変数 > .env_vm の順.

    例: get_credential('RAKUTEN_LOGIN_PASSWORD')
    """
    val = os.environ.get(key)
    if val:
        return val
    return _load_env_vm().get(key, default)


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
