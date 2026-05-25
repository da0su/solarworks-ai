#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 unified entry point.

Plan v4 P1 (VB 完結化): 4機能を mode で分岐実行する単一エントリーポイント。

使い方 (VM 内 cmd):
    python rakuten_room_runner.py --mode post --limit 50 --batch 1
    python rakuten_room_runner.py --mode like --limit 100
    python rakuten_room_runner.py --mode follow --limit 200 --force
    python rakuten_room_runner.py --mode followback --limit 30

【2026-05-24】 mode 拡張: bootstrap, http_server も対応
    bootstrap: VM 環境セットアップ (deps install + http_server 起動)
    http_server: HTTP server (FastAPI) 起動 (background detached)
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import json
import time
from datetime import datetime
from pathlib import Path

print(f"[RUNNER_VERSION] 2026-05-24_v3_stdlib_dispatch loaded at {datetime.now()}")


def _is_port_listening(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _ensure_http_server_running(log_func=print) -> bool:
    """VM 内 http_server (port 8765) が走っていれば True. なければ起動を試行.

    2026-05-24: VM disk full の場合 fastapi install 不可 → stdlib http.server 版を優先.
    """
    if _is_port_listening(8765):
        log_func("[http_server] already running on port 8765")
        return True
    log_func("[http_server] not running → starting (stdlib version)...")
    # stdlib 版 (依存ゼロ) を優先
    server_py = Path(r"\\vboxsvr\vm_v6\server\http_server_stdlib.py")
    if not server_py.exists():
        local = Path(__file__).resolve().parent.parent / "server" / "http_server_stdlib.py"
        if local.exists():
            server_py = local
        else:
            # fallback: fastapi 版
            server_py = Path(r"\\vboxsvr\vm_v6\server\http_server.py")
            if not server_py.exists():
                log_func("[http_server] no server script found")
                return False
    # Spawn detached
    try:
        log_path = Path(r"\\vboxsvr\vm_data\http_server_runtime.log")
        # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS (Windows)
        DETACHED = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        with log_path.open("a", encoding="utf-8") as logf:
            logf.write(f"\n[runner] spawning http_server at {datetime.now().isoformat()}\n")
            subprocess.Popen([sys.executable, str(server_py)],
                             stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=DETACHED if sys.platform == "win32" else 0,
                             close_fds=True)
        log_func("[http_server] spawn complete")
        # Wait up to 30s for port to listen
        for i in range(30):
            time.sleep(1)
            if _is_port_listening(8765):
                log_func(f"[http_server] port 8765 listening after {i+1}s")
                return True
        log_func("[http_server] port 8765 NOT listening (30s timeout)")
        return False
    except Exception as e:
        log_func(f"[http_server] spawn fail: {e}")
        return False


def _read_guestproperty_override() -> dict | None:
    """GuestProperty /RakutenBot/Trigger から override 設定を読む.

    HOST は host_trigger.py で {"mode": "X", "payload": {"limit": N, ...}} を set する.
    watcher は --mode comment_edit hardcode で呼ぶが、ここで GuestProperty を読んで
    実 mode に dispatch すれば watcher 改修不要で全 mode 実行可能.

    VBoxControl output 形式:
        Name: /RakutenBot/Trigger
        Value: {"mode": "follow", "payload": {...}, ...}
        Timestamp: ...
        Flags: ...
    """
    try:
        vbc = r"C:\Program Files\Oracle\VirtualBox Guest Additions\VBoxControl.exe"
        r = subprocess.run([vbc, "guestproperty", "get", "/RakutenBot/Trigger"],
                           capture_output=True, text=True, timeout=5)
        print(f"[dispatch] VBoxControl rc={r.returncode} stdout_len={len(r.stdout or '')}")
        if r.returncode != 0:
            return None
        # 各行から "Value: " で始まる行を探す
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("Value:"):
                json_str = line[len("Value:"):].strip()
                parsed = json.loads(json_str)
                print(f"[dispatch] parsed override: {parsed.get('mode')} (trigger_id={parsed.get('trigger_id')})")
                return parsed
        print(f"[dispatch] no Value: line in stdout: {(r.stdout or '')[:200]!r}")
        return None
    except Exception as e:
        print(f"[dispatch] _read_guestproperty_override err: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="楽天ROOM VM v6 unified runner")
    parser.add_argument("--mode", required=True,
                        choices=["post", "like", "follow", "followback",
                                 "comment_edit", "bootstrap", "http_server"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch", type=int, default=1, help="POST batch index")
    parser.add_argument("--force", action="store_true", help="force (FOLLOW dead_zone bypass)")
    args = parser.parse_args()

    # 2026-05-24: comment_edit 呼び出し時 GuestProperty を読んで実 mode に dispatch
    # watcher hardcode が --mode comment_edit のため、HOST trigger で実 mode を指定可能
    if args.mode == "comment_edit":
        override = _read_guestproperty_override()
        if override:
            real_mode = override.get("mode")
            if real_mode and real_mode != "comment_edit" and real_mode in (
                "post", "like", "follow", "followback", "bootstrap", "http_server"
            ):
                print(f"[dispatch] GuestProperty override: {args.mode} → {real_mode}")
                args.mode = real_mode
                payload = override.get("payload", {})
                if "limit" in payload:
                    args.limit = int(payload["limit"])
                if "batch" in payload:
                    args.batch = int(payload["batch"])
                if payload.get("force"):
                    args.force = True

    # 2026-05-24: mode=bootstrap / http_server は早期 return
    if args.mode in ("bootstrap", "http_server"):
        ok = _ensure_http_server_running()
        return 0 if ok else 1

    # 2026-05-24: 全 mode で http_server を自動起動 (idempotent)
    # → HOST から http API で完全制御可能になる
    _ensure_http_server_running()

    # 現在のディレクトリを Python path に追加 (VM 内・HOST 両対応)
    runner_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(runner_dir.parent.parent.parent))  # repo root

    # 2026-05-24: VM で HOST の rakuten-room/bot にアクセス
    # 共有フォルダ "bot" 追加 (transient) → \\vboxsvr\bot = HOST の rakuten-room/bot
    # これで config / executor.X / planner.X / logger.X 等 全て import 可能
    vm_bot = Path(r"\\vboxsvr\bot")
    if vm_bot.exists():
        sys.path.insert(0, str(vm_bot))
        print(f"[path] vm_bot added: {vm_bot}")
    else:
        # fallback: 旧構造 (share = executor 直接) で alias 作る
        vm_share = Path(r"\\vboxsvr\share")
        if vm_share.exists():
            sys.path.insert(0, str(vm_share))
            try:
                import types
                executor_pkg = types.ModuleType("executor")
                executor_pkg.__path__ = [str(vm_share)]
                sys.modules["executor"] = executor_pkg
                print(f"[path] fallback: vm_share aliased as 'executor'")
            except Exception as e:
                print(f"[path] executor alias err: {e}")

    # vm_data フォルダも path に (一部 module が data 内から import する可能性)
    vm_data = Path(r"\\vboxsvr\vm_data")
    if vm_data.exists():
        sys.path.insert(0, str(vm_data))

    # VM 内 (ops package 無) と HOST (ops package 有) 両対応
    try:
        from ops.vm_v6.runner.shared_logic import HeartbeatPusher, SessionLogger
    except ImportError:
        from .shared_logic import HeartbeatPusher, SessionLogger

    hb = HeartbeatPusher(args.mode)
    log = SessionLogger(args.mode)

    log.log(f"=== rakuten_room_runner start: mode={args.mode} limit={args.limit} ===")

    # mode 別 dispatch (絶対 import で fail なら相対 import に fallback)
    try:
        if args.mode == "post":
            try:
                from ops.vm_v6.runner.post_executor_v6 import run_post
            except ImportError:
                from .post_executor_v6 import run_post
            result = run_post(limit=args.limit, batch=args.batch, hb=hb, log=log)
        elif args.mode == "like":
            try:
                from ops.vm_v6.runner.like_executor_v6 import run_like
            except ImportError:
                from .like_executor_v6 import run_like
            result = run_like(limit=args.limit, hb=hb, log=log)
        elif args.mode == "follow":
            try:
                from ops.vm_v6.runner.follow_executor_v6 import run_follow
            except ImportError:
                from .follow_executor_v6 import run_follow
            result = run_follow(limit=args.limit, hb=hb, log=log, force=args.force)
        elif args.mode == "followback":
            try:
                from ops.vm_v6.runner.followback_executor_v6 import run_followback
            except ImportError:
                from .followback_executor_v6 import run_followback
            result = run_followback(limit=args.limit, hb=hb, log=log)
        elif args.mode == "comment_edit":
            # CEO 5/21 自立対応: 空 comment 投稿の append 修正
            try:
                from ops.vm_v6.runner.comment_edit_executor_v6 import run_comment_edit
            except ImportError:
                from .comment_edit_executor_v6 import run_comment_edit
            result = run_comment_edit(hb=hb, log=log)
        else:
            log.log(f"[ERROR] unknown mode: {args.mode}")
            return 1

        # 結果を JSON で stdout に出力 (HTTP server がパースする)
        print(json.dumps(result, ensure_ascii=False))
        log.log(f"=== rakuten_room_runner end: {result} ===")
        # comment_edit mode: job_success (Codex 42 #1) で判定
        if args.mode == "comment_edit":
            return 0 if result.get("job_success") else 4
        # no_queue_today: 今日の投稿計画が未生成 or 既に全投稿済み → 正常終了 (exit 0)
        # db_connect_error: DB障害 → 異常終了 (exit 4)
        _ok_reasons = ("target_reached", "all_seeds_done", "completed", "no_queue_today")
        return 0 if result.get("success", 0) > 0 or result.get("stop_reason") in _ok_reasons else 4

    except Exception as e:
        log.log(f"[FATAL] {e}")
        import traceback; log.log(traceback.format_exc())
        return 5


if __name__ == "__main__":
    sys.exit(main())
