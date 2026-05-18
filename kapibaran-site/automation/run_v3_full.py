# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — フル デプロイ orchestrator

実行順:
1. deploy_v3_media.py     — 17 枚 画像を WP メディアライブラリへ upload
2. deploy_v3_css.py       — CSS_V3 を Customizer に流し込み (v3 追加 class)
3. deploy_v3_pages.py     — 法令遵守 + 画像入り pages を一括 upsert
4. deploy_v3_journal.py   — Journal 5 記事 + アイキャッチ
5. deploy_v3_compliance.py— 残った post_content の禁止表現を全文置換
6. verify_v3.py           — HTTP GET で反映確認 (禁止表現 + 画像数)
"""
from __future__ import annotations
import io
import os
import subprocess
import sys
import time
from pathlib import Path

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
        ("Step 1/6: メディアアップロード",     "deploy_v3_media.py"),
        ("Step 2/6: Custom CSS v3 デプロイ",   "deploy_v3_css.py"),
        ("Step 3/6: ページ一括 upsert (v3)",   "deploy_v3_pages.py"),
        ("Step 4/6: ジャーナル 5 記事",        "deploy_v3_journal.py"),
        ("Step 5/6: 全文置換 (compliance)",     "deploy_v3_compliance.py"),
        ("Step 6/6: v3 反映確認",              "verify_v3.py"),
    ]
    summary = []
    for name, script in steps:
        ok = run_step(name, script)
        summary.append((name, ok))
        if not ok and "確認" not in name:
            print(f"\n💥 fatal: {name} で失敗。verify を実行して状況確認。")
            run_step("Final: 反映確認 (強制)", "verify_v3.py")
            break

    elapsed = time.time() - t0
    print(f"\n{'=' * 68}")
    print(f"  TOTAL elapsed: {elapsed:.1f}s")
    print(f"{'=' * 68}")
    for name, ok in summary:
        print(f"  {'✅' if ok else '❌'} {name}")
    all_ok = all(ok for _, ok in summary)
    print(f"\n  {'🎉 v3 全工程 PASS' if all_ok else '⚠️ 一部失敗あり (logs/verify_v3_result.json を確認)'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
