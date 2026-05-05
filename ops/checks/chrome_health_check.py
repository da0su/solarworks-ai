#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase C-3: Chrome profile health check (preflight)

【目的】
Bot 起動前に Chrome profile (post/like/followback) の健全性を事前確認し、
ログイン失効・プロファイル破損を bot 実行前に検知する。

【背景】
Phase 2 で「ログイン失効を bot 実行中に検知」する仕組みは作ったが、
bot 起動前に検知できていれば 30 分の無駄な実行が防げる。
さらに 2026-03-25 のような profile lock collision 問題を起動時に発見できる。

【check 項目】
1. profile dir が存在する
2. Default/Network/Cookies が存在する (cookie ファイル)
3. SingletonLock が残置していない (前回終了時の残骸)
4. profile dir のサイズが正常範囲 (10MB 〜 5GB)
5. (オプション) Cookies ファイルのタイムスタンプが新しい (90日以内)

実行: python ops/checks/chrome_health_check.py [--action post|like|followback|follow]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "rakuten-room" / "bot"))

import config  # type: ignore  # rakuten-room/bot/config.py


# ==================================================
# health check
# ==================================================

class HealthCheckResult:
    def __init__(self, action: str):
        self.action = action
        self.passed: list[str] = []
        self.failed: list[dict] = []
        self.warnings: list[dict] = []

    def ok(self, name: str):
        self.passed.append(name)

    def fail(self, name: str, detail: str):
        self.failed.append({"check": name, "detail": detail})

    def warn(self, name: str, detail: str):
        self.warnings.append({"check": name, "detail": detail})

    @property
    def is_healthy(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "is_healthy": self.is_healthy,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "checked_at": datetime.now().isoformat(),
        }

    def print_summary(self):
        sym = "OK" if self.is_healthy else "NG"
        print(f"\n[{sym}] Chrome health check [{self.action}]")
        print(f"  Passed: {len(self.passed)}")
        print(f"  Warnings: {len(self.warnings)}")
        print(f"  Failed: {len(self.failed)}")
        for w in self.warnings:
            print(f"  [WARN] {w['check']}: {w['detail']}")
        for f in self.failed:
            print(f"  [FAIL] {f['check']}: {f['detail']}")


def check_profile(action: str) -> HealthCheckResult:
    """指定 action の Chrome profile を健全性チェック."""
    res = HealthCheckResult(action)
    profile_path = config.get_chrome_profile(action)

    # 1. profile dir 存在確認
    if not profile_path.exists():
        res.fail("profile_exists", f"profile dir not found: {profile_path}")
        return res
    res.ok("profile_exists")

    # 2. Cookies ファイル存在
    cookies_paths = [
        profile_path / "Default" / "Network" / "Cookies",   # Modern Chrome
        profile_path / "Default" / "Cookies",               # Legacy
    ]
    cookies_found = next((p for p in cookies_paths if p.exists()), None)
    if not cookies_found:
        res.fail("cookies_exist", f"no Cookies file in {profile_path}/Default")
        return res
    res.ok("cookies_exist")

    # 3. SingletonLock 残置チェック (前回終了時の残骸 = SingletonLock 残ると Chrome起動失敗)
    lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
    lock_found = []
    for lf in lock_files:
        lp = profile_path / lf
        if lp.exists():
            try:
                age_sec = time.time() - lp.stat().st_mtime
                lock_found.append(f"{lf}({age_sec:.0f}s)")
            except Exception:
                lock_found.append(lf)
    if lock_found:
        # 残置 lock は WARN (browser_manager._cleanup_profile_locks() が起動時に削除する)
        res.warn("no_stale_lock", f"残置 lock 検出: {', '.join(lock_found)} (起動時に自動削除)")
    else:
        res.ok("no_stale_lock")

    # 4. profile size が正常範囲か
    try:
        total_size = sum(
            p.stat().st_size for p in profile_path.rglob("*") if p.is_file()
        )
        size_mb = total_size / (1024 * 1024)
        if size_mb < 10:
            res.fail("profile_size", f"profile too small: {size_mb:.1f}MB (expected >=10MB)")
        elif size_mb > 5000:
            res.warn("profile_size", f"profile too large: {size_mb:.1f}MB (Chrome cleanup 推奨)")
        else:
            res.ok("profile_size")
    except Exception as e:
        res.warn("profile_size", f"size check failed: {e}")

    # 5. Cookies ファイル の修正日時 (90日以内)
    try:
        c_mtime = datetime.fromtimestamp(cookies_found.stat().st_mtime)
        c_age_days = (datetime.now() - c_mtime).total_seconds() / 86400
        if c_age_days > 90:
            res.warn("cookies_freshness",
                     f"Cookies last modified {c_age_days:.0f}d ago (>90d) - re-login may be needed")
        else:
            res.ok("cookies_freshness")
    except Exception:
        res.warn("cookies_freshness", "mtime check failed")

    # 6. SQLite Cookies file の正常性 (sqlite として開けるか)
    try:
        c = sqlite3.connect(f"file:{cookies_found}?mode=ro", uri=True, timeout=2)
        n = c.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
        c.close()
        if n == 0:
            res.fail("cookies_db", "Cookies DB is empty (logged out)")
        elif n < 5:
            res.warn("cookies_db", f"only {n} cookies (suspicious)")
        else:
            res.ok("cookies_db")

        # 楽天 ROOM のセッションcookie (Rses/Raut) 存在確認
        c = sqlite3.connect(f"file:{cookies_found}?mode=ro", uri=True, timeout=2)
        rakuten_cookies = c.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%rakuten%'"
        ).fetchall()
        c.close()
        cookie_names = [r[0] for r in rakuten_cookies]
        has_session = any(n in cookie_names for n in ("Rses", "Raut", "rr_session", "Rat"))
        if not has_session:
            res.warn("rakuten_session_cookies",
                     f"no session cookie in {len(cookie_names)} rakuten cookies")
        else:
            res.ok("rakuten_session_cookies")
    except sqlite3.OperationalError as e:
        # ロック中 (Chrome起動中) の場合は WARN にとどめる
        res.warn("cookies_db", f"locked or read failed: {e}")
    except Exception as e:
        res.fail("cookies_db", f"sqlite error: {e}")

    return res


def main():
    parser = argparse.ArgumentParser(description="Chrome profile health check (Phase C-3)")
    parser.add_argument("--action", default="all",
                        choices=["post", "like", "followback", "follow", "all"],
                        help="チェック対象 action (default: all)")
    parser.add_argument("--json", action="store_true", help="JSON 出力")
    args = parser.parse_args()

    actions = ["post", "like", "followback", "follow"] if args.action == "all" else [args.action]
    all_results = []
    overall_healthy = True

    for action in actions:
        res = check_profile(action)
        if not res.is_healthy:
            overall_healthy = False
        all_results.append(res)
        if not args.json:
            res.print_summary()

    if args.json:
        print(json.dumps([r.to_dict() for r in all_results], ensure_ascii=False, indent=2))

    return 0 if overall_healthy else 2


if __name__ == "__main__":
    sys.exit(main())
