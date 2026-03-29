"""
海外オークション落札データ取得スクリプト

対象ソース（優先順）:
  1. Heritage Auctions  (HTML scraping / archive search)
  2. Stack's Bowers     (archive.stacksbowers.com)
  3. Noonans / Spink    (NumisBids 経由 / 将来実装)

取得データ → market_transactions テーブルへ upsert
  source = "heritage" / "stacks_bowers" / "noonans" 等
  platform フィールドで出典を区別

使い方:
  python run.py overseas-fetch                    # 全ソース・全コイン
  python run.py overseas-fetch --source heritage  # Heritage のみ
  python run.py overseas-fetch --coin 001001      # 特定管理番号のみ
  python run.py overseas-fetch --dry-run          # DB投入なし
"""

import re
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ── パス設定 ────────────────────────────────────────────
_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))
from supabase_client import get_client, make_dedup_key

# ── ロガー ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── 定数 ────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BATCH_SIZE  = 50
REQUEST_DELAY = 2.0   # 秒（リクエスト間隔）

# auction_schedule.json パス
SCHEDULE_FILE = _DIR.parent / "data" / "auction_schedule.json"

# ── ヘルパー ────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _load_schedule() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        logger.warning(f"auction_schedule.json が見つかりません: {SCHEDULE_FILE}")
        return []
    with open(SCHEDULE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("auctions", [])


def _active_auctions(days_after: int = 14) -> list[dict]:
    """現在開催中 or 直近 days_after 日以内に終了したオークションを返す"""
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days_after)
    result = []
    for a in _load_schedule():
        try:
            end_d = datetime.fromisoformat(a["end_date"]).date()
            start_d = datetime.fromisoformat(a["start_date"]).date()
            if start_d <= today and end_d >= cutoff:
                result.append(a)
        except (ValueError, KeyError):
            pass
    return result


def _load_coin_slab_data(mgmt_no: Optional[str] = None) -> list[dict]:
    """coin_slab_data から対象コインを取得"""
    client = get_client()
    data = []
    offset = 0
    while True:
        # ページごとに query を新規構築（builderの再利用を避ける）
        q = (
            client.table("coin_slab_data")
            .select("id,management_no,grader,slab_line1,slab_line2,grade,material,ref2_yahoo_price_jpy")
            .eq("status", "completed_hit")
            .gt("ref2_yahoo_price_jpy", 0)
            .order("id")
            .range(offset, offset + 499)
        )
        if mgmt_no:
            q = q.eq("management_no", mgmt_no)
        r = q.execute()
        if not r.data:
            break
        data.extend(r.data)
        if len(r.data) < 500:
            break
        offset += len(r.data)
    return data


def _upsert_records(records: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """market_transactions へ upsert"""
    if not records:
        return 0, 0
    if dry_run:
        logger.info(f"[DRY-RUN] {len(records)}件（DB投入スキップ）")
        return len(records), 0
    client = get_client()
    ok = 0
    ng = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        try:
            resp = client.table("market_transactions").upsert(
                batch, on_conflict="dedup_key"
            ).execute()
            ok += len(resp.data)
        except Exception as e:
            logger.warning(f"  upsert エラー: {e}")
            ng += len(batch)
    return ok, ng


# ============================================================
# Heritage Auctions フェッチャー
# ============================================================

def _parse_heritage_html(html: str, source_label: str = "heritage") -> list[dict]:
    """Heritage の検索結果 HTML から落札データを抽出"""
    records = []

    # ロット情報ブロックを抽出（ATG framework）
    # タイトル: <span class="item-title">...</span> 系
    # 価格:   "Realized: $X,XXX" / "Estimate: $X,XXX" パターン
    # 日付:   "Sale Date: Month DD, YYYY" パターン

    # ロットブロック正規表現
    blocks = re.findall(
        r'(?s)<(?:div|article)[^>]+class="[^"]*(?:item-title|lot-title|search-item)[^"]*"[^>]*>.*?(?=<(?:div|article)[^>]+class="[^"]*(?:item-title|lot-title|search-item)|$)',
        html,
    )

    # シンプルな正規表現で fallback: タイトルと価格を別々に抽出
    titles = re.findall(
        r'<(?:span|h\d|div)[^>]+class="[^"]*(?:lot-title|item-title|title)[^"]*"[^>]*>\s*(.*?)\s*</(?:span|h\d|div)>',
        html, re.IGNORECASE,
    )
    prices = re.findall(
        r'[Rr]ealized[\s:$]*([0-9,]+)',
        html,
    )
    dates = re.findall(
        r'(?:Sale\s+Date|Sold)[:\s]+([A-Za-z]+ \d{1,2},? \d{4})',
        html,
    )
    urls = re.findall(
        r'href="(https://coins\.ha\.com/itm/[^"]+)"',
        html,
    )

    for i, title in enumerate(titles):
        title_clean = re.sub(r'<[^>]+>', '', title).strip()
        if not title_clean:
            continue
        price_raw = prices[i] if i < len(prices) else ""
        date_raw  = dates[i] if i < len(dates) else ""
        url       = urls[i]  if i < len(urls)  else ""

        price_usd = 0
        if price_raw:
            try:
                price_usd = int(price_raw.replace(",", ""))
            except ValueError:
                pass

        sold_date = ""
        if date_raw:
            try:
                sold_date = datetime.strptime(
                    date_raw.replace(",", ""), "%B %d %Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

        if not title_clean or price_usd <= 0:
            continue

        grader = "NGC" if "NGC" in title_clean.upper() else (
                  "PCGS" if "PCGS" in title_clean.upper() else "")
        year_m = re.search(r'\b(1[5-9]\d{2}|20\d{2})\b', title_clean)
        year   = year_m.group(1) if year_m else ""

        dedup_key = make_dedup_key(
            source=source_label,
            url=url or None,
            title=title_clean,
            price=price_usd,
            sold_date=sold_date,
        )

        records.append({
            "source":    source_label,
            "platform":  "heritage",
            "title":     title_clean,
            "price_jpy": 0,          # USD→JPY換算は stats 側で対応
            "price_usd": price_usd,
            "sold_date": sold_date or None,
            "url":       url or None,
            "grader":    grader,
            "year":      year,
            "dedup_key": dedup_key,
        })

    logger.info(f"  [Heritage HTML] {len(records)}件パース")
    return records


def fetch_heritage(coins: list[dict], session: requests.Session,
                   dry_run: bool = False) -> int:
    """Heritage Auctions から各コインの落札履歴を取得"""
    total_new = 0

    for coin in coins:
        mgmt_no = coin.get("management_no", "")
        grader  = (coin.get("grader") or "").upper()
        line1   = coin.get("slab_line1", "") or ""
        grade   = coin.get("grade", "") or ""

        # 検索クエリ: "NGC 1904 $20 MS62" 形式
        year_m = re.search(r'\b(1[5-9]\d{2}|20\d{2})\b', line1)
        year   = year_m.group(1) if year_m else ""
        query  = f"{grader} {year} {grade}".strip()
        if len(query) < 5:
            continue

        url = (
            f"https://coins.ha.com/c/search/results.zx"
            f"?dept=1909&sold_status=1526~1524&mode=archive"
            f"&term={requests.utils.quote(query)}&limit=25"
        )
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                logger.warning(f"  [{mgmt_no}] Heritage HTTP {resp.status_code}")
                time.sleep(REQUEST_DELAY)
                continue
            recs = _parse_heritage_html(resp.text, source_label="heritage")
            ok, ng = _upsert_records(recs, dry_run=dry_run)
            total_new += ok
            logger.info(f"  [{mgmt_no}] Heritage: {len(recs)}件パース → {ok}件DB登録")
        except Exception as e:
            logger.warning(f"  [{mgmt_no}] Heritage 取得エラー: {e}")
        time.sleep(REQUEST_DELAY)

    return total_new


# ============================================================
# アクティブオークション期間チェック（P0-2 イベント駆動）
# ============================================================

def get_active_auctions() -> list[dict]:
    """現在開催中のオークション一覧を返す（slack_bridge から呼ばれる）"""
    return _active_auctions(days_after=7)


def is_high_priority_active() -> bool:
    """priority=3 のオークションが現在開催中なら True"""
    return any(a.get("priority", 0) >= 3 for a in _active_auctions(days_after=0))


# ============================================================
# メイン処理
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="海外オークション落札データ取得")
    parser.add_argument("--source", choices=["heritage", "stacks_bowers", "all"],
                        default="all", help="取得ソース（default: all）")
    parser.add_argument("--coin", default=None,
                        help="特定の管理番号のみ取得（例: 001001）")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB投入なしでテスト実行")
    # run.py から呼ばれる場合 sys.argv[1]="overseas-fetch" をスキップ
    _argv = [a for a in sys.argv[1:] if a != "overseas-fetch"]
    args = parser.parse_args(_argv)

    logger.info("=== 海外オークション落札データ取得 開始 ===")
    logger.info(f"ソース: {args.source} / 管理番号: {args.coin or '全件'} / dry-run: {args.dry_run}")

    # コインデータ取得
    coins = _load_coin_slab_data(mgmt_no=args.coin)
    logger.info(f"対象コイン: {len(coins)}件")
    if not coins:
        logger.warning("対象コインなし。終了します。")
        return

    session = _session()
    total = 0

    if args.source in ("heritage", "all"):
        logger.info("--- Heritage Auctions ---")
        total += fetch_heritage(coins, session, dry_run=args.dry_run)

    # Stack's Bowers / Noonans は将来実装
    if args.source in ("stacks_bowers", "all"):
        logger.info("--- Stack's Bowers: 将来実装予定 ---")

    logger.info(f"=== 完了: 合計 {total}件 DB登録 ===")
    print(f"海外落札データ取得完了: {total}件登録")


if __name__ == "__main__":
    main()
