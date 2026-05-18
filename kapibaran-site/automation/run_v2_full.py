# -*- coding: utf-8 -*-
"""KAPIBARAN v2 フル デプロイ（orchestrator）

実行順:
1. deploy_v2_css.py  — Customizer に v2 CSS を流し込み
2. deploy_v2_pages.py — 全ページを REST API で upsert + 旧 v1 商品 draft 化
3. deploy_v2_menus.py — ヘッダー/フッターメニューを再構築
4. verify_v2.py       — HTTP GET で反映確認
"""
from __future__ import annotations
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# Windows cp932 環境でも UTF-8 で出力
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
AUTO = BASE / "automation"


def run_step(name: str, script: str) -> bool:
    print(f"\n{'=' * 68}")
    print(f"  ▶ {name}  ({script})")
    print(f"{'=' * 68}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, str(AUTO / script)],
        cwd=str(BASE),
        capture_output=False,
        env=env,
    )
    ok = (r.returncode == 0)
    print(f"  {'✅' if ok else '❌'} {name} returncode={r.returncode}")
    return ok


def main():
    t0 = time.time()
    steps = [
        ("Step 1/4: Custom CSS デプロイ", "deploy_v2_css.py"),
        ("Step 2/4: ページ一括 upsert",   "deploy_v2_pages.py"),
        ("Step 3/4: メニュー再構築",       "deploy_v2_menus.py"),
        ("Step 4/4: 反映確認",             "verify_v2.py"),
    ]
    summary = []
    for name, script in steps:
        ok = run_step(name, script)
        summary.append((name, ok))
        if not ok and "確認" not in name:
            print(f"\n💥 fatal: {name} で失敗。後続をスキップして verify を実行します。")
            run_step("Step 4/4: 反映確認（強制実行）", "verify_v2.py")
            break

    elapsed = time.time() - t0
    print(f"\n{'=' * 68}")
    print(f"  TOTAL elapsed: {elapsed:.1f}s")
    print(f"{'=' * 68}")
    for name, ok in summary:
        print(f"  {'✅' if ok else '❌'} {name}")
    all_ok = all(ok for _, ok in summary)
    print(f"\n  {'🎉 v2 全工程 PASS' if all_ok else '⚠️ 一部失敗あり (logs/verify_v2_result.json を確認)'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
