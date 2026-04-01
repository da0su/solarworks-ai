"""
coin_business/yahoo/normalizer.py
===================================
Yahoo!落札タイトルの正規化モジュール。

normalize_title():
  - 全角→半角変換
  - 連続スペース除去
  - ノイズキーワード除去
  - 結果を title_normalized に格納

normalize_lot_record():
  - market_transactions の1行 (dict) を受け取り
  - yahoo_sold_lots_staging 用レコード (dict) を返す
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from yahoo.parser import ParseResult, parse_lot_title

# ================================================================
# ノイズキーワード (タイトルから除去するパターン)
# ================================================================

_NOISE_PATTERNS: list[re.Pattern] = [
    # カッコ内の補足を最初に除去 (内容パターンより先に処理する)
    re.compile(r'【[^】]*】'),
    re.compile(r'《[^》]*》'),
    re.compile(r'〔[^〕]*〕'),
    # 状態表記
    re.compile(r'\s*送料[^\s]*', re.IGNORECASE),
    re.compile(r'\s*即決[^\s]*', re.IGNORECASE),
    re.compile(r'\s*スタート[^\s]*', re.IGNORECASE),
    re.compile(r'\s*円スタート', re.IGNORECASE),
    re.compile(r'\s*1円スタート', re.IGNORECASE),
    re.compile(r'\s*希少[^\s]*', re.IGNORECASE),
    re.compile(r'\s*レア[^\s]*', re.IGNORECASE),
    re.compile(r'\s*美品[^\s]*'),
    re.compile(r'\s*極美品[^\s]*'),
    re.compile(r'\s*未使用[^\s]*'),
    re.compile(r'\s*新品[^\s]*'),
    re.compile(r'\s*入手困難[^\s]*'),
    re.compile(r'\s*保証あり[^\s]*'),
    re.compile(r'\s*本物保証[^\s]*'),
    re.compile(r'\s*真贋[^\s]*'),
    # 記号の連続
    re.compile(r'[★☆◆◇■□●○▼▽▲△]{1,}'),
    re.compile(r'[!！]{2,}'),
    # 末尾の価格情報「¥xxx」
    re.compile(r'¥\s*[\d,]+'),
]

# ================================================================
# 公開 API
# ================================================================

def normalize_title(raw_title: str) -> str:
    """
    生タイトルを正規化して返す。

    処理順:
      1. 全角英数・記号 → 半角
      2. ノイズキーワード除去
      3. 連続空白 → 単一スペース
      4. 前後空白除去
    """
    if not raw_title:
        return ""

    # 1. 全角→半角 (英数・記号)
    s = _fullwidth_to_halfwidth(raw_title)

    # 2. ノイズ除去
    for pat in _NOISE_PATTERNS:
        s = pat.sub(" ", s)

    # 3. 連続空白 → 1スペース
    s = re.sub(r'\s+', ' ', s)

    # 4. 前後トリム
    return s.strip()


def normalize_lot_record(
    mt_row: dict,
    yahoo_listing_id: Optional[str] = None,
) -> dict:
    """
    market_transactions の1行 (dict) を yahoo_sold_lots_staging 用レコードに変換する。

    Args:
        mt_row:            market_transactions の1レコード
        yahoo_listing_id:  上書きしたい場合に指定。省略時は mt_row から推定。

    Returns:
        yahoo_sold_lots_staging INSERT 用 dict
        (status / created_at / updated_at は DB デフォルト値を使う)
    """
    raw_title  = mt_row.get("title", "") or ""
    normalized = normalize_title(raw_title)

    parsed: ParseResult = parse_lot_title(raw_title)

    # yahoo_listing_id の決定
    lot_id = (
        yahoo_listing_id
        or mt_row.get("item_id")
        or mt_row.get("yahoo_lot_id")
        or _derive_lot_id_from_url(mt_row.get("url", "") or mt_row.get("source_url", ""))
    )

    # 売却日を DATE 文字列に正規化 (YYYY-MM-DD)
    sold_at = _normalize_date(mt_row.get("sold_date") or mt_row.get("sold_at"))

    record: dict = {
        "yahoo_lot_id":       lot_id,
        "lot_title":          raw_title[:2000] if raw_title else None,
        "title_normalized":   normalized[:2000] if normalized else None,
        "sold_price_jpy":     _safe_int(mt_row.get("price_jpy") or mt_row.get("price")),
        "sold_at":            sold_at,
        "cert_company":       parsed.cert_company,
        "cert_number":        parsed.cert_number,
        "year":               parsed.year,
        "denomination":       parsed.denomination,
        "grade_text":         parsed.grade_text,
        "source_url":         mt_row.get("url") or mt_row.get("source_url"),
        "image_url":          mt_row.get("thumbnail_url") or mt_row.get("image_url"),
        "parse_confidence":   parsed.parse_confidence,
        # status は DB DEFAULT 'PENDING_CEO' に任せるが明示もする
        "status":             "PENDING_CEO",
    }
    # None 値のキーは送らない (DB のデフォルト/NULL に任せる)
    return {k: v for k, v in record.items() if v is not None}


# ================================================================
# 内部ヘルパー
# ================================================================

def _fullwidth_to_halfwidth(text: str) -> str:
    """全角英数字・記号を半角に変換する (日本語かなは変換しない)。"""
    result = []
    for ch in text:
        cp = ord(ch)
        # 全角英数字・記号 (FF01-FF5E) → 半角 (0021-007E)
        if 0xFF01 <= cp <= 0xFF5E:
            result.append(chr(cp - 0xFEE0))
        # 全角スペース
        elif cp == 0x3000:
            result.append(' ')
        else:
            result.append(ch)
    return ''.join(result)


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    """日付文字列を YYYY-MM-DD 形式に正規化する。"""
    if not raw:
        return None
    s = str(raw).strip()
    # すでに YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
        return s[:10]
    # YYYY/MM/DD
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})', s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s


def _safe_int(val) -> Optional[int]:
    """数値っぽい値を int に変換。失敗したら None。"""
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _derive_lot_id_from_url(url: str) -> Optional[str]:
    """
    Yahoo!オークション URL から lot ID を抽出する。
    例: https://page.auctions.yahoo.co.jp/jp/auction/m12345678
        → "m12345678"
    """
    if not url:
        return None
    m = re.search(r'/auction/([a-z]\d+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    # aucfan 系 URL
    m = re.search(r'[?&]id=([a-z0-9]+)', url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None
