"""
auction_cost_calculator.py

オークション別手数料ルールを読み込み、総仕入れコスト（JPY）を計算する。

設計原則:
  - 手数料は auction_fee_rules.json で一元管理（コード直書き禁止）
  - ceo_confirmed=False のルールは計算に使用しない（安全ロック）
  - eBay の計算式を他社に流用しない
  - 新オークション追加時は CEO確認 → JSON更新 → 再計算

手数料設定場所:
  data/auction_fee_rules.json

使い方:
  from scripts.auction_cost_calculator import calc_total_cost_jpy, get_fee_rule

  # Heritage のロットを計算
  cost = calc_total_cost_jpy(
      hammer_price=1200.0,
      source="heritage",
      fx_rate=150.0,
  )
  # → float: JPY換算の総仕入れコスト

  # CEO承認済みルールのみ使う安全版
  cost = calc_total_cost_jpy(1200.0, "heritage", 150.0, require_confirmed=True)
  # → ceo_confirmed=False の場合は None を返す（計算スキップ）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── パス ──────────────────────────────────────────────────────────
_DIR           = Path(__file__).parent
_FEE_RULES_FILE = _DIR.parent / "data" / "auction_fee_rules.json"

# ── キャッシュ ────────────────────────────────────────────────────
_fee_rules_cache: Optional[dict[str, dict]] = None


def _load_fee_rules(force_reload: bool = False) -> dict[str, dict]:
    """
    auction_fee_rules.json を読み込んで {source: rule_dict} のキャッシュを返す。
    force_reload=True で再読み込み。
    """
    global _fee_rules_cache
    if _fee_rules_cache is not None and not force_reload:
        return _fee_rules_cache

    if not _FEE_RULES_FILE.exists():
        logger.error(f"fee_rules ファイルが見つかりません: {_FEE_RULES_FILE}")
        _fee_rules_cache = {}
        return {}

    try:
        data = json.loads(_FEE_RULES_FILE.read_text(encoding="utf-8"))
        _fee_rules_cache = {
            rule["source"]: rule
            for rule in data.get("fee_rules", [])
        }
        logger.debug(f"fee_rules 読み込み: {len(_fee_rules_cache)}件")
        return _fee_rules_cache
    except Exception as e:
        logger.error(f"fee_rules 読み込みエラー: {e}")
        _fee_rules_cache = {}
        return {}


def reload_fee_rules() -> None:
    """手数料設定を再読み込み（設定ファイル更新後に呼ぶ）。"""
    _load_fee_rules(force_reload=True)


def get_fee_rule(source: str) -> Optional[dict]:
    """
    指定ソースの手数料ルールを返す。
    見つからない場合は None。
    """
    rules = _load_fee_rules()
    return rules.get(source)


def is_ceo_confirmed(source: str) -> bool:
    """
    指定ソースが CEO 承認済みかを返す。
    ルール自体が存在しない場合は False。
    """
    rule = get_fee_rule(source)
    if rule is None:
        return False
    return bool(rule.get("ceo_confirmed", False))


# ── コスト計算 ────────────────────────────────────────────────────

def calc_total_cost_jpy(
    hammer_price: float,
    source: str,
    fx_rate: float,
    require_confirmed: bool = True,
) -> Optional[float]:
    """
    落札価格（ハンマープライス）から総仕入れコスト（JPY）を計算。

    計算式:
      step1: hammer × (1 + buyer_premium + cc_fee) = subtotal_in_currency
      step2: subtotal × fx_rate × (1 + fx_buffer) = subtotal_jpy
      step3: subtotal_jpy + shipping_jpy + transfer_jpy + insurance_jpy = pretax_jpy
      step4: pretax_jpy × (1 + import_tax) = total_cost_jpy

    Args:
        hammer_price      : 落札価格（現地通貨）
        source            : オークションソース ('heritage', 'noble', etc.)
        fx_rate           : 為替レート（現地通貨 → JPY）
        require_confirmed : True の場合、ceo_confirmed=False のルールは None を返す

    Returns:
        total_cost_jpy: JPY換算の総仕入れコスト
        None          : ルール未定義 or CEO未承認（require_confirmed=True 時）
    """
    if hammer_price <= 0:
        return 0.0

    rule = get_fee_rule(source)
    if rule is None:
        logger.warning(f"  [cost_calc] {source}: 手数料ルール未定義")
        return None

    if require_confirmed and not rule.get("ceo_confirmed", False):
        logger.info(
            f"  [cost_calc] {source}: CEO未承認ルール — 計算スキップ "
            f"({rule.get('confirmed_note', '')[:50]})"
        )
        return None

    # step1: バイヤーズプレミアム + CC手数料
    bp   = float(rule.get("buyer_premium_pct") or 0.0)
    cc   = float(rule.get("cc_fee_pct") or 0.0)
    subtotal_currency = hammer_price * (1.0 + bp + cc)

    # step2: 為替換算 + FXバッファ
    fx_buf = float(rule.get("fx_buffer_pct") or 0.0)
    subtotal_jpy = subtotal_currency * fx_rate * (1.0 + fx_buf)

    # step3: 送料・転送・保険
    shipping_usd = float(rule.get("shipping_usd") or 0.0)
    transfer_jpy = float(rule.get("transfer_jpy") or 0.0)
    insurance_pct = float(rule.get("insurance_pct") or 0.0)
    other_fees    = float(rule.get("other_fees_jpy") or 0.0)

    shipping_jpy  = shipping_usd * fx_rate   # USD建て送料 → JPY
    insurance_jpy = hammer_price * fx_rate * insurance_pct

    pretax_jpy = subtotal_jpy + shipping_jpy + transfer_jpy + insurance_jpy + other_fees

    # step4: 関税
    import_tax_pct = float(rule.get("import_tax_pct") or 0.0)
    total_cost_jpy = pretax_jpy * (1.0 + import_tax_pct)

    return round(total_cost_jpy, 0)


def calc_margin_pct(
    total_cost_jpy: float,
    buy_limit_jpy: float,
) -> Optional[float]:
    """
    (buy_limit - cost) / buy_limit の利益率を返す。
    buy_limit が 0 以下の場合は None。
    """
    if not buy_limit_jpy or buy_limit_jpy <= 0:
        return None
    return (buy_limit_jpy - total_cost_jpy) / buy_limit_jpy


def enrich_lot_with_cost(
    lot: dict,
    fx_rate: float,
    buy_limit_jpy: Optional[float] = None,
    require_confirmed: bool = True,
) -> dict:
    """
    overseas_lot dict に estimated_cost_jpy / estimated_margin_pct を付加して返す。
    buy_limit_jpy が None の場合は management_no から coin_slab_data を参照しない
    （呼び出し元で設定する設計）。

    副作用なし（元の lot を変更しない）。
    """
    lot = dict(lot)
    source     = lot.get("source", "unknown")
    price      = float(lot.get("current_price") or 0.0)
    currency   = lot.get("currency", "USD")

    cost = calc_total_cost_jpy(
        hammer_price=price,
        source=source,
        fx_rate=fx_rate,
        require_confirmed=require_confirmed,
    )

    lot["estimated_cost_jpy"]  = cost
    lot["fx_rate"]             = fx_rate
    lot["buy_limit_jpy"]       = buy_limit_jpy

    if cost is not None and buy_limit_jpy:
        lot["estimated_margin_pct"] = calc_margin_pct(cost, buy_limit_jpy)
    else:
        lot["estimated_margin_pct"] = None

    # price_jpy: 手数料なしの参考換算
    if price > 0:
        lot["price_jpy"] = int(price * fx_rate)

    return lot


# ── 表示用 ────────────────────────────────────────────────────────

def print_fee_rules_summary() -> None:
    """手数料ルールのサマリーをコンソール出力。"""
    rules = _load_fee_rules()
    print(f"\n=== 手数料ルール一覧 ({len(rules)}件) ===\n")
    print(f"{'ソース':<15} {'表示名':<25} {'BP%':>5} {'CC%':>5} "
          f"{'FX buf':>7} {'送料USD':>7} {'転送JPY':>7} {'関税':>5} {'CEO確認':>6}")
    print("-" * 85)
    for source, rule in sorted(rules.items()):
        confirmed = "✅" if rule.get("ceo_confirmed") else "❌ 未確認"
        print(
            f"{source:<15} {rule.get('display_name',''):<25} "
            f"{float(rule.get('buyer_premium_pct',0))*100:>4.0f}% "
            f"{float(rule.get('cc_fee_pct',0))*100:>4.0f}% "
            f"{float(rule.get('fx_buffer_pct',0))*100:>6.0f}% "
            f"{float(rule.get('shipping_usd',0)):>6.0f}$ "
            f"{int(rule.get('transfer_jpy',0)):>6,}円 "
            f"{float(rule.get('import_tax_pct',0))*100:>4.0f}% "
            f"{confirmed}"
        )
    print()
    unconfirmed = [s for s, r in rules.items() if not r.get("ceo_confirmed")]
    if unconfirmed:
        print(f"⚠️  CEO未承認: {', '.join(unconfirmed)}")
        print("   → data/auction_fee_rules.json を更新し ceo_confirmed=true にしてください")
    print()


def calc_cost_breakdown(
    hammer_price: float,
    source: str,
    fx_rate: float,
) -> dict:
    """
    コスト内訳を dict で返す（デバッグ・報告用）。
    CEO未承認の場合でも計算する（内訳確認目的）。
    """
    rule = get_fee_rule(source) or {}
    bp             = float(rule.get("buyer_premium_pct") or 0)
    cc             = float(rule.get("cc_fee_pct") or 0)
    fx_buf         = float(rule.get("fx_buffer_pct") or 0)
    shipping_usd   = float(rule.get("shipping_usd") or 0)
    transfer_jpy   = float(rule.get("transfer_jpy") or 0)
    insurance_pct  = float(rule.get("insurance_pct") or 0)
    import_tax_pct = float(rule.get("import_tax_pct") or 0)
    other_fees     = float(rule.get("other_fees_jpy") or 0)

    buyers_premium_amt = hammer_price * bp
    cc_fee_amt         = hammer_price * cc
    subtotal_currency  = hammer_price + buyers_premium_amt + cc_fee_amt
    subtotal_jpy       = subtotal_currency * fx_rate * (1 + fx_buf)
    shipping_jpy       = shipping_usd * fx_rate
    insurance_jpy      = hammer_price * fx_rate * insurance_pct
    pretax_jpy         = subtotal_jpy + shipping_jpy + transfer_jpy + insurance_jpy + other_fees
    import_tax_jpy     = pretax_jpy * import_tax_pct
    total_jpy          = pretax_jpy + import_tax_jpy

    return {
        "source":              source,
        "ceo_confirmed":       rule.get("ceo_confirmed", False),
        "hammer_price":        hammer_price,
        "buyers_premium_amt":  round(buyers_premium_amt, 2),
        "cc_fee_amt":          round(cc_fee_amt, 2),
        "subtotal_currency":   round(subtotal_currency, 2),
        "fx_rate":             fx_rate,
        "fx_buffer_pct":       fx_buf,
        "subtotal_jpy":        round(subtotal_jpy),
        "shipping_jpy":        round(shipping_jpy),
        "transfer_jpy":        round(transfer_jpy),
        "insurance_jpy":       round(insurance_jpy),
        "other_fees_jpy":      round(other_fees),
        "pretax_jpy":          round(pretax_jpy),
        "import_tax_jpy":      round(import_tax_jpy),
        "total_cost_jpy":      round(total_jpy),
    }


# ── スタンドアロン実行 ────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="手数料ルール確認・コスト計算")
    parser.add_argument("--list",   action="store_true", help="ルール一覧表示")
    parser.add_argument("--calc",   type=float, help="落札価格（現地通貨）でコスト計算")
    parser.add_argument("--source", default="heritage", help="オークションソース")
    parser.add_argument("--fx",     type=float, default=150.0, help="為替レート")
    parser.add_argument("--breakdown", action="store_true", help="内訳表示")
    args = parser.parse_args()

    if args.list:
        print_fee_rules_summary()

    if args.calc:
        if args.breakdown:
            bd = calc_cost_breakdown(args.calc, args.source, args.fx)
            print(f"\n=== コスト内訳 ({args.source} / {args.calc:.0f} @ {args.fx} JPY) ===")
            for k, v in bd.items():
                if isinstance(v, (int, float)) and v != 0:
                    print(f"  {k:<25}: {v:>12,.0f}")
                else:
                    print(f"  {k:<25}: {v}")
        else:
            cost = calc_total_cost_jpy(args.calc, args.source, args.fx, require_confirmed=False)
            print(f"\n{args.source} 落札価格 {args.calc:.0f} → 総仕入れコスト: ¥{cost:,.0f}")
            if not is_ceo_confirmed(args.source):
                print("⚠️  このソースはCEO未承認です。実際の仕入れには使用しないでください。")
