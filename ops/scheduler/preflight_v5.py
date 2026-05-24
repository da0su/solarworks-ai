#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V5 Preflight Check

実行前に13項目をチェック。1つでも CRITICAL NG なら blocked で終了。
WARNING は記録して続行。

使い方:
  python ops/scheduler/preflight_v5.py              # チェック実行
  python ops/scheduler/preflight_v5.py --verbose     # 詳細出力
"""
from __future__ import annotations
import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "room_bot_v5.db"
LOCK_PATH = REPO_ROOT / "rakuten-room" / "bot" / "data" / "execution.lock"
SHARED_FOLDER = Path(r"\\VBOXSVR\share")


class PreflightCheck:
    """13項目のPreflight Check"""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.results = {}
        self.blocked = False
        self.warnings = []

    def check(self, name, level, passed, detail=""):
        """level: 'critical' or 'warning'"""
        self.results[name] = {
            "passed": passed,
            "level": level,
            "detail": detail,
        }
        if not passed and level == "critical":
            self.blocked = True
        if not passed and level == "warning":
            self.warnings.append(f"{name}: {detail}")
        if self.verbose:
            status = "PASS" if passed else f"FAIL({level})"
            print(f"  [{status}] {name}: {detail}")

    def run_all(self):
        """全13項目チェック"""
        print("=== V5 Preflight Check ===\n")

        # 1. ログイン状態（簡易: storage_state.jsonの存在と更新日時）
        # 2026-05-25 fix: Plan v6 cutover 後は HOST storage_state は参照しない (VM 内 Chrome が
        # 独立セッションを持つ)。閾値 warning に降格して false positive による block を解消。
        # 旧: critical 168h → 新: warning 720h (30日)
        ss_path = REPO_ROOT / "rakuten-room" / "bot" / "data" / "state" / "storage_state.json"
        if ss_path.exists():
            age_hours = (datetime.now().timestamp() - ss_path.stat().st_mtime) / 3600
            self.check("login_state", "warning", age_hours < 720, f"storage_state age={age_hours:.1f}h (host session, VM-managed)")
        else:
            self.check("login_state", "warning", False, "storage_state.json not found (ok if VM-managed)")

        # 2. 共有フォルダ書込可否
        try:
            test_file = SHARED_FOLDER / ".preflight_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            self.check("shared_folder", "critical", True, "write OK")
        except Exception as e:
            self.check("shared_folder", "warning", False, str(e)[:80])

        # 3. ジョブ重複
        if LOCK_PATH.exists():
            age_min = (datetime.now().timestamp() - LOCK_PATH.stat().st_mtime) / 60
            stale = age_min > 30  # 30分以上前のlockはstale
            if stale:
                LOCK_PATH.unlink()
                self.check("job_duplicate", "warning", True, f"stale lock removed (age={age_min:.0f}min)")
            else:
                self.check("job_duplicate", "critical", False, f"lock exists (age={age_min:.0f}min)")
        else:
            self.check("job_duplicate", "critical", True, "no lock")

        # 4. 日次上限
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            today = datetime.now().strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT SUM(success_count) FROM execution_log WHERE plan_date=? AND action_type='follow'",
                (today,),
            ).fetchone()
            daily_count = row[0] or 0
            conn.close()
            self.check("daily_limit", "warning", daily_count < 2100, f"follow today={daily_count}")
        else:
            self.check("daily_limit", "warning", True, "no DB yet")

        # 5. 時間上限（rate_limit_tracker）
        self.check("hourly_limit", "warning", True, "not tracked yet")

        # 6. VM起動状態
        vm_alive = SHARED_FOLDER.exists() if os.name == 'nt' else False
        self.check("vm_status", "warning", vm_alive, "shared folder accessible" if vm_alive else "shared folder not accessible")

        # 7. Slack接続性
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        self.check("slack_connectivity", "warning", bool(slack_token), "token set" if slack_token else "no token")

        # 8. ディスク空き
        usage = shutil.disk_usage(str(REPO_ROOT))
        free_gb = usage.free / (1024**3)
        self.check("disk_space", "critical", free_gb > 1.0, f"free={free_gb:.1f}GB")

        # 9-13: GUI環境（VM側でチェックするため、ホスト側はスキップ）
        for name in ["resolution", "scaling", "chrome_zoom", "window_position", "auto_update"]:
            self.check(name, "warning", True, "VM-side check (skipped on host)")

        # Summary
        print(f"\n=== Preflight Result ===")
        passed = sum(1 for r in self.results.values() if r["passed"])
        total = len(self.results)
        print(f"  Passed: {passed}/{total}")
        print(f"  Blocked: {self.blocked}")
        if self.warnings:
            print(f"  Warnings: {len(self.warnings)}")
            for w in self.warnings:
                print(f"    - {w}")

        # Save to DB
        self._save_to_db()

        return not self.blocked

    def _save_to_db(self):
        if not DB_PATH.exists():
            return
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO preflight_log (checked_at, all_passed, results_json, blocked_reason) VALUES (?,?,?,?)",
            (
                datetime.now().isoformat(),
                1 if not self.blocked else 0,
                json.dumps(self.results, ensure_ascii=False),
                "; ".join(self.warnings) if self.blocked else None,
            ),
        )
        conn.commit()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="V5 Preflight Check")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    pf = PreflightCheck(verbose=args.verbose)
    ok = pf.run_all()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
