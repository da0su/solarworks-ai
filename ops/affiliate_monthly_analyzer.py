#!/usr/bin/env python3
"""楽天アフィリエイト 月次レポート分析ツール (2026-06-19 新設)

CEO 6/19 「点ではなく線で捉えろ」指示に基づき作成。
Downloads フォルダの shop_*.csv を読み、月次トレンド + 勝ち筋/死蔵 候補を
抽出して state/sales_winners_blacklist.json の更新候補を提案する。

楽天アフィリエイト管理画面で月次「期間別成果 ショップ別」CSV を Downloads に
保存しておけば、このスクリプトが集計する。

Usage:
    python ops/affiliate_monthly_analyzer.py                    # Downloads から自動検出
    python ops/affiliate_monthly_analyzer.py --files a.csv b.csv  # 直接指定
    python ops/affiliate_monthly_analyzer.py --emit-json         # JSON 出力 (CI 連携用)
    python ops/affiliate_monthly_analyzer.py --propose-update    # SSOT JSON 更新案を生成
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
SSOT_PATH = REPO_ROOT / "state" / "sales_winners_blacklist.json"

# blacklist 候補閾値
BLACKLIST_SINGLE_MONTH_CLK = 5    # 単月 click>=5 で sales=0 → 候補
BLACKLIST_MULTI_MONTH_CNT = 2     # 2ヶ月以上連続出現で sales=0 → 候補


def _load_csv(path: Path) -> tuple[str, list[tuple[str, int, int, int, int]]]:
    """1ヶ月分の CSV を読込。

    エンコーディング: utf-8-sig 優先・失敗時 cp932 にフォールバック
    (楽天アフィリエイト管理画面は utf-8-sig で出力するが、Excel 経由保存等で
     cp932 になる可能性に対応)。

    Returns:
        (期間ラベル "YYYY.MM", [(shop_name, rewards, clicks, sales, amount), ...])
    """
    lines: list[str] | None = None
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc) as f:
                lines = f.readlines()
            break
        except UnicodeDecodeError:
            continue
    if lines is None:
        print(f"  [skip] エンコーディング判別失敗: {path.name}", file=sys.stderr)
        return "", []
    period = ""
    if lines:
        m = re.search(r"(\d{4}\.\d{2})", lines[0])
        if m:
            period = m.group(1)
    reader = csv.reader(io.StringIO("".join(lines[4:])))
    rows: list[tuple[str, int, int, int, int]] = []
    for r in reader:
        if not r or len(r) < 5 or not r[0]:
            continue
        try:
            rows.append((r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4])))
        except ValueError:
            continue
    return period, rows


def _autoscan(downloads: Path) -> list[Path]:
    """Downloads 配下の shop*.csv を検出"""
    if not downloads.exists():
        return []
    return sorted(downloads.glob("shop*.csv"))


def analyze(files: list[Path]) -> dict:
    """月次レポート群を集計

    同 period 複数ファイルがある場合は mtime 新しい方を採用 (Downloads 重複対策)
    """
    monthly: dict[str, list[tuple[str, int, int, int, int]]] = {}
    monthly_src: dict[str, Path] = {}
    for p in files:
        period, rows = _load_csv(p)
        if not period or not rows:
            print(f"  [skip] 期間/データ未検出: {p.name}", file=sys.stderr)
            continue
        # 同 period 複数ファイルは mtime 新しい方
        if period in monthly_src:
            cur_mt = monthly_src[period].stat().st_mtime
            if p.stat().st_mtime <= cur_mt:
                print(f"  [skip] {period} 既に新しいデータあり: {p.name}", file=sys.stderr)
                continue
            print(f"  [override] {period} を {p.name} で上書き (mtime 新)", file=sys.stderr)
        monthly[period] = rows
        monthly_src[period] = p
    if not monthly:
        return {"error": "no valid monthly data"}

    months = sorted(monthly.keys())
    summary: list[dict] = []
    for m in months:
        rows = monthly[m]
        clk = sum(x[2] for x in rows)
        sal = sum(x[3] for x in rows)
        amt = sum(x[4] for x in rows)
        rew = sum(x[1] for x in rows)
        cvr = sal / clk * 100 if clk else 0.0
        summary.append({
            "month": m, "shops": len(rows), "clicks": clk, "sales": sal,
            "amount": amt, "rewards": rew, "cvr_pct": round(cvr, 2),
        })

    # 全月通算 winners (任意月で sales>0)
    winner_map: dict[str, dict] = {}
    for m, rows in monthly.items():
        for name, rew, clk, sal, amt in rows:
            if sal > 0:
                w = winner_map.setdefault(name, {"shop_name": name, "months": [],
                                                  "rewards_total": 0, "sales_total": 0, "amount_total": 0})
                w["months"].append(m)
                w["rewards_total"] += rew
                w["sales_total"] += sal
                w["amount_total"] += amt
    winners = sorted(winner_map.values(), key=lambda x: -x["rewards_total"])

    # blacklist 候補: 任意月で sales=0 かつ
    #   (a) 単月 click >= BLACKLIST_SINGLE_MONTH_CLK
    #   (b) BLACKLIST_MULTI_MONTH_CNT ヶ月以上出現
    shop_appearance: dict[str, dict] = {}
    for m, rows in monthly.items():
        for name, rew, clk, sal, amt in rows:
            s = shop_appearance.setdefault(name, {"shop_name": name, "months_no_sale": [],
                                                   "months_any": [], "clicks_total": 0, "sales_total": 0})
            s["months_any"].append(m)
            s["clicks_total"] += clk
            s["sales_total"] += sal
            if sal == 0:
                s["months_no_sale"].append(m)
    blacklist_candidates = []
    for name, s in shop_appearance.items():
        if s["sales_total"] > 0:
            continue
        single_clk_max = max(
            (rows[2] for m, rows in (
                (m, next(((n, r, c, sa, a) for n, r, c, sa, a in monthly[m] if n == name), None))
                for m in s["months_any"]
            ) if rows is not None),
            default=0,
        )
        if single_clk_max >= BLACKLIST_SINGLE_MONTH_CLK or \
           len(s["months_no_sale"]) >= BLACKLIST_MULTI_MONTH_CNT:
            blacklist_candidates.append({
                "shop_name": name,
                "clicks_total": s["clicks_total"],
                "months_no_sale": s["months_no_sale"],
                "single_clk_max": single_clk_max,
                "reason": (
                    f"単月clk_max={single_clk_max} / no_sale_months={len(s['months_no_sale'])}"
                ),
            })
    blacklist_candidates.sort(key=lambda x: -x["clicks_total"])

    # 価格分析 (winner の単価)
    unit_prices = []
    for w in winners:
        if w["sales_total"]:
            unit_prices.append(w["amount_total"] // w["sales_total"])
    price_stats = {}
    if unit_prices:
        price_stats = {
            "count": len(unit_prices),
            "min": min(unit_prices),
            "max": max(unit_prices),
            "median": int(statistics.median(unit_prices)),
            "mean": int(statistics.mean(unit_prices)),
            "p25": int(statistics.quantiles(unit_prices, n=4)[0]) if len(unit_prices) >= 4 else None,
            "p75": int(statistics.quantiles(unit_prices, n=4)[2]) if len(unit_prices) >= 4 else None,
        }

    return {
        "files_loaded": [str(p) for p in files],
        "months": months,
        "monthly_summary": summary,
        "winners": winners,
        "blacklist_candidates": blacklist_candidates,
        "price_stats": price_stats,
    }


def _print_human(result: dict) -> None:
    print("=" * 78)
    print("楽天アフィリエイト 月次レポート集計")
    print("=" * 78)
    print(f"対象ファイル数: {len(result['files_loaded'])}")
    print()
    print("【月次トレンド】")
    print(f'  {"月":<10}{"shop":>6}{"clk":>6}{"sales":>7}{"売上額":>11}{"報酬":>9}{"CVR":>7}')
    for s in result["monthly_summary"]:
        print(f'  {s["month"]:<10}{s["shops"]:>6}{s["clicks"]:>6}{s["sales"]:>7}'
              f'{s["amount"]:>11,}{s["rewards"]:>9,}{s["cvr_pct"]:>6.2f}%')
    print()
    print(f"【winners 候補】(全月通算 sales>0) 計 {len(result['winners'])} shop")
    for w in result["winners"][:30]:
        print(f"  JPY{w['rewards_total']:>5} sales={w['sales_total']} amt=JPY{w['amount_total']:>7,} "
              f"months={','.join(w['months'])} : {w['shop_name']}")
    if len(result["winners"]) > 30:
        print(f"  ... 他 {len(result['winners']) - 30} shop")
    print()
    print(f"【blacklist 候補】(click 死蔵) 計 {len(result['blacklist_candidates'])} shop")
    for b in result["blacklist_candidates"][:20]:
        print(f"  clk_total={b['clicks_total']:>3} {b['reason']} : {b['shop_name']}")
    if result.get("price_stats"):
        ps = result["price_stats"]
        print()
        print(f"【単価分布】winners 全 {ps['count']} 件")
        print(f"  min=JPY{ps['min']:,} / max=JPY{ps['max']:,} / median=JPY{ps['median']:,} / mean=JPY{ps['mean']:,}")
        if ps.get("p25") is not None:
            print(f"  P25=JPY{ps['p25']:,} / P75=JPY{ps['p75']:,} → 推奨価格帯")


def _propose_ssot_update(result: dict) -> dict:
    """SSOT JSON 更新案を生成 (既存 SSOT との diff を提示)"""
    proposal = {
        "schema_version": 1,
        "generated_at_note": "affiliate_monthly_analyzer.py による提案。手動レビュー後 state/sales_winners_blacklist.json に転記",
        "source_months": result["months"],
        "winners": [{"shop_name": w["shop_name"], "category": "todo_classify",
                     "months": w["months"], "rewards": w["rewards_total"]}
                    for w in result["winners"]],
        "blacklist": [{"shop_name": b["shop_name"], "clicks_total": b["clicks_total"],
                       "months_no_sale": b["months_no_sale"], "reason": b["reason"]}
                      for b in result["blacklist_candidates"]],
    }
    # 既存 SSOT と差分
    if SSOT_PATH.exists():
        existing = json.loads(SSOT_PATH.read_text(encoding="utf-8"))
        ex_w = {e["shop_name"] for e in existing.get("winners", [])}
        ex_b = {e["shop_name"] for e in existing.get("blacklist", [])}
        new_w = {w["shop_name"] for w in proposal["winners"]}
        new_b = {b["shop_name"] for b in proposal["blacklist"]}
        proposal["diff_vs_current_ssot"] = {
            "winners_added": sorted(new_w - ex_w),
            "winners_removed": sorted(ex_w - new_w),
            "blacklist_added": sorted(new_b - ex_b),
            "blacklist_removed": sorted(ex_b - new_b),
        }
    return proposal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", nargs="+", help="CSV ファイルパス (省略時 Downloads 自動検出)")
    ap.add_argument("--downloads", default=str(DEFAULT_DOWNLOADS),
                    help=f"Downloads パス (default: {DEFAULT_DOWNLOADS})")
    ap.add_argument("--emit-json", action="store_true", help="JSON 出力")
    ap.add_argument("--propose-update", action="store_true",
                    help="SSOT JSON 更新案を出力 (既存 SSOT との diff つき)")
    args = ap.parse_args()

    if args.files:
        files = [Path(p) for p in args.files]
    else:
        files = _autoscan(Path(args.downloads))
    files = [p for p in files if p.exists()]
    if not files:
        print(f"[error] CSV 未検出 (Downloads={args.downloads})", file=sys.stderr)
        return 1
    result = analyze(files)
    if "error" in result:
        print(f"[error] {result['error']}", file=sys.stderr)
        return 2

    if args.propose_update:
        proposal = _propose_ssot_update(result)
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
    elif args.emit_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
