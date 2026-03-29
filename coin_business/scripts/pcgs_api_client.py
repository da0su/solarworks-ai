"""
PCGS Public API クライアント

PCGS Auction Prices Realized (APR) データを取得して market_transactions へ upsert する。

エンドポイント:
  - GetAPRByCertNo/{CertNo}  : cert_number から落札履歴 + PCGSNo を取得（メイン）
  - GetAPRByGrade            : PCGSNo + グレードで類似コインの落札履歴を取得
  - GetCoinFactsByCertNo/{CertNo} : cert_number から詳細情報 + PCGSNo を取得

認証:
  - Bearer Token (.env の PCGS_API_TOKEN)
  - 日次上限: 1,000 calls（超過は dealer@pcgs.com に申請）
  - トークンは https://www.pcgs.com/publicapi/documentation で再生成可能

使い方:
  python run.py overseas-fetch --source pcgs
  python run.py overseas-fetch --source pcgs --dry-run
  python run.py overseas-fetch --source pcgs --coin 001462
"""

import os
import re
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ── パス設定 ──────────────────────────────────────────────
_DIR = Path(__file__).parent
_ENV_FILE = _DIR.parent / ".env"

# ── .env 読み込み ──────────────────────────────────────────
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── ロガー ─────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── 定数 ───────────────────────────────────────────────────
PCGS_BASE       = "https://api.pcgs.com/publicapi"
REQUEST_DELAY   = 0.8    # 秒（rate limit対策）
MAX_DAILY_CALLS = 900    # 1,000上限に対して余裕を持たせる
BATCH_SIZE      = 50


# ── トークン管理 ───────────────────────────────────────────

def _get_token() -> str:
    token = os.environ.get("PCGS_API_TOKEN", "")
    if not token:
        raise RuntimeError(
            "PCGS_API_TOKEN が未設定です。"
            ".env に PCGS_API_TOKEN=<token> を追記するか、"
            "https://www.pcgs.com/publicapi/documentation でトークンを生成してください。"
        )
    return token


def _headers() -> dict:
    return {
        "Authorization": f"bearer {_get_token()}",
        "Accept": "application/json",
    }


# ── グレード変換 ────────────────────────────────────────────

def _parse_grade_no(grade_str: str) -> tuple[int, bool]:
    """
    'MS62' → (62, False)
    'MS65+' → (65, True)
    'PR70DCAM' → (70, False)
    'AU58+' → (58, True)
    """
    if not grade_str:
        return 0, False
    s = grade_str.strip().upper()
    plus = s.endswith("+")
    # 数字部分を抽出
    m = re.search(r"(\d{1,2})", s)
    if not m:
        return 0, False
    return int(m.group(1)), plus


# ── 日付パース ──────────────────────────────────────────────

def _parse_pcgs_date(date_str: str) -> Optional[str]:
    """
    'MM-YYYY' → 'YYYY-MM-DD' (月初日)
    'MM-YYYY' = PCGS API の日付フォーマット
    """
    if not date_str:
        return None
    try:
        m = re.match(r"(\d{1,2})-(\d{4})", date_str)
        if m:
            month, year = int(m.group(1)), int(m.group(2))
            return f"{year:04d}-{month:02d}-01"
    except (ValueError, AttributeError):
        pass
    return None


# ── レコード変換 ─────────────────────────────────────────────

def _parse_apr_response(data: dict, cert_number: str, source_label: str = "pcgs") -> list[dict]:
    """
    PCGS API レスポンスを market_transactions 形式に変換
    """
    from supabase_client import make_dedup_key

    records = []
    auctions = data.get("Auctions") or []
    coin_name = data.get("Name", "")
    grade_str = data.get("Grade", "")
    pcgs_no   = data.get("PCGSNo", "")

    year_m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", coin_name)
    year   = year_m.group(1) if year_m else ""

    for auction in auctions:
        price_usd = float(auction.get("Price") or 0)
        if price_usd <= 0:
            continue

        sold_date = _parse_pcgs_date(auction.get("Date", ""))
        lot_url   = auction.get("AuctionLotUrl") or ""
        auctioneer = auction.get("Auctioneer", "")
        sale_name  = auction.get("SaleName", "")
        lot_no     = str(auction.get("LotNo") or "")
        is_cac     = bool(auction.get("IsCAC", False))

        title = f"{coin_name} {grade_str}"
        if auctioneer:
            title += f" [{auctioneer}]"

        dedup_key = make_dedup_key(
            source=source_label,
            url=lot_url or None,
            title=title,
            price=int(price_usd),
            sold_date=sold_date or "",
        )

        records.append({
            "source":      source_label,
            "platform":    "pcgs",
            "title":       title,
            "price_jpy":   0,          # USD→JPY換算は stats 側で対応
            "price_usd":   price_usd,
            "sold_date":   sold_date,
            "url":         lot_url or None,
            "grader":      "PCGS",
            "grade":       grade_str,
            "year":        year,
            "dedup_key":   dedup_key,
            # 拡張フィールド（市場分析用）
            "_pcgs_no":    pcgs_no,
            "_cert_no":    cert_number,
            "_auctioneer": auctioneer,
            "_sale_name":  sale_name,
            "_lot_no":     lot_no,
            "_is_cac":     is_cac,
        })

    return records


# ── API 呼び出し ─────────────────────────────────────────────

def get_apr_by_cert(cert_number: str, session: requests.Session) -> Optional[dict]:
    """cert_number から落札履歴を取得。失敗時は None。"""
    url = f"{PCGS_BASE}/coindetail/GetAPRByCertNo/{cert_number}"
    try:
        resp = session.get(url, headers=_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("IsValidRequest") is False:
                logger.debug(f"  [PCGS] cert={cert_number}: IsValidRequest=False ({data.get('ServerMessage', '')})")
                return None
            return data
        elif resp.status_code == 401:
            logger.error("PCGS API: 認証失敗。トークンを再生成してください。")
            raise RuntimeError("PCGS API 認証エラー (401)")
        else:
            logger.warning(f"  [PCGS] cert={cert_number}: HTTP {resp.status_code}")
            return None
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning(f"  [PCGS] cert={cert_number}: 取得エラー: {e}")
        return None


def get_apr_by_grade(pcgs_no: str, grade_no: int, plus_grade: bool,
                     session: requests.Session,
                     days_back: int = 365,
                     num_records: int = 100) -> Optional[dict]:
    """PCGSNo + グレードから落札履歴を取得。"""
    url = f"{PCGS_BASE}/coindetail/GetAPRByGrade"
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days_back)
    params = {
        "PCGSNo":        pcgs_no,
        "GradeNo":       grade_no,
        "PlusGrade":     plus_grade,
        "StartDate":     start.strftime("%m-%d-%Y"),
        "EndDate":       today.strftime("%m-%d-%Y"),
        "NumberOfRecords": num_records,
    }
    try:
        resp = session.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"  [PCGS] GetAPRByGrade PCGSNo={pcgs_no}: HTTP {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"  [PCGS] GetAPRByGrade PCGSNo={pcgs_no}: エラー: {e}")
        return None


# ── メイン取得関数 ────────────────────────────────────────────

def fetch_pcgs(coins: list[dict], dry_run: bool = False) -> int:
    """
    PCGS グレードコインの落札履歴を取得して market_transactions へ upsert。

    coins: coin_slab_data の行リスト。grader='PCGS' かつ cert_number ありのものが対象。
    returns: DB登録件数（dry_run時は取得件数）
    """
    from supabase_client import get_client

    pcgs_coins = [
        c for c in coins
        if (c.get("grader") or "").upper() == "PCGS" and c.get("cert_number")
    ]
    logger.info(f"  [PCGS] 対象: {len(pcgs_coins)}件 (全{len(coins)}件中 PCGS+cert_number あり)")

    if not pcgs_coins:
        return 0

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SolarWorksBot/1.0)"})

    all_records = []
    call_count  = 0
    skip_count  = 0

    for coin in pcgs_coins:
        if call_count >= MAX_DAILY_CALLS:
            logger.warning(f"  [PCGS] 日次上限 {MAX_DAILY_CALLS} に達したため中断")
            break

        mgmt_no     = coin.get("management_no", "")
        cert_number = str(coin.get("cert_number", "")).strip()
        grade_str   = coin.get("grade", "")

        # Step 1: GetAPRByCertNo で落札履歴取得
        data = get_apr_by_cert(cert_number, session)
        call_count += 1
        time.sleep(REQUEST_DELAY)

        if not data:
            skip_count += 1
            continue

        # Step 2: cert単位のAPR
        cert_records = _parse_apr_response(data, cert_number, source_label="pcgs")

        # Step 3: PCGSNoが取得できた場合、同グレード全体の相場も取得（オプション）
        pcgs_no = data.get("PCGSNo", "")
        grade_no, plus_grade = _parse_grade_no(grade_str)

        if pcgs_no and grade_no > 0 and call_count < MAX_DAILY_CALLS:
            grade_data = get_apr_by_grade(pcgs_no, grade_no, plus_grade, session, days_back=365)
            call_count += 1
            time.sleep(REQUEST_DELAY)

            if grade_data:
                grade_records = _parse_apr_response(grade_data, cert_number="", source_label="pcgs")
                # cert_recordsと重複しないものを追加
                existing_dedup = {r["dedup_key"] for r in cert_records}
                new_recs = [r for r in grade_records if r["dedup_key"] not in existing_dedup]
                cert_records.extend(new_recs)

        # _で始まる拡張フィールドを除去（DBカラム外）
        clean_records = []
        for r in cert_records:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            clean_records.append(clean)

        all_records.extend(clean_records)

        auctions_count = len([r for r in (data.get("Auctions") or [])])
        logger.info(
            f"  [{mgmt_no}] PCGS cert={cert_number} ({grade_str}): "
            f"APR {auctions_count}件 / 変換レコード {len(clean_records)}件"
        )

    logger.info(f"  [PCGS] 取得完了: {len(all_records)}件 / API呼び出し: {call_count}回 / スキップ: {skip_count}件")

    # DB upsert
    if not all_records:
        return 0

    if dry_run:
        logger.info(f"  [DRY-RUN] {len(all_records)}件（DB投入スキップ）")
        # サンプル表示
        for r in all_records[:3]:
            logger.info(f"    {r.get('sold_date')} | USD {r.get('price_usd'):,.0f} | {r.get('title', '')[:50]}")
        return len(all_records)

    client = get_client()
    ok = 0
    ng = 0
    for i in range(0, len(all_records), BATCH_SIZE):
        batch = all_records[i:i + BATCH_SIZE]
        try:
            resp = client.table("market_transactions").upsert(
                batch, on_conflict="dedup_key"
            ).execute()
            ok += len(resp.data)
        except Exception as e:
            logger.warning(f"  [PCGS] upsert エラー: {e}")
            ng += len(batch)

    logger.info(f"  [PCGS] DB登録: {ok}件 / エラー: {ng}件")
    return ok
