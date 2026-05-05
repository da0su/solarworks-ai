#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase C-5: Evidence rotation manager

【目的】
follow_rpa_vm.py が evidence/<session_id>/ に保存する fail screenshot を rotation 保存し、
古いものを削除する。最新 N セッション分を維持する。

【保存先】
rakuten-room/bot/evidence/  (HOST 上)
└── 20260505_153045/         (session id = タイムスタンプ)
    ├── fail_001_*.png
    ├── fail_002_*.png
    └── ...

【ルール】
- 最新 5 セッション分のみ keep
- 30 日以上前のものは強制削除
- 「最新 evidence の URL」を patrol_hourly が Slack alert に貼れるよう
  最新 evidence の path を state file に書く

実行: python ops/evidence_rotation.py [--max-sessions 5] [--max-age-days 30]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "rakuten-room" / "bot" / "evidence"
LATEST_EVIDENCE_STATE = REPO_ROOT / "state" / "evidence_latest.json"


def list_sessions() -> list[Path]:
    """evidence/ 配下の session dir 一覧（新しい順）."""
    if not EVIDENCE_DIR.exists():
        return []
    sessions = [d for d in EVIDENCE_DIR.iterdir() if d.is_dir()]
    # 名前 (timestamp) 降順 でソート → 新しいものが先頭
    sessions.sort(key=lambda d: d.name, reverse=True)
    return sessions


def cleanup(max_sessions: int = 5, max_age_days: int = 30) -> dict:
    """rotation cleanup を実施.

    Returns:
        {"kept": [...], "removed": [...], "errors": [...]}
    """
    result: dict = {"kept": [], "removed": [], "errors": []}
    sessions = list_sessions()
    cutoff = datetime.now() - timedelta(days=max_age_days)

    for i, sess in enumerate(sessions):
        # 古すぎる場合は無条件削除
        try:
            session_dt = datetime.strptime(sess.name[:15], "%Y%m%d_%H%M%S")
            is_old = session_dt < cutoff
        except Exception:
            is_old = False

        # 最新 N セッション以外 + 古すぎる → 削除対象
        if i < max_sessions and not is_old:
            result["kept"].append(sess.name)
            continue

        try:
            shutil.rmtree(sess)
            result["removed"].append(sess.name)
        except Exception as e:
            result["errors"].append({"session": sess.name, "error": str(e)})

    # 最新 evidence path を state file に書く
    try:
        if result["kept"]:
            latest = result["kept"][0]
            latest_path = str(EVIDENCE_DIR / latest)
            file_count = sum(1 for _ in (EVIDENCE_DIR / latest).glob("*"))
            LATEST_EVIDENCE_STATE.parent.mkdir(parents=True, exist_ok=True)
            LATEST_EVIDENCE_STATE.write_text(json.dumps({
                "session_id": latest,
                "path": latest_path,
                "file_count": file_count,
                "updated_at": datetime.now().isoformat(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        result["errors"].append({"state_write_error": str(e)})

    return result


def main():
    parser = argparse.ArgumentParser(description="Evidence rotation (Phase C-5)")
    parser.add_argument("--max-sessions", type=int, default=5,
                        help="維持する最新 session 数 (default: 5)")
    parser.add_argument("--max-age-days", type=int, default=30,
                        help="この日数より古い session は削除 (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="削除せず予定のみ表示")
    args = parser.parse_args()

    if args.dry_run:
        sessions = list_sessions()
        print(f"sessions: {len(sessions)}")
        for i, s in enumerate(sessions):
            label = "KEEP" if i < args.max_sessions else "DEL"
            print(f"  [{label}] {s.name}")
        return 0

    result = cleanup(max_sessions=args.max_sessions, max_age_days=args.max_age_days)
    print(f"[evidence_rotation] kept={len(result['kept'])} removed={len(result['removed'])} errors={len(result['errors'])}")
    if result["removed"]:
        print(f"  removed: {result['removed']}")
    if result["errors"]:
        print(f"  errors: {result['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
