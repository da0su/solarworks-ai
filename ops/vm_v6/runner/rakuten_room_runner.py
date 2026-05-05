#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 unified entry point.

Plan v4 P1 (VB 完結化): 4機能を mode で分岐実行する単一エントリーポイント。

使い方 (VM 内 cmd):
    python rakuten_room_runner.py --mode post --limit 50 --batch 1
    python rakuten_room_runner.py --mode like --limit 100
    python rakuten_room_runner.py --mode follow --limit 200 --force
    python rakuten_room_runner.py --mode followback --limit 30
"""
from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="楽天ROOM VM v6 unified runner")
    parser.add_argument("--mode", required=True,
                        choices=["post", "like", "follow", "followback"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch", type=int, default=1, help="POST batch index")
    parser.add_argument("--force", action="store_true", help="force (FOLLOW dead_zone bypass)")
    args = parser.parse_args()

    # 現在のディレクトリを Python path に追加 (VM 内・HOST 両対応)
    runner_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(runner_dir.parent.parent.parent))  # repo root

    from ops.vm_v6.runner.shared_logic import HeartbeatPusher, SessionLogger

    hb = HeartbeatPusher(args.mode)
    log = SessionLogger(args.mode)

    log.log(f"=== rakuten_room_runner start: mode={args.mode} limit={args.limit} ===")

    # mode 別 dispatch
    try:
        if args.mode == "post":
            from ops.vm_v6.runner.post_executor_v6 import run_post
            result = run_post(limit=args.limit, batch=args.batch, hb=hb, log=log)
        elif args.mode == "like":
            from ops.vm_v6.runner.like_executor_v6 import run_like
            result = run_like(limit=args.limit, hb=hb, log=log)
        elif args.mode == "follow":
            from ops.vm_v6.runner.follow_executor_v6 import run_follow
            result = run_follow(limit=args.limit, hb=hb, log=log, force=args.force)
        elif args.mode == "followback":
            from ops.vm_v6.runner.followback_executor_v6 import run_followback
            result = run_followback(limit=args.limit, hb=hb, log=log)
        else:
            log.log(f"[ERROR] unknown mode: {args.mode}")
            return 1

        # 結果を JSON で stdout に出力 (HTTP server がパースする)
        print(json.dumps(result, ensure_ascii=False))
        log.log(f"=== rakuten_room_runner end: {result} ===")
        return 0 if result.get("success", 0) > 0 or result.get("stop_reason") in ("target_reached", "all_seeds_done", "completed") else 4

    except Exception as e:
        log.log(f"[FATAL] {e}")
        import traceback; log.log(traceback.format_exc())
        return 5


if __name__ == "__main__":
    sys.exit(main())
