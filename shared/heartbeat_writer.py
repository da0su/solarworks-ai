#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase C-2: heartbeat schema 統一

【背景】
従来は各 executor (follow_rpa_vm.py, orchestrator_v5.py, like_executor.py等) が
それぞれ独自フォーマットで heartbeat を書いており、patrol が読む側で
複雑な分岐が必要だった。

【統一スキーマ】
{
    "schema_version": 2,
    "ts": "2026-05-05T15:30:00",          # ISO timestamp
    "pid": 12345,                          # process id
    "action": "follow",                    # follow / post / like / followback / replenish
    "phase": "navigate",                   # 任意の進行状態 (executor 依存)
    "current_target": "room_xxx",          # 現在処理中の対象 (任意)
    "metrics": {                           # action 単位の累積カウンタ
        "success_count": 42,
        "fail_count": 13,
        "skip_count": 5,
    },
    "extra": {}                            # action 固有の追加情報
}

【書込先】
- Local: rakuten-room/bot/data/state/heartbeat_<action>.json
- (Optional VM Share): \\VBOXSVR\share\heartbeat_<action>.json (follow のみ)

【スロットル】
default 30秒。executor は force=True でスロットル無視可能（startup/shutdown時）。

使い方:
    from shared.heartbeat_writer import HeartbeatWriter
    hb = HeartbeatWriter(action="like")
    hb.write(phase="scanning", success=10, fail=2)        # スロットルあり
    hb.write(phase="shutdown", force=True)                  # 即時書込
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HEARTBEAT_DIR = REPO_ROOT / "rakuten-room" / "bot" / "data" / "state"
VM_SHARE_DIR = Path(r"\\VBOXSVR\share")  # follow 専用 (VM が書込み HOST が読込)

DEFAULT_THROTTLE_SEC = 30
SCHEMA_VERSION = 2


class HeartbeatWriter:
    """統一 heartbeat スキーマで atomic write する writer.

    使用例:
        hb = HeartbeatWriter(action="follow", vm_share=True)
        hb.write(phase="startup", force=True)           # 即時書込
        hb.write(phase="navigate", current="room_xxx",
                 success=10, fail=2)                    # スロットル制御
        hb.write(phase="shutdown", force=True)          # 即時書込
    """

    def __init__(
        self,
        action: str,
        vm_share: bool = False,
        throttle_sec: int = DEFAULT_THROTTLE_SEC,
        local_dir: Optional[Path] = None,
    ):
        """
        Args:
            action: "follow", "post", "like", "followback", "replenish" のいずれか
            vm_share: True なら \\VBOXSVR\share にも書く (follow 専用)
            throttle_sec: 連続書込みの最小間隔（秒）
            local_dir: ローカル書込先 (default: rakuten-room/bot/data/state)
        """
        self.action = action
        self.throttle_sec = throttle_sec
        self.local_path = (local_dir or LOCAL_HEARTBEAT_DIR) / f"heartbeat_{action}.json"
        self.vm_share_path = VM_SHARE_DIR / f"heartbeat_{action}.json" if vm_share else None
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_write_ts = 0.0

    def write(
        self,
        phase: str = "running",
        current_target: str = "",
        success: int = 0,
        fail: int = 0,
        skip: int = 0,
        extra: Optional[dict] = None,
        force: bool = False,
    ) -> bool:
        """heartbeat を書く. force=False ならスロットル制御.

        Returns:
            True = 書込成功 / False = スロットルでスキップ
        """
        now = time.time()
        if not force and (now - self._last_write_ts) < self.throttle_sec:
            return False

        payload = {
            "schema_version": SCHEMA_VERSION,
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
        # atomic write: tmp → replace
        try:
            tmp = self.local_path.with_suffix(".tmp")
            tmp.write_text(json_str, encoding="utf-8")
            tmp.replace(self.local_path)
        except Exception:
            # P0-4: local write 失敗は致命的 (watchdog が heartbeat age で stuck 検出できなくなる)
            logger.critical(
                "[heartbeat] LOCAL WRITE FAILED action=%s phase=%s",
                self.action, phase, exc_info=True
            )
            return False

        # VM share への書込 (follow 専用・失敗しても致命的ではない)
        if self.vm_share_path is not None:
            try:
                tmp_share = self.vm_share_path.with_suffix(".tmp")
                tmp_share.write_text(json_str, encoding="utf-8")
                tmp_share.replace(self.vm_share_path)
            except Exception:
                pass  # share folder 接続不能でも local write は成功とする

        self._last_write_ts = now
        return True


def read_heartbeat(action: str, prefer_share: bool = False) -> Optional[dict]:
    """heartbeat を読む. prefer_share=True なら VM share を優先.

    Returns:
        dict or None
    """
    paths = []
    if prefer_share:
        paths.append(VM_SHARE_DIR / f"heartbeat_{action}.json")
    paths.append(LOCAL_HEARTBEAT_DIR / f"heartbeat_{action}.json")

    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def heartbeat_age_seconds(action: str, prefer_share: bool = False) -> Optional[float]:
    """heartbeat の age を秒単位で返す."""
    hb = read_heartbeat(action, prefer_share)
    if not hb:
        return None
    try:
        ts = datetime.fromisoformat(str(hb.get("ts", "")).replace("Z", ""))
        return (datetime.now() - ts).total_seconds()
    except Exception:
        return None


# ==================================================
# 自己テスト
# ==================================================

if __name__ == "__main__":
    print("=== heartbeat_writer 動作確認 ===")
    hb = HeartbeatWriter(action="test_self")
    print("Test 1: force write")
    print("  -> ", hb.write(phase="startup", force=True))
    print("Test 2: throttle (30秒以内・スキップ予定)")
    print("  -> ", hb.write(phase="running", success=5))
    print("Test 3: read back")
    print("  -> ", read_heartbeat("test_self"))
    print("Test 4: age")
    print(f"  -> {heartbeat_age_seconds('test_self'):.1f}s")
    # cleanup
    p = LOCAL_HEARTBEAT_DIR / "heartbeat_test_self.json"
    if p.exists():
        p.unlink()
        print("  -> cleaned up test file")
