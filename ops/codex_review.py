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

# CEO 指示 (2026-05-16):「Codex 使用した場合は毎回 使用分を報告」
# 料金表 (USD / 1M tokens). 最新値は OpenAI pricing page で要確認.
# 不明 model は input=10.0/output=30.0 で保守的概算.
USD_PER_1M = {
    "gpt-5":          {"input": 1.25,  "output": 10.0},
    "gpt-5-mini":     {"input": 0.25,  "output": 2.0},
    "gpt-4o":         {"input": 2.5,   "output": 10.0},
    "gpt-4o-mini":    {"input": 0.15,  "output": 0.6},
}
USD_JPY = 155.0  # 概算 USD→JPY (毎月見直し)
USAGE_LOG = REVIEWS_DIR / "_usage_log.jsonl"


def _calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
    rate = USD_PER_1M.get(model, {"input": 10.0, "output": 30.0})
    in_usd = prompt_tokens * rate["input"] / 1_000_000
    out_usd = completion_tokens * rate["output"] / 1_000_000
    total_usd = in_usd + out_usd
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "input_usd": round(in_usd, 6),
        "output_usd": round(out_usd, 6),
        "total_usd": round(total_usd, 6),
        "total_jpy": round(total_usd * USD_JPY, 4),
        "rate_used": rate,
        "usd_jpy": USD_JPY,
    }


def _append_usage_log(entry: dict) -> None:
    try:
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _cumulative_usage() -> dict:
    """USAGE_LOG を全集計 (今月 / 累計)."""
    if not USAGE_LOG.exists():
        return {"month_usd": 0, "month_jpy": 0, "all_usd": 0, "all_jpy": 0, "month_calls": 0, "all_calls": 0}
    from datetime import date
    today = date.today()
    ym = f"{today.year:04d}-{today.month:02d}"
    m_usd = a_usd = 0.0
    m_calls = a_calls = 0
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            a_usd += e.get("total_usd", 0); a_calls += 1
            if str(e.get("timestamp", "")).startswith(ym):
                m_usd += e.get("total_usd", 0); m_calls += 1
        except Exception:
            continue
    return {
        "month_usd": round(m_usd, 4), "month_jpy": round(m_usd * USD_JPY, 2),
        "all_usd": round(a_usd, 4), "all_jpy": round(a_usd * USD_JPY, 2),
        "month_calls": m_calls, "all_calls": a_calls,
    }

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
    """OpenAI API key を取得 (環境変数 or 専用 .env).

    Codex review 指摘 (2026-05-16): 越境 .env 走査廃止. 単一公式経路のみ.
    """
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    # 専用 .env のみ (越境走査廃止)
    env_path = REPO_ROOT / "credentials" / "openai.env"
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
        # CEO 5/16 指示: 使用 token + 概算費用を毎回記録
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = _calc_cost(MODEL, u.prompt_tokens, u.completion_tokens)
        else:
            usage = _calc_cost(MODEL, 0, 0)
    except Exception as e:
        return {"verdict": "REVIEW_NEEDED", "summary": f"OpenAI call err: {e}", "issues": [str(e)], "suggestions": [], "risks": [], "raw": "", "usage": None}

    ts = datetime.now().isoformat(timespec="seconds")
    data["raw"] = raw
    data["timestamp"] = ts
    data["context"] = context
    data["model"] = MODEL
    data["usage"] = usage

    # 使用量を _usage_log.jsonl に追記 (月次・累計 集計用)
    _append_usage_log({
        "timestamp": ts, "model": MODEL,
        "context": context[:120],
        **usage,
    })
    data["cumulative"] = _cumulative_usage()

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
    # CEO 5/16 指示: 使用量必ず報告
    u = r.get("usage") or {}
    c = r.get("cumulative") or {}
    if u:
        print(f"\nUSAGE    : tokens in={u.get('prompt_tokens',0):,} / out={u.get('completion_tokens',0):,} / total={u.get('total_tokens',0):,}")
        print(f"COST     : ${u.get('total_usd',0):.4f} ~= JPY{u.get('total_jpy',0):.2f}  (in ${u.get('input_usd',0):.4f} + out ${u.get('output_usd',0):.4f})")
    if c:
        print(f"MONTH    : ${c.get('month_usd',0):.4f} ~= JPY{c.get('month_jpy',0):.2f}  ({c.get('month_calls',0)} calls 今月)")
        print(f"CUM      : ${c.get('all_usd',0):.4f} ~= JPY{c.get('all_jpy',0):.2f}  ({c.get('all_calls',0)} calls 累計)")
    if r.get("saved_to"):
        print(f"\nSaved: {r['saved_to']}")
    print(f"{'='*60}\n")


def print_usage_summary():
    """累計 + 今月の Codex 使用量を表示 (毎回 review 報告 + 単独確認用)."""
    c = _cumulative_usage()
    print(f"\n[Codex 使用量]")
    print(f"  今月: ${c['month_usd']:.4f} ~= JPY{c['month_jpy']:.2f}  ({c['month_calls']} calls)")
    print(f"  累計: ${c['all_usd']:.4f} ~= JPY{c['all_jpy']:.2f}  ({c['all_calls']} calls)")
    return c


def main():
    ap = argparse.ArgumentParser(description="ChatGPT (Codex) で change を review")
    ap.add_argument("--commit", help="git rev (e.g. HEAD, HEAD~1, sha)")
    ap.add_argument("--file", help="file path to review")
    ap.add_argument("--diff", help="diff text directly")
    ap.add_argument("--context", default="", help="change の背景説明")
    ap.add_argument("--usage", action="store_true", help="累計+今月の使用量のみ表示して終了")
    args = ap.parse_args()

    if args.usage:
        print_usage_summary()
        return 0

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
