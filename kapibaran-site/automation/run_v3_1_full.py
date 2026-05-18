# -*- coding: utf-8 -*-
"""KAPIBARAN v3.1 — フル デプロイ orchestrator (Codex review 後 改善版)

Codex review (2026-05-18) 反映:
- #5 バックアップ ゲート: backup_v3_snapshot.py 失敗時は全 step ABORT
- #9 ドライラン / ロールバック手順は RUNBOOK_v3.md
- #10 通知抑制 default. 失敗時のみ Slack <!channel> 集約通知

実行順:
  0. backup_v3_snapshot.py    — REST 全件 snapshot (失敗時 ABORT)
  1. deploy_v3_media.py       — 17 枚 画像
  2. deploy_v3_css.py         — Customizer CSS (display:none 削除版)
  3. deploy_v3_pages.py       — 全ページ upsert (privacy slug 統一 + EC disabled)
  4. deploy_v3_journal.py     — Journal 5 記事
  5. deploy_v3_compliance.py  — 全 page/post phrase + markup strip
  6. verify_v3.py             — 15 URL 陽性 assertion 強化版
"""
from __future__ import annotations
import io
import os
import json
import subprocess
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE.parent
AUTO = BASE / "automation"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str, *, silent: bool):
    line = f"[{_ts()}] {msg}"
    if not silent:
        print(line, flush=True)
    with open(LOG_DIR / "run_v3_1_full.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(name: str, script: str, *, silent: bool) -> tuple[bool, str]:
    _log(f"\n{'=' * 68}", silent=silent)
    _log(f"  ▶ {name}  ({script})", silent=silent)
    _log(f"{'=' * 68}", silent=silent)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # silent 時は capture, 失敗時に tail を返す
    r = subprocess.run(
        [sys.executable, str(AUTO / script)],
        cwd=str(BASE),
        capture_output=silent,
        env=env,
        text=True,
    )
    ok = (r.returncode == 0)
    tail = ""
    if silent and not ok:
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        tail = "\n".join(out.strip().splitlines()[-30:])
    _log(f"  {'OK' if ok else 'NG'} {name} returncode={r.returncode}", silent=silent)
    return ok, tail


def _slack_notify(text: str) -> None:
    """Codex #10: 失敗時のみ呼ばれる. SLACK_WEBHOOK_URL があれば送信."""
    url = os.environ.get("KAPIBARAN_V3_SLACK_WEBHOOK") \
        or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    try:
        import urllib.request
        data = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="KAPIBARAN v3.1 full deploy")
    ap.add_argument("--verbose", action="store_true",
                    help="進捗を stdout にも詳細出力 (default: silent)")
    ap.add_argument("--dry-run", action="store_true",
                    help="backup のみ実行して exit (Codex #9)")
    ap.add_argument("--skip-backup", action="store_true",
                    help="バックアップ skip (緊急時のみ・通常使用禁止)")
    args = ap.parse_args()

    silent = not args.verbose  # Codex #10: default silent
    t0 = time.time()
    _log(f"===== KAPIBARAN v3.1 deploy 開始 (silent={silent} dry_run={args.dry_run}) =====",
         silent=silent)

    summary: list = []
    # Codex #8 (3 回目): silent run でも検知できるよう sentinel file を必ず更新
    sentinel = LOG_DIR / "run_v3_1_full.sentinel.json"
    sentinel_data = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "silent": silent,
        "dry_run": args.dry_run,
        "exit_code": None,
        "steps": [],
    }
    sentinel.write_text(json.dumps(sentinel_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Step 0: backup (Codex #5)
    # Codex #6 (5 回目): --skip-backup は env ALLOW_SKIP_BACKUP=1 がない限り無効化
    if args.skip_backup:
        if os.environ.get("ALLOW_SKIP_BACKUP", "0") != "1":
            _log("💥 --skip-backup には env ALLOW_SKIP_BACKUP=1 必須 (Codex 5 回目 安全策). ABORT.",
                 silent=False)
            sentinel_data["status"] = "skip_backup_blocked"
            sentinel_data["exit_code"] = 2
            sentinel_data["finished_at"] = datetime.now().isoformat(timespec="seconds")
            sentinel.write_text(json.dumps(sentinel_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return 2
        _log("⚠️ --skip-backup: バックアップ skip (緊急時のみ・ALLOW_SKIP_BACKUP=1 検出)", silent=silent)
    else:
        ok, tail = run_step("Step 0/6: REST 全件 snapshot", "backup_v3_snapshot.py", silent=silent)
        summary.append(("Step 0/6: backup", ok))
        if not ok:
            _log("💥 BACKUP FAILED -> ABORT (Codex #5 要件)", silent=False)
            _slack_notify(
                "<!channel> [KAPIBARAN v3.1] BACKUP FAILED — deploy ABORT.\n"
                f"tail:\n```{tail}```"
            )
            sentinel_data["status"] = "backup_failed"
            sentinel_data["exit_code"] = 2
            sentinel_data["finished_at"] = datetime.now().isoformat(timespec="seconds")
            sentinel_data["steps"] = [{"name": n, "ok": ok} for n, ok in summary]
            sentinel.write_text(json.dumps(sentinel_data, ensure_ascii=False, indent=2), encoding="utf-8")
            _print_summary(summary, t0, silent=False)
            return 2

    if args.dry_run:
        _log("✅ dry-run 完了 (Codex #9): backup snapshot のみ実行", silent=False)
        # Codex #1 (4 回目): dry-run でも sentinel を ok に更新 (status=running 残置 false positive 回避)
        sentinel_data["status"] = "ok_dry_run"
        sentinel_data["exit_code"] = 0
        sentinel_data["finished_at"] = datetime.now().isoformat(timespec="seconds")
        sentinel_data["steps"] = [{"name": n, "ok": ok} for n, ok in summary]
        sentinel.write_text(json.dumps(sentinel_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    steps = [
        ("Step 1/6: メディアアップロード",     "deploy_v3_media.py"),
        ("Step 2/6: Custom CSS v3 デプロイ",   "deploy_v3_css.py"),
        ("Step 3/6: ページ一括 upsert (v3)",   "deploy_v3_pages.py"),
        ("Step 4/6: ジャーナル 5 記事",        "deploy_v3_journal.py"),
        ("Step 5/6: 全文置換 (compliance)",     "deploy_v3_compliance.py"),
        ("Step 6/6: v3.1 反映確認 (verify)",   "verify_v3.py"),
    ]
    failed_step: tuple | None = None
    for name, script in steps:
        ok, tail = run_step(name, script, silent=silent)
        summary.append((name, ok))
        if not ok and script != "verify_v3.py":
            failed_step = (name, script, tail)
            # 続けて verify_v3 だけは実行して状況把握
            run_step("Final: verify_v3 (強制)", "verify_v3.py", silent=silent)
            break

    all_ok = all(ok for _, ok in summary)
    exit_code = 0 if all_ok else 1
    if not all_ok:
        # Codex #10: 失敗時のみ Slack 集約通知
        tail_msg = ""
        if failed_step:
            tail_msg = f"\nfailed: {failed_step[0]}\ntail:\n```{failed_step[2]}```"
        _slack_notify(
            f"<!channel> [KAPIBARAN v3.1] deploy 失敗.{tail_msg}\n"
            f"検証結果: kapibaran-site/logs/verify_v3_result.json"
        )

    # Codex #8 (3 回目): sentinel 更新
    sentinel_data["status"] = "ok" if all_ok else "failed"
    sentinel_data["exit_code"] = exit_code
    sentinel_data["finished_at"] = datetime.now().isoformat(timespec="seconds")
    sentinel_data["steps"] = [{"name": n, "ok": ok} for n, ok in summary]
    sentinel.write_text(json.dumps(sentinel_data, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(summary, t0, silent=False)
    return exit_code


def _print_summary(summary, t0, *, silent: bool):
    elapsed = time.time() - t0
    print(f"\n{'=' * 68}", flush=True)
    print(f"  TOTAL elapsed: {elapsed:.1f}s", flush=True)
    print(f"{'=' * 68}", flush=True)
    for name, ok in summary:
        print(f"  [{'OK' if ok else 'NG'}] {name}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
