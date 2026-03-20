"""柱3: eBay交渉候補抽出 + 希望価格算出 + メッセージテンプレ生成

利益候補データを元に、eBayでMake Offer（価格交渉）すべき候補を
特定し、希望価格とメッセージテンプレートを自動生成する。

使い方:
    python scripts/negotiate_candidates.py
    python scripts/negotiate_candidates.py --top 10
    python scripts/negotiate_candidates.py --min-profit 10000
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 設定
# ============================================================

USD_JPY_RATE = 150.0
SHIPPING_JPY = 3000
YAHOO_FEE_RATE = 0.088
EBAY_FEE_RATE = 0.13     # eBay出品者手数料（参考）
TARGET_MARGIN = 0.30      # 目標利益率30%（手数料後）

# メッセージテンプレート
OFFER_TEMPLATES = {
    "en_standard": (
        "Hello,\n"
        "I'm interested in this {coin_desc}. "
        "Would you consider ${offer_usd:.2f} for it?\n"
        "I'm a serious buyer and can pay immediately via PayPal.\n"
        "Thank you for your consideration.\n"
        "Best regards"
    ),
    "en_bulk": (
        "Hello,\n"
        "I'm interested in purchasing this {coin_desc} "
        "and possibly other items from your store.\n"
        "Would you accept ${offer_usd:.2f} for this item? "
        "I may buy multiple items if we can work out pricing.\n"
        "Happy to pay promptly.\n"
        "Best regards"
    ),
    "en_return_buyer": (
        "Hi,\n"
        "I've purchased from you before and am interested in this {coin_desc}.\n"
        "Would ${offer_usd:.2f} work for you?\n"
        "As always, I can pay right away.\n"
        "Thanks!"
    ),
}


# ============================================================
# 交渉価格算出
# ============================================================

def calculate_offer_price(yahoo_median_jpy: int, ebay_median_jpy: int) -> dict:
    """eBay交渉価格を逆算

    ヤフオク販売価格（手数料後）から逆算して、
    目標利益率を確保できるeBay購入上限を算出する。

    Returns:
        {
            "yahoo_sell_after_fee": ヤフオク手数料後の手取り,
            "max_buy_jpy": これ以下で買えば利益が出る上限（JPY）,
            "max_buy_usd": USD換算,
            "offer_jpy": 交渉希望価格（上限の85%）,
            "offer_usd": USD換算,
            "profit_at_offer": 希望価格で買えた場合の利益,
            "profit_pct_at_offer": 利益率,
            "breakeven_jpy": 損益分岐点（JPY）,
        }
    """
    yahoo_sell = yahoo_median_jpy
    yahoo_fee = int(yahoo_sell * YAHOO_FEE_RATE)
    yahoo_after_fee = yahoo_sell - yahoo_fee

    # 損益分岐: eBay購入価格 + 送料 = ヤフオク手取り
    breakeven_jpy = yahoo_after_fee - SHIPPING_JPY

    # 目標利益を確保できる上限購入価格
    # 利益 = yahoo_after_fee - (purchase + shipping)
    # 利益率 = 利益 / (purchase + shipping)
    # TARGET_MARGIN = (yahoo_after_fee - purchase - shipping) / (purchase + shipping)
    # (1 + TARGET_MARGIN) * (purchase + shipping) = yahoo_after_fee
    # purchase = yahoo_after_fee / (1 + TARGET_MARGIN) - shipping
    max_buy_jpy = int(yahoo_after_fee / (1 + TARGET_MARGIN) - SHIPPING_JPY)

    # 交渉希望価格 = 上限の85%（交渉余地を残す）
    offer_jpy = int(max_buy_jpy * 0.85)
    offer_usd = round(offer_jpy / USD_JPY_RATE, 2)
    max_buy_usd = round(max_buy_jpy / USD_JPY_RATE, 2)

    # 希望価格で買えた場合の利益
    profit = yahoo_after_fee - offer_jpy - SHIPPING_JPY
    profit_pct = profit / (offer_jpy + SHIPPING_JPY) * 100 if (offer_jpy + SHIPPING_JPY) > 0 else 0

    return {
        "yahoo_sell_after_fee": yahoo_after_fee,
        "max_buy_jpy": max(0, max_buy_jpy),
        "max_buy_usd": max(0, max_buy_usd),
        "offer_jpy": max(0, offer_jpy),
        "offer_usd": max(0, offer_usd),
        "profit_at_offer": profit,
        "profit_pct_at_offer": profit_pct,
        "breakeven_jpy": max(0, breakeven_jpy),
    }


def generate_message(template_key: str, coin_desc: str, offer_usd: float) -> str:
    """交渉メッセージを生成"""
    template = OFFER_TEMPLATES.get(template_key, OFFER_TEMPLATES["en_standard"])
    return template.format(coin_desc=coin_desc, offer_usd=offer_usd)


# ============================================================
# 候補抽出
# ============================================================

def extract_negotiate_candidates(profit_candidates: list, min_profit: int = 5000,
                                  top_n: int = 20) -> list:
    """利益候補から交渉候補を抽出・価格算出"""
    results = []

    for cand in profit_candidates:
        yahoo_median = cand.get("yahoo_median", 0)
        ebay_median = cand.get("ebay_median", 0)

        if not yahoo_median or not ebay_median:
            continue

        pricing = calculate_offer_price(yahoo_median, ebay_median)

        # 希望価格がeBay現在価格より低い場合のみ交渉意味あり
        if pricing["offer_jpy"] >= ebay_median:
            negotiate_type = "不要（現在価格で利益出る）"
        else:
            negotiate_type = "交渉推奨"

        # コイン説明文生成
        key_parts = cand["key"].split("|")
        coin_desc = " ".join([p for p in key_parts if p])

        result = {
            "rank": cand.get("rank", 0),
            "key": cand["key"],
            "coin_desc": coin_desc,
            "ebay_current_jpy": ebay_median,
            "ebay_current_usd": round(ebay_median / USD_JPY_RATE, 2),
            "negotiate_type": negotiate_type,
            **pricing,
            "ebay_count": cand.get("ebay_count", 0),
            "yahoo_count": cand.get("yahoo_count", 0),
            "ebay_title_sample": cand.get("ebay_title", ""),
            "yahoo_title_sample": cand.get("yahoo_title", ""),
        }

        # メッセージ生成
        result["message_standard"] = generate_message(
            "en_standard", coin_desc, pricing["offer_usd"]
        )
        result["message_bulk"] = generate_message(
            "en_bulk", coin_desc, pricing["offer_usd"]
        )

        if pricing["profit_at_offer"] >= min_profit:
            results.append(result)

    results.sort(key=lambda x: -x["profit_at_offer"])
    return results[:top_n]


# ============================================================
# レポート出力
# ============================================================

def print_negotiate_report(candidates: list):
    """交渉候補レポート出力"""
    lines = []
    def add(t=""): lines.append(t)

    add("=" * 110)
    add("  柱3: eBay交渉候補レポート")
    add(f"  基準日: {datetime.now().strftime('%Y-%m-%d')}")
    add(f"  目標利益率: {TARGET_MARGIN*100:.0f}%  |  送料: {SHIPPING_JPY:,}円  |  USD/JPY: {USD_JPY_RATE}")
    add("=" * 110)
    add()

    # サマリーテーブル
    add("# 交渉候補一覧")
    add()
    add(f"{'#':>3} {'条件':<40} {'eBay現在':>10} {'交渉希望':>10} {'上限':>10} {'利益':>12} {'率':>7} {'判定':<16}")
    add("-" * 110)

    for i, c in enumerate(candidates, 1):
        add(f"{i:>3} {c['key'][:39]:<40} "
            f"${c['ebay_current_usd']:>8.0f} "
            f"${c['offer_usd']:>8.0f} "
            f"${c['max_buy_usd']:>8.0f} "
            f"{c['profit_at_offer']:>+11,}円 "
            f"{c['profit_pct_at_offer']:>+6.0f}% "
            f"{c['negotiate_type']:<16}")

    add()
    add()

    # 各候補の詳細 + メッセージテンプレ
    for i, c in enumerate(candidates, 1):
        add("=" * 110)
        add(f"  #{i} {c['key']}")
        add("=" * 110)
        add()
        add(f"  eBay現在中央値:  ${c['ebay_current_usd']:.0f} ({c['ebay_current_jpy']:,}円)")
        add(f"  交渉希望価格:    ${c['offer_usd']:.0f} ({c['offer_jpy']:,}円)  ← eBay現在の{c['offer_jpy']/c['ebay_current_jpy']*100:.0f}%")
        add(f"  購入上限:        ${c['max_buy_usd']:.0f} ({c['max_buy_jpy']:,}円)  ← これ以上は利益率{TARGET_MARGIN*100:.0f}%割れ")
        add(f"  損益分岐:        {c['breakeven_jpy']:,}円  ← これ以上は赤字")
        add(f"  ヤフオク手取り:  {c['yahoo_sell_after_fee']:,}円")
        add(f"  推定利益:        {c['profit_at_offer']:+,}円 ({c['profit_pct_at_offer']:+.0f}%)")
        add(f"  再現性:          eBay {c['ebay_count']}件 / ヤフオク {c['yahoo_count']}件")
        add(f"  判定:            {c['negotiate_type']}")
        add()
        add("  --- メッセージテンプレ（標準） ---")
        for line in c["message_standard"].split("\n"):
            add(f"  {line}")
        add()
        add("  --- メッセージテンプレ（まとめ買い） ---")
        for line in c["message_bulk"].split("\n"):
            add(f"  {line}")
        add()

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================

def main():
    args = sys.argv[1:]

    top_n = 20
    min_profit = 5000
    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1]); i += 2
        elif args[i] == "--min-profit" and i + 1 < len(args):
            min_profit = int(args[i + 1]); i += 2
        else:
            i += 1

    # 利益候補JSONを読み込み
    candidates_path = PROJECT_ROOT / "data" / "profit_candidates_v3.json"
    if not candidates_path.exists():
        print(f"ERROR: {candidates_path} が見つかりません。")
        print("先に cross_market_analysis.py を実行してください。")
        sys.exit(1)

    with open(candidates_path, "r", encoding="utf-8") as f:
        profit_candidates = json.load(f)

    print(f"利益候補 {len(profit_candidates)}件 読み込み")

    # 交渉候補抽出
    negotiate = extract_negotiate_candidates(
        profit_candidates, min_profit=min_profit, top_n=top_n
    )

    print(f"交渉候補 {len(negotiate)}件 抽出")
    print()

    # レポート出力
    report = print_negotiate_report(negotiate)

    # ファイル保存
    output_path = PROJECT_ROOT / "data" / "negotiate_candidates.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"保存: {output_path}")

    # JSON保存
    json_path = PROJECT_ROOT / "data" / "negotiate_candidates.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(negotiate, f, ensure_ascii=False, indent=2, default=str)
    print(f"保存: {json_path}")

    # コンソールにも出力
    try:
        print(report)
    except UnicodeEncodeError:
        print(report.encode("ascii", errors="replace").decode())


if __name__ == "__main__":
    main()
