"""日次為替レート取得・保存スクリプト

サイバーが毎朝7時に実行。Google FinanceからUSD/JPY等を取得し、
CEOルール（+1円→切り上げ）を適用してSupabaseに保存。

Usage:
    python run.py fetch-rates          # 本日分を取得・保存
    python run.py fetch-rates --show   # 現在のレートを表示のみ
"""
import math
import re
import sys
from datetime import date
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client

# Google Finance URLs
FX_URLS = {
    "usd_jpy": "https://www.google.com/finance/quote/USD-JPY",
    "gbp_jpy": "https://www.google.com/finance/quote/GBP-JPY",
    "eur_jpy": "https://www.google.com/finance/quote/EUR-JPY",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch_rate_from_google(pair_url: str) -> float | None:
    """Google Financeから為替レートを取得"""
    try:
        resp = requests.get(pair_url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        # Google Finance HTMLから価格を抽出
        # パターン: data-last-price="159.24" or similar
        m = re.search(r'data-last-price="([\d.]+)"', resp.text)
        if m:
            return float(m.group(1))
        # フォールバック: ページタイトルから
        m = re.search(r'([\d.]+)\s*(?:円|JPY)', resp.text)
        if m:
            return float(m.group(1))
    except Exception as e:
        print(f"  ERROR fetching {pair_url}: {e}")
    return None


def apply_ceo_rule(raw_rate: float) -> int:
    """CEOルール: +1円 → 1円未満切り上げ"""
    return math.ceil(raw_rate + 1)


def fetch_and_save(show_only: bool = False) -> dict:
    """為替レートを取得してSupabaseに保存"""
    today = date.today().isoformat()
    print(f"[fetch_daily_rates] {today}")

    # 為替取得
    rates = {}
    for key, url in FX_URLS.items():
        raw = fetch_rate_from_google(url)
        if raw:
            calc = apply_ceo_rule(raw)
            rates[f"{key}_raw"] = raw
            rates[f"{key}_calc"] = calc
            print(f"  {key}: {raw:.4f} -> {calc} (CEOルール適用)")
        else:
            print(f"  {key}: 取得失敗")

    if not rates.get("usd_jpy_raw"):
        print("  USD/JPY取得失敗。中止。")
        return {}

    if show_only:
        print("  (--show モード: 保存しません)")
        return rates

    # Supabaseにupsert
    record = {
        "rate_date": today,
        "usd_jpy_raw": rates.get("usd_jpy_raw"),
        "usd_jpy_calc": rates.get("usd_jpy_calc"),
        "gbp_jpy_raw": rates.get("gbp_jpy_raw"),
        "gbp_jpy_calc": rates.get("gbp_jpy_calc"),
        "eur_jpy_raw": rates.get("eur_jpy_raw"),
        "eur_jpy_calc": rates.get("eur_jpy_calc"),
        "source": "google_finance",
        "created_by": "coo",
    }

    client = get_client()
    try:
        client.table("daily_rates").upsert(
            record, on_conflict="rate_date"
        ).execute()
        print(f"  Supabase保存完了: {today}")
    except Exception as e:
        print(f"  Supabase保存失敗: {e}")

    return rates


def get_today_rate(currency: str = "usd_jpy") -> int | None:
    """今日の計算用レートをSupabaseから取得（キャップ用）

    Returns:
        計算用レート（CEOルール適用済み）。未取得の場合はNone。
    """
    client = get_client()
    today = date.today().isoformat()
    calc_col = f"{currency}_calc"

    try:
        resp = (client.table("daily_rates")
            .select(calc_col)
            .eq("rate_date", today)
            .limit(1)
            .execute())
        if resp.data:
            return int(resp.data[0][calc_col])
    except Exception:
        pass

    # 今日のデータがない場合、直近のレートを取得
    try:
        resp = (client.table("daily_rates")
            .select(f"rate_date, {calc_col}")
            .order("rate_date", desc=True)
            .limit(1)
            .execute())
        if resp.data:
            print(f"  [WARN] 本日レートなし。直近 {resp.data[0]['rate_date']} のレートを使用。")
            return int(resp.data[0][calc_col])
    except Exception:
        pass

    return None


if __name__ == "__main__":
    show = "--show" in sys.argv
    fetch_and_save(show_only=show)
