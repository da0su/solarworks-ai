"""禁忌ファイル Read ガード hook (Codex 推奨 C).

CLAUDE.md 「ROOM 状況把握フロー」 + memory rakuten_machine_split.md の
「メイン PC ファイルは Plan v6 cutover で凍結された旧データ」 を強制するための
pre-tool-use hook.

【目的】
Claude (LLM agent) が ROOM 4 機能の現状判断目的で host のレガシー JSON
(follow_history.json 等) を Read することを **デフォルト拒否** し、
SSOT (state/follow_runtime_state.json / ops/room_status.py) への誘導メッセージを返す.

【動作】
- stdin から tool_name + tool_input.file_path を受け取る
- tool_name == "Read" かつ file_path が禁忌リストにマッチ → exit(2) + 誘導メッセージ
- 環境変数 `ROOM_STATUS_ALLOW_FORBIDDEN=1` が設定されていれば bypass + 警告 log
- 監査記録: state/forbidden_reads.log に全アクセス記録

【exit code】
- 0: 許可
- 2: blocked (Claude Code 側で tool 実行を阻止)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT_LOG = REPO / "state" / "forbidden_reads.log"

# 禁忌ファイル パターン (path に含まれていれば match)
FORBIDDEN_PATTERNS = [
    "rakuten-room/bot/data/follow_history.json",
    "rakuten-room/bot/data/like_history.json",
    "rakuten-room/bot/data/post_history.json",
    "rakuten-room/bot/data/fl_daily_log.json",
    "rakuten-room\\bot\\data\\follow_history.json",
    "rakuten-room\\bot\\data\\like_history.json",
    "rakuten-room\\bot\\data\\post_history.json",
    "rakuten-room\\bot\\data\\fl_daily_log.json",
]


def _audit(record: dict) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    # stdin から tool call JSON を読む (Claude Code hook protocol)
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except Exception:
        # 入力なし or 不正 → 何もしない (他の hook を妨げない)
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    file_path = (tool_input.get("file_path") or "").replace("\\", "/").lower()

    # Read tool 以外は何もしない
    if tool_name not in ("Read", "Grep", "Glob"):
        return 0
    if not file_path:
        return 0

    # 禁忌マッチ?
    matched = None
    for pat in FORBIDDEN_PATTERNS:
        if pat.replace("\\", "/").lower() in file_path:
            matched = pat
            break
    if not matched:
        return 0

    # 例外フラグ
    allow_forbidden = os.environ.get("ROOM_STATUS_ALLOW_FORBIDDEN", "").strip()
    audit = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": tool_name,
        "file_path": tool_input.get("file_path"),
        "matched_pattern": matched,
        "allow_flag": allow_forbidden or None,
        "result": "allowed" if allow_forbidden else "blocked",
    }
    _audit(audit)

    if allow_forbidden:
        print(
            f"[room_status_guard] ⚠️ allow_flag 経由で禁忌ファイル read 許可: {matched}",
            file=sys.stderr,
        )
        return 0

    # BLOCK + 誘導メッセージ
    msg = [
        "",
        "=" * 60,
        "🚨 [room_status_guard] 禁忌ファイルへの Read を BLOCK",
        f"  対象: {tool_input.get('file_path')}",
        f"  パターン: {matched}",
        "",
        "  理由: このファイルは Plan v6 cutover で凍結された旧データ.",
        "  ROOM 4 機能の状況判断に使うのは 過去 2 回の CEO 指摘で禁止済.",
        "",
        "  代わりに以下を実行してください:",
        "    python ops/room_status.py --human    # 4 機能 SSOT サマリー",
        "    cat state/follow_runtime_state.json  # 生 JSON SSOT",
        "",
        "  どうしても本当にこのファイルを読みたい場合 (POST 実装時の参照等):",
        "    ROOM_STATUS_ALLOW_FORBIDDEN=1 で実行",
        "",
        "  過去の失敗:",
        "    - 2026-05-20: chrome_profile_post (host) を見て「空アカ」誤判定",
        "    - 2026-05-22: follow_history.json を見て「FOLLOW 2日停止」誤判定",
        "    → 二度と繰り返さないため hook で強制ブロック (Codex 推奨 C)",
        "=" * 60,
        "",
    ]
    print("\n".join(msg), file=sys.stderr)
    return 2  # Claude Code が tool 実行を中止


if __name__ == "__main__":
    sys.exit(main())
