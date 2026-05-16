"""CEO 指示 (2026-05-16): ChatGPT (Codex) と連携して change をブラッシュアップ.

【目的】
私 (Claude) が独断で「過剰反応」と判断して fix を撤去するような誤判断
(例: 5/12 commit 8f34a76 で 5/10 de0d8bd の URL check を消去 → 5日間虚偽報告)
を防ぐため、重要 change を ChatGPT (GPT-5) に critical review してもらう.

【使い方】
1. CLI 直接:
   python ops/codex_review.py --commit HEAD       # 直前 commit を review
   python ops/codex_review.py --file <path>       # 単一 file を review
   python ops/codex_review.py --diff "<text>"     # 任意 diff を review

2. 関数 import:
   from ops.codex_review import review_change
   r = review_change(diff_text, context="POST executor success 判定の修正")
   # r = {"verdict": "APPROVE|REVIEW|REJECT", "issues": [...], "suggestions": [...]}

【出力先】
state/codex_reviews/<timestamp>.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEWS_DIR = REPO_ROOT / "state" / "codex_reviews"
REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "gpt-5"  # critical review model

SYSTEM_PROMPT = """あなたはシニア Python/Playwright/Windows Task Scheduler エキスパート コードレビュアーです。
楽天 ROOM 自動化 bot の change を critical review します。
重視:
1. 過去の修正を撤去・上書きする change は特に疑い、その変更で何かが壊れないか厳密に check
2. 「URL 残留 = failure 判定」のような検証ロジックを安易に消すと false success が発生する潜在問題を指摘
3. CEO は信頼性を重視・嘘の報告を 1 度でも出すと信用失墜・徹底防止が必要
4. Slack 通知過剰・cmd 窓表示・依存追加 etc も指摘

出力フォーマット (厳格):
{
    "verdict": "APPROVE | REVIEW_NEEDED | REJECT",
    "summary": "1-2 行要約",
    "issues": ["指摘 1", "指摘 2", ...],
    "suggestions": ["改善 1", "改善 2", ...],
    "risks": ["リスク 1", ...]
}

verdict 基準:
- APPROVE: 問題なし安全
- REVIEW_NEEDED: 何か気になる点あり (issues に記載) - CEO 確認推奨
- REJECT: 既存 fix を壊す/虚偽報告発生 等 重大問題あり - 適用すべきでない
"""


def _load_openai_key() -> str | None:
    """OpenAI API key を環境変数 or .env files から取得."""
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    for env_path in [
        REPO_ROOT / ".env",
        REPO_ROOT / "web-media" / "eneuru" / ".env",
        REPO_ROOT / "web-media" / "seo" / ".env",
        REPO_ROOT / "rakuten-room" / "bot" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def review_change(diff_text: str, context: str = "") -> dict:
    """ChatGPT (Codex) に change を投げて review を取得.

    Args:
        diff_text: git diff 形式の change
        context: change の背景説明 (1-3 行推奨)

    Returns:
        {verdict, summary, issues, suggestions, risks, raw}
    """
    key = _load_openai_key()
    if not key:
        return {"verdict": "REJECT", "summary": "OPENAI_API_KEY 未設定", "issues": ["key not found"], "suggestions": [], "risks": [], "raw": ""}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
    except Exception as e:
        return {"verdict": "REJECT", "summary": f"OpenAI client init failed: {e}", "issues": [], "suggestions": [], "risks": [], "raw": ""}

    user_content = f"【コンテキスト】\n{context}\n\n【diff】\n```\n{diff_text[:60000]}\n```\n\n結果は JSON 形式で返してください."
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
    except Exception as e:
        return {"verdict": "REVIEW_NEEDED", "summary": f"OpenAI call err: {e}", "issues": [str(e)], "suggestions": [], "risks": [], "raw": ""}

    data["raw"] = raw
    data["timestamp"] = datetime.now().isoformat(timespec="seconds")
    data["context"] = context
    data["model"] = MODEL

    # 保存
    fn = REVIEWS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_review.json"
    fn.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    data["saved_to"] = str(fn)
    return data


def _get_commit_diff(rev: str) -> tuple[str, str]:
    """指定 commit の diff + message を取得."""
    msg = subprocess.run(["git", "log", "-1", "--format=%s%n%n%b", rev], capture_output=True, text=True, encoding="utf-8").stdout
    diff = subprocess.run(["git", "show", "--format=", rev], capture_output=True, text=True, encoding="utf-8").stdout
    return diff, msg


def _print_review(r: dict):
    print(f"\n{'='*60}")
    print(f"Codex Review @ {r.get('timestamp','?')} (model={r.get('model','?')})")
    print(f"{'='*60}")
    print(f"VERDICT  : {r.get('verdict','?')}")
    print(f"SUMMARY  : {r.get('summary','?')}")
    if r.get("issues"):
        print("\nISSUES:")
        for i, it in enumerate(r.get("issues", []), 1):
            print(f"  {i}. {it}")
    if r.get("suggestions"):
        print("\nSUGGESTIONS:")
        for i, s in enumerate(r.get("suggestions", []), 1):
            print(f"  {i}. {s}")
    if r.get("risks"):
        print("\nRISKS:")
        for i, ri in enumerate(r.get("risks", []), 1):
            print(f"  {i}. {ri}")
    if r.get("saved_to"):
        print(f"\nSaved: {r['saved_to']}")
    print(f"{'='*60}\n")


def main():
    ap = argparse.ArgumentParser(description="ChatGPT (Codex) で change を review")
    ap.add_argument("--commit", help="git rev (e.g. HEAD, HEAD~1, sha)")
    ap.add_argument("--file", help="file path to review")
    ap.add_argument("--diff", help="diff text directly")
    ap.add_argument("--context", default="", help="change の背景説明")
    args = ap.parse_args()

    if args.commit:
        diff, msg = _get_commit_diff(args.commit)
        context = f"{args.context}\ncommit message:\n{msg}"
    elif args.file:
        diff = Path(args.file).read_text(encoding="utf-8")
        context = args.context or f"file: {args.file}"
    elif args.diff:
        diff = args.diff
        context = args.context
    else:
        ap.print_help()
        return 1

    r = review_change(diff, context)
    _print_review(r)

    # CI 用 exit code: REJECT=2, REVIEW_NEEDED=1, APPROVE=0
    return {"APPROVE": 0, "REVIEW_NEEDED": 1, "REJECT": 2}.get(r.get("verdict"), 1)


if __name__ == "__main__":
    sys.exit(main())
