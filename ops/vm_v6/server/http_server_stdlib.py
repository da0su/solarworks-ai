#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM 内 HTTP server (stdlib only, no fastapi/uvicorn).

2026-05-24: VM disk full で fastapi install 不可 → stdlib http.server で代替.
依存ゼロ. Python 3.x で即起動可能.

API (互換):
  GET  /              → status check
  GET  /healthz       → health check (no auth)
  GET  /status        → running modes + heartbeats
  POST /run           → {"mode": "post"|"like"|"follow"|"followback"|"comment_edit", "limit": N, "batch": N, "force": bool}
  POST /abort         → {"mode": "X"} or {"all": true}
  POST /exec          → {"cmd": [...args]} VM 内任意コマンド実行 (危険・要 auth)

Auth: Authorization: Bearer <BOT_API_TOKEN>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API_TOKEN = os.environ.get("BOT_API_TOKEN", "rakuten-room-v6-secret")
PORT = int(os.environ.get("BOT_HTTP_PORT", "8765"))

RUNNING_MODES: dict[str, dict] = {}
RUNNER_PATH = Path(__file__).resolve().parent.parent / "runner" / "rakuten_room_runner.py"
HEARTBEAT_DIR = Path(__file__).resolve().parent.parent / "data"
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _cleanup_finished_modes():
    """RUNNING_MODES から終了済みプロセスを除去 (2026-05-25 fix: stale entry 防止)."""
    for m in list(RUNNING_MODES.keys()):
        proc = RUNNING_MODES[m].get("_proc")
        if proc is not None:
            if proc.poll() is not None:  # process has terminated
                del RUNNING_MODES[m]
        else:
            # _proc なし (古いエントリ or PID のみ) → PID 存在確認
            pid = RUNNING_MODES[m].get("pid")
            if pid:
                try:
                    r = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                        capture_output=True, text=True, timeout=5, creationflags=NO_WIN
                    )
                    # 2026-05-25 fix: tasklist /FI "PID eq N" は正確な PID フィルタ。
                    # 一致プロセスなし → "INFO: No tasks running" を返す。
                    # 一致あり → プロセス名 + PID 含む行を返す。
                    # "No tasks" で死亡判定 (PID 部分一致の誤検知回避)。
                    stdout_text = r.stdout or ""
                    pid_dead = "No tasks" in stdout_text or "タスクは実行されていません" in stdout_text
                    if pid_dead:
                        del RUNNING_MODES[m]
                except Exception:
                    pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet (don't print every req)
        pass

    def _send(self, code: int, body: dict | str, content_type="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {API_TOKEN}"

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self._send(200, {"status": "ok", "service": "rakuten_room_bot_v6_stdlib"})
        elif self.path == "/healthz":
            self._send(200, {"ok": True, "ts": datetime.now().isoformat()})
        elif self.path == "/status":
            if not self._check_auth():
                self._send(401, {"error": "invalid token"})
                return
            _cleanup_finished_modes()  # 終了済みプロセスを除去
            out = {"running": list(RUNNING_MODES.keys()), "heartbeats": {}}
            for mode in ["post", "like", "follow", "followback"]:
                hb_path = HEARTBEAT_DIR / f"heartbeat_{mode}.json"
                if hb_path.exists():
                    try:
                        hb = json.loads(hb_path.read_text(encoding="utf-8"))
                        age = (datetime.now() - datetime.fromisoformat(hb["ts"])).total_seconds()
                        out["heartbeats"][mode] = {**hb, "age_sec": int(age)}
                    except Exception:
                        out["heartbeats"][mode] = None
            self._send(200, out)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._check_auth():
            self._send(401, {"error": "invalid token"})
            return
        payload = self._read_json()
        if self.path == "/run":
            mode = payload.get("mode")
            if mode not in ["post", "like", "follow", "followback", "comment_edit"]:
                self._send(400, {"error": "invalid mode"})
                return
            _cleanup_finished_modes()  # stale entries を除去
            if mode in RUNNING_MODES:
                self._send(200, {"status": "already_running", "pid": RUNNING_MODES[mode]["pid"]})
                return
            # 2026-05-24: -m runner.rakuten_room_runner で相対 import 正常動作
            # cwd は ops/vm_v6/ (= runner の親). VM では \\vboxsvr\vm_v6
            args = [sys.executable, "-m", "runner.rakuten_room_runner",
                    "--mode", mode, "--limit", str(payload.get("limit", 100))]
            if mode == "post":
                args += ["--batch", str(payload.get("batch", 1))]
            if payload.get("force"):
                args += ["--force"]
            try:
                DETACHED = 0x00000008 if sys.platform == "win32" else 0
                # ログ出力先 (HOST から見える共有フォルダ)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_path = Path(r"\\vboxsvr\vm_data") / f"runner_{mode}_{ts}.log"
                logf = log_path.open("w", encoding="utf-8")
                logf.write(f"=== runner {mode} launched at {datetime.now().isoformat()} ===\n")
                logf.write(f"args: {args}\n")
                logf.flush()
                # cwd は runner の親 dir (vm_v6/) = RUNNER_PATH.parent.parent
                cwd = str(RUNNER_PATH.parent.parent)
                proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                        cwd=cwd,
                                        creationflags=NO_WIN | DETACHED, close_fds=True)
                RUNNING_MODES[mode] = {"pid": proc.pid, "_proc": proc,
                                       "started_at": datetime.now().isoformat(),
                                       "log": str(log_path)}
                self._send(200, {"status": "launched", "mode": mode, "pid": proc.pid,
                                 "log": str(log_path)})
            except Exception as e:
                self._send(500, {"error": f"launch fail: {e}"})
        elif self.path == "/abort":
            mode = payload.get("mode")
            if mode and mode in RUNNING_MODES:
                try:
                    pid = RUNNING_MODES[mode]["pid"]
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                   capture_output=True, timeout=5)
                    del RUNNING_MODES[mode]
                    self._send(200, {"status": "aborted", "mode": mode})
                except Exception as e:
                    self._send(500, {"error": f"abort fail: {e}"})
                return
            if payload.get("all"):
                for m in list(RUNNING_MODES.keys()):
                    try:
                        pid = RUNNING_MODES[m]["pid"]
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                       capture_output=True, timeout=5)
                    except Exception:
                        pass
                RUNNING_MODES.clear()
                self._send(200, {"status": "all_aborted"})
                return
            self._send(404, {"error": "mode not running"})
        elif self.path == "/exec":
            # 任意コマンド実行 (要 auth)
            cmd = payload.get("cmd")
            if not isinstance(cmd, list) or not cmd:
                self._send(400, {"error": "cmd must be list of args"})
                return
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=payload.get("timeout", 60),
                                   encoding="utf-8", errors="replace",
                                   creationflags=NO_WIN)
                self._send(200, {
                    "rc": r.returncode,
                    "stdout": (r.stdout or "")[-2000:],
                    "stderr": (r.stderr or "")[-2000:],
                })
            except Exception as e:
                self._send(500, {"error": f"exec fail: {e}"})
        else:
            self._send(404, {"error": "not found"})


def main():
    print(f"[http_server_stdlib] starting on port {PORT}", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[http_server_stdlib] listening on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
