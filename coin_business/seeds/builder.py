"""
coin_business/seeds/builder.py
================================
yahoo_sold_lots レコードから探索用 seed を生成するモジュール。

seed は eBay API の検索クエリや絞り込みパラメータをパッケージ化したもの。
Phase 5 の eBay スキャンが seed を消費して ebay_listings_raw に書き込む。

seed_type 一覧:
  CERT_EXACT       : cert_company + cert_number 完全一致
                     → eBay に同一スラブが出ているか確認
  CERT_TITLE       : cert_company + title_normalized
                     → 同じグレーダーで同コインを探す
  TITLE_NORMALIZED : title_normalized のみ
                     → cert なし案件の類似検索
  YEAR_DENOM_GRADE : year + denomination + grade_text
                     → 年代・額面・グレード範囲のブロード検索

設計原則:
  - 入力は必ず yahoo_sold_lots のレコード。staging からは生成しない。
  - 1 レコードから複数 seed を生成することがある。
  - 生成できる seed が 0 件でも例外を出さない。
  - priority_score は SeedType.PRIORITY から引く。
"""

from __future__ import annotations

import re
from typing import Optional

from constants import SeedType, SeedStatus


# ================================================================
# 公開 API
# ================================================================

def build_seeds_for_lot(lot: dict) -> list[dict]:
    """
    yahoo_sold_lots の 1 レコードから seed リストを生成する。

    Args:
        lot: yahoo_sold_lots の 1 行 (dict)

    Returns:
        list of dict — yahoo_coin_seeds に upsert するレコード一覧
        (id / created_at / updated_at は DB デフォルトに任せる)
    """
    seeds: list[dict] = []

    yahoo_lot_id = lot.get("yahoo_lot_id")
    source_row_id = lot.get("id")

    if not yahoo_lot_id:
        return seeds

    cert_company    = _clean(lot.get("cert_company"))
    cert_number     = _clean(lot.get("cert_number"))
    title_norm      = _clean(lot.get("title_normalized")) or _clean(lot.get("lot_title"))
    year            = lot.get("year")
    denomination    = _clean(lot.get("denomination"))
    grade_text      = _clean(lot.get("grade_text"))
    ref_price_jpy   = lot.get("sold_price_jpy")
    ref_sold_date   = lot.get("sold_date")

    # ── CERT_EXACT: cert_company + cert_number の両方が必要
    if cert_company and cert_number:
        query = _build_cert_exact_query(cert_company, cert_number)
        seeds.append(_make_seed(
            yahoo_lot_id  = yahoo_lot_id,
            source_row_id = source_row_id,
            seed_type     = SeedType.CERT_EXACT,
            search_query  = query,
            cert_company  = cert_company,
            cert_number   = cert_number,
            ref_price_jpy = ref_price_jpy,
            ref_sold_date = ref_sold_date,
        ))

    # ── CERT_TITLE: cert_company + title_normalized
    if cert_company and title_norm:
        query = _build_cert_title_query(cert_company, title_norm)
        seeds.append(_make_seed(
            yahoo_lot_id  = yahoo_lot_id,
            source_row_id = source_row_id,
            seed_type     = SeedType.CERT_TITLE,
            search_query  = query,
            cert_company  = cert_company,
            grader        = cert_company,
            ref_price_jpy = ref_price_jpy,
            ref_sold_date = ref_sold_date,
        ))

    # ── TITLE_NORMALIZED: title_normalized のみ (cert なし案件でも生成可)
    if title_norm and len(title_norm) >= 10:
        query = _build_title_query(title_norm)
        seeds.append(_make_seed(
            yahoo_lot_id  = yahoo_lot_id,
            source_row_id = source_row_id,
            seed_type     = SeedType.TITLE_NORMALIZED,
            search_query  = query,
            ref_price_jpy = ref_price_jpy,
            ref_sold_date = ref_sold_date,
        ))

    # ── YEAR_DENOM_GRADE: year + denomination の両方が必要。grade は任意。
    if year and denomination:
        query = _build_year_denom_grade_query(year, denomination, grade_text, cert_company)
        seeds.append(_make_seed(
            yahoo_lot_id  = yahoo_lot_id,
            source_row_id = source_row_id,
            seed_type     = SeedType.YEAR_DENOM_GRADE,
            search_query  = query,
            year_min      = year - 0,
            year_max      = year + 0,
            denomination  = denomination,
            grade_min     = grade_text,
            grader        = cert_company,
            ref_price_jpy = ref_price_jpy,
            ref_sold_date = ref_sold_date,
        ))

    return seeds


def build_search_query(seed: dict) -> str:
    """
    seed レコードから eBay API 用の検索クエリ文字列を再構築する。
    seed にすでに search_query が入っている場合はそれを返す。
    """
    if seed.get("search_query"):
        return seed["search_query"]

    seed_type = seed.get("seed_type", "")
    cert_company = seed.get("cert_company", "")
    cert_number  = seed.get("cert_number", "")

    if seed_type == SeedType.CERT_EXACT and cert_company and cert_number:
        return _build_cert_exact_query(cert_company, cert_number)

    return ""


# ================================================================
# 内部ヘルパー
# ================================================================

def _make_seed(
    yahoo_lot_id:  str,
    source_row_id: Optional[str],
    seed_type:     str,
    search_query:  str,
    cert_company:  Optional[str] = None,
    cert_number:   Optional[str] = None,
    year_min:      Optional[int] = None,
    year_max:      Optional[int] = None,
    denomination:  Optional[str] = None,
    grade_min:     Optional[str] = None,
    grader:        Optional[str] = None,
    ref_price_jpy: Optional[int] = None,
    ref_sold_date = None,
) -> dict:
    """seed レコードの dict を組み立てる。"""
    priority = SeedType.PRIORITY.get(seed_type, 0.3)
    rec: dict = {
        "yahoo_lot_id":   yahoo_lot_id,
        "source_row_id":  source_row_id,
        "seed_type":      seed_type,
        "search_query":   search_query[:500] if search_query else "",
        "seed_status":    SeedStatus.READY,
        "priority_score": priority,
        "is_active":      True,
    }
    if cert_company:  rec["cert_company"]  = cert_company
    if cert_number:   rec["cert_number"]   = cert_number
    if year_min:      rec["year_min"]      = year_min
    if year_max:      rec["year_max"]      = year_max
    if denomination:  rec["denomination"]  = denomination
    if grade_min:     rec["grade_min"]     = grade_min
    if grader:        rec["grader"]        = grader.upper()
    if ref_price_jpy: rec["ref_price_jpy"] = ref_price_jpy
    if ref_sold_date: rec["ref_sold_date"] = str(ref_sold_date)[:10]
    return rec


def _build_cert_exact_query(cert_company: str, cert_number: str) -> str:
    """例: 'NGC 12345678' """
    return f"{cert_company.upper()} {cert_number}".strip()


def _build_cert_title_query(cert_company: str, title_norm: str) -> str:
    """例: 'NGC 1921 Morgan Dollar MS63' """
    # title_norm に cert_company が含まれていない場合のみ先頭に追加
    upper = cert_company.upper()
    if upper in title_norm.upper():
        return _truncate(title_norm, 100)
    return _truncate(f"{upper} {title_norm}", 100)


def _build_title_query(title_norm: str) -> str:
    """最初の 80 文字に切り詰めた title_normalized。"""
    return _truncate(title_norm, 80)


def _build_year_denom_grade_query(
    year:        int,
    denomination: str,
    grade_text:  Optional[str],
    cert_company: Optional[str],
) -> str:
    """例: '1921 Morgan Dollar NGC MS63' """
    parts = [str(year), denomination]
    if cert_company:
        parts.append(cert_company.upper())
    if grade_text:
        parts.append(grade_text)
    return " ".join(p for p in parts if p).strip()


def _clean(val: Optional[str]) -> Optional[str]:
    """None / 空文字 → None。前後スペースを除去。"""
    if not val:
        return None
    s = str(val).strip()
    return s if s else None


def _truncate(s: str, max_len: int) -> str:
    """文字列を max_len 文字に切り詰める。"""
    return s[:max_len] if len(s) > max_len else s
