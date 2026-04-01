# scripts/pricing_engine.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.business_rules import (
    PRIMARY_PRICING_BUCKET,
    TARGET_GROSS_MARGIN,
    ProfitInputs,
    classify_recency_bucket,
    comparison_is_allowed,
    compute_profit_snapshot,
    year_matches_strict,
)
from scripts.supabase_client import get_client


# ============================================================
# Column alias candidates
# 実テーブルの列名差異を吸収するための候補群
# ============================================================

COL_CANDIDATES = {
    "title": ["title", "item_title", "auction_title", "name"],
    "sale_price_jpy": ["sale_price_jpy", "price_jpy", "final_price_jpy", "hammer_price_jpy", "amount_jpy"],
    "sale_date": ["sale_date", "sold_date", "ended_at", "sold_at", "transaction_date", "created_at"],
    "year": ["year", "coin_year", "year_text", "issue_year"],
    "mintmark": ["mintmark", "mint_mark"],
    "grade": ["grade", "grade_text", "slab_grade"],
    "size": ["size", "diameter", "diameter_text"],
    "signature": ["signature", "designer_signature", "engraver_signature"],
    "cert_number": ["cert_number", "cert", "slab_cert_number"],
    "grader": ["grader", "grading_company"],
}


def _pick_value(row: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip()


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    # ISO想定
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text).date()
    except Exception:
        pass

    # YYYY-MM-DD
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue

    return None


@dataclass
class CandidateIdentity:
    candidate_id: str
    title: Optional[str] = None
    year: Optional[str] = None
    mintmark: Optional[str] = None
    grade: Optional[str] = None
    size: Optional[str] = None
    signature: Optional[str] = None
    cert_number: Optional[str] = None
    grader: Optional[str] = None


@dataclass
class MarketComp:
    title: Optional[str]
    sale_price_jpy: float
    sale_date: date
    bucket: str
    year: Optional[str]
    mintmark: Optional[str]
    grade: Optional[str]
    size: Optional[str]
    signature: Optional[str]
    cert_number: Optional[str]
    grader: Optional[str]
    difference_count: int
    allowed: bool
    raw: Dict[str, Any]


@dataclass
class BucketStats:
    count: int
    avg_jpy: Optional[float]
    median_jpy: Optional[float]
    min_jpy: Optional[float]
    max_jpy: Optional[float]


@dataclass
class PricingSnapshot:
    expected_sale_price_jpy: Optional[float]
    recent_3m_avg_jpy: Optional[float]
    recent_3_6m_avg_jpy: Optional[float]
    recent_6_12m_avg_jpy: Optional[float]
    older_12m_plus_avg_jpy: Optional[float]
    recent_3m_median_jpy: Optional[float]
    recent_3_6m_median_jpy: Optional[float]
    recent_6_12m_median_jpy: Optional[float]
    older_12m_plus_median_jpy: Optional[float]
    recent_3m_count: int
    recent_3_6m_count: int
    recent_6_12m_count: int
    older_12m_plus_count: int
    total_cost_jpy: Optional[float]
    projected_profit_jpy: Optional[float]
    projected_roi: Optional[float]
    projected_margin: Optional[float]
    pricing_notes: List[str]


# ============================================================
# Identity extraction
# ============================================================

def extract_candidate_identity(candidate_row: Dict[str, Any]) -> CandidateIdentity:
    """
    daily_candidates の1行から pricing comparison 用 identity を抽出する。
    実カラム名がブレる可能性があるため複数候補名を吸収する。
    """
    return CandidateIdentity(
        candidate_id=str(candidate_row.get("id")),
        title=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["title"])),
        year=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["year"])),
        mintmark=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["mintmark"])),
        grade=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["grade"])),
        size=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["size"])),
        signature=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["signature"])),
        cert_number=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["cert_number"])),
        grader=_safe_str(_pick_value(candidate_row, COL_CANDIDATES["grader"])),
    )


# ============================================================
# Market transactions loading
# ============================================================

def fetch_market_transactions(limit: int = 30000) -> List[Dict[str, Any]]:
    """
    market_transactions を取得する（ページネーション対応）。
    Supabase は 1件あたり 1000行上限のため、1000件ずつページ取得して結合する。
    sold_date 降順で直近データ優先。
    """
    client = get_client()
    PAGE_SIZE = 1000
    all_rows: List[Dict[str, Any]] = []
    offset = 0

    while len(all_rows) < limit:
        batch_limit = min(PAGE_SIZE, limit - len(all_rows))
        result = (
            client.table("market_transactions")
            .select("id,title,price_jpy,sold_date,grade,grader,year,denomination,source")
            .order("sold_date", desc=True)
            .range(offset, offset + batch_limit - 1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < batch_limit:
            break  # 最終ページ
        offset += batch_limit

    return all_rows


def build_market_comp(
    row: Dict[str, Any],
    candidate_identity: CandidateIdentity,
    as_of: Optional[date] = None,
) -> Optional[MarketComp]:
    sale_price = _safe_float(_pick_value(row, COL_CANDIDATES["sale_price_jpy"]))
    sale_date = _parse_date(_pick_value(row, COL_CANDIDATES["sale_date"]))

    if sale_price is None or sale_date is None:
        return None

    as_of = as_of or datetime.now(timezone.utc).date()
    days_ago = max((as_of - sale_date).days, 0)
    bucket = classify_recency_bucket(days_ago)

    comp_identity = {
        "year": _safe_str(_pick_value(row, COL_CANDIDATES["year"])),
        "mintmark": _safe_str(_pick_value(row, COL_CANDIDATES["mintmark"])),
        "grade": _safe_str(_pick_value(row, COL_CANDIDATES["grade"])),
        "size": _safe_str(_pick_value(row, COL_CANDIDATES["size"])),
        "signature": _safe_str(_pick_value(row, COL_CANDIDATES["signature"])),
    }

    candidate_identity_dict = {
        "year": candidate_identity.year,
        "mintmark": candidate_identity.mintmark,
        "grade": candidate_identity.grade,
        "size": candidate_identity.size,
        "signature": candidate_identity.signature,
    }

    # Hard rule: 年号完全一致
    if not year_matches_strict(candidate_identity.year, comp_identity["year"]):
        return None

    allowed, diff_count = comparison_is_allowed(candidate_identity_dict, comp_identity)
    if not allowed:
        return None

    return MarketComp(
        title=_safe_str(_pick_value(row, COL_CANDIDATES["title"])),
        sale_price_jpy=float(sale_price),
        sale_date=sale_date,
        bucket=bucket,
        year=comp_identity["year"],
        mintmark=comp_identity["mintmark"],
        grade=comp_identity["grade"],
        size=comp_identity["size"],
        signature=comp_identity["signature"],
        cert_number=_safe_str(_pick_value(row, COL_CANDIDATES["cert_number"])),
        grader=_safe_str(_pick_value(row, COL_CANDIDATES["grader"])),
        difference_count=diff_count,
        allowed=allowed,
        raw=row,
    )


def filter_market_transactions(
    candidate_identity: CandidateIdentity,
    market_rows: Iterable[Dict[str, Any]],
    as_of: Optional[date] = None,
) -> List[MarketComp]:
    comps: List[MarketComp] = []

    for row in market_rows:
        comp = build_market_comp(row, candidate_identity, as_of=as_of)
        if comp is not None:
            comps.append(comp)

    return comps


# ============================================================
# Bucket stats
# ============================================================

def _calc_bucket_stats(comps: List[MarketComp]) -> BucketStats:
    if not comps:
        return BucketStats(count=0, avg_jpy=None, median_jpy=None, min_jpy=None, max_jpy=None)

    prices = [c.sale_price_jpy for c in comps]
    return BucketStats(
        count=len(prices),
        avg_jpy=round(mean(prices), 2),
        median_jpy=round(median(prices), 2),
        min_jpy=round(min(prices), 2),
        max_jpy=round(max(prices), 2),
    )


def bucketize_comps(comps: List[MarketComp]) -> Dict[str, List[MarketComp]]:
    buckets: Dict[str, List[MarketComp]] = {
        "recent_3m": [],
        "recent_3_6m": [],
        "recent_6_12m": [],
        "older_12m_plus": [],
    }

    for comp in comps:
        buckets.setdefault(comp.bucket, []).append(comp)

    return buckets


def compute_expected_sale_price(
    buckets: Dict[str, List[MarketComp]],
) -> Tuple[Optional[float], List[str]]:
    """
    価格決定ロジック初版。
    優先順位:
    1. recent_3m median (件数2以上)
    2. recent_3m avg (件数1)
    3. weighted fallback: recent_3m 0.70 / recent_3_6m 0.20 / recent_6_12m 0.10
    4. それでも無理なら older_12m_plus median
    """
    notes: List[str] = []

    stats = {bucket: _calc_bucket_stats(rows) for bucket, rows in buckets.items()}
    r3 = stats["recent_3m"]
    r36 = stats["recent_3_6m"]
    r612 = stats["recent_6_12m"]
    old = stats["older_12m_plus"]

    if r3.count >= 2 and r3.median_jpy is not None:
        notes.append("expected_sale_price uses recent_3m median")
        return r3.median_jpy, notes

    if r3.count == 1 and r3.avg_jpy is not None:
        notes.append("expected_sale_price uses recent_3m avg (single comp)")
        return r3.avg_jpy, notes

    weighted_values: List[Tuple[float, float]] = []
    if r3.median_jpy is not None:
        weighted_values.append((r3.median_jpy, 0.70))
    if r36.median_jpy is not None:
        weighted_values.append((r36.median_jpy, 0.20))
    if r612.median_jpy is not None:
        weighted_values.append((r612.median_jpy, 0.10))

    if weighted_values:
        numerator = sum(v * w for v, w in weighted_values)
        denominator = sum(w for _, w in weighted_values)
        value = round(numerator / denominator, 2)
        notes.append("expected_sale_price uses weighted fallback (3m prioritized)")
        return value, notes

    if old.median_jpy is not None:
        notes.append("expected_sale_price uses older_12m_plus median fallback")
        return old.median_jpy, notes

    notes.append("expected_sale_price unavailable due to missing comps")
    return None, notes


# ============================================================
# Snapshot builder
# ============================================================

def build_pricing_snapshot(
    *,
    purchase_price_jpy: float,
    import_tax_jpy: float,
    comps: List[MarketComp],
) -> PricingSnapshot:
    buckets = bucketize_comps(comps)
    stats = {bucket: _calc_bucket_stats(rows) for bucket, rows in buckets.items()}
    expected_sale_price, notes = compute_expected_sale_price(buckets)

    total_cost_jpy = None
    projected_profit_jpy = None
    projected_roi = None
    projected_margin = None

    if expected_sale_price is not None:
        profit_result = compute_profit_snapshot(
            inputs=ProfitInputs(
                purchase_price_jpy=purchase_price_jpy,
                expected_sale_price_jpy=expected_sale_price,
                import_tax_jpy=import_tax_jpy,
            )
        )
        total_cost_jpy = profit_result.total_cost_jpy
        projected_profit_jpy = profit_result.projected_profit_jpy
        projected_roi = profit_result.projected_roi
        projected_margin = profit_result.projected_margin

        if projected_margin is not None and projected_margin < TARGET_GROSS_MARGIN:
            notes.append("projected_margin below TARGET_GROSS_MARGIN")

    return PricingSnapshot(
        expected_sale_price_jpy=expected_sale_price,
        recent_3m_avg_jpy=stats["recent_3m"].avg_jpy,
        recent_3_6m_avg_jpy=stats["recent_3_6m"].avg_jpy,
        recent_6_12m_avg_jpy=stats["recent_6_12m"].avg_jpy,
        older_12m_plus_avg_jpy=stats["older_12m_plus"].avg_jpy,
        recent_3m_median_jpy=stats["recent_3m"].median_jpy,
        recent_3_6m_median_jpy=stats["recent_3_6m"].median_jpy,
        recent_6_12m_median_jpy=stats["recent_6_12m"].median_jpy,
        older_12m_plus_median_jpy=stats["older_12m_plus"].median_jpy,
        recent_3m_count=stats["recent_3m"].count,
        recent_3_6m_count=stats["recent_3_6m"].count,
        recent_6_12m_count=stats["recent_6_12m"].count,
        older_12m_plus_count=stats["older_12m_plus"].count,
        total_cost_jpy=total_cost_jpy,
        projected_profit_jpy=projected_profit_jpy,
        projected_roi=projected_roi,
        projected_margin=projected_margin,
        pricing_notes=notes,
    )


# ============================================================
# Persistence
# ============================================================

def save_pricing_snapshot(candidate_id: str, snapshot: PricingSnapshot) -> Dict[str, Any]:
    client = get_client()

    payload = {
        "candidate_id": str(candidate_id),
        "expected_sale_price_jpy": snapshot.expected_sale_price_jpy,
        "recent_3m_avg_jpy": snapshot.recent_3m_avg_jpy,
        "recent_3_6m_avg_jpy": snapshot.recent_3_6m_avg_jpy,
        "recent_6_12m_avg_jpy": snapshot.recent_6_12m_avg_jpy,
        "older_12m_plus_avg_jpy": snapshot.older_12m_plus_avg_jpy,
        "cost_formula_json": {
            "recent_3m_median_jpy": snapshot.recent_3m_median_jpy,
            "recent_3_6m_median_jpy": snapshot.recent_3_6m_median_jpy,
            "recent_6_12m_median_jpy": snapshot.recent_6_12m_median_jpy,
            "older_12m_plus_median_jpy": snapshot.older_12m_plus_median_jpy,
            "recent_3m_count": snapshot.recent_3m_count,
            "recent_3_6m_count": snapshot.recent_3_6m_count,
            "recent_6_12m_count": snapshot.recent_6_12m_count,
            "older_12m_plus_count": snapshot.older_12m_plus_count,
            "pricing_notes": snapshot.pricing_notes,
        },
        "total_cost_jpy": snapshot.total_cost_jpy,
        "projected_profit_jpy": snapshot.projected_profit_jpy,
        "projected_roi": snapshot.projected_roi,
        "projected_margin": snapshot.projected_margin,
    }

    result = client.table("candidate_pricing_snapshots").insert(payload).execute()
    data = result.data[0] if result.data else {}

    # daily_candidates の最新表示用キャッシュも更新
    client.table("daily_candidates").update(
        {
            "projected_profit_jpy": snapshot.projected_profit_jpy,
            "projected_roi": snapshot.projected_roi,
            "recommended_max_bid_jpy": snapshot.expected_sale_price_jpy,
            "recency_bucket_summary": {
                "recent_3m_count": snapshot.recent_3m_count,
                "recent_3_6m_count": snapshot.recent_3_6m_count,
                "recent_6_12m_count": snapshot.recent_6_12m_count,
                "older_12m_plus_count": snapshot.older_12m_plus_count,
                "primary_bucket": PRIMARY_PRICING_BUCKET,
                "pricing_notes": snapshot.pricing_notes,
            },
        }
    ).eq("id", candidate_id).execute()

    return data


# ============================================================
# Orchestration helper
# ============================================================

def build_and_save_candidate_pricing_snapshot(
    candidate_row: Dict[str, Any],
    *,
    purchase_price_jpy: float,
    import_tax_jpy: float = 0.0,
    market_rows: Optional[List[Dict[str, Any]]] = None,
) -> PricingSnapshot:
    candidate_identity = extract_candidate_identity(candidate_row)
    market_rows = market_rows if market_rows is not None else fetch_market_transactions()
    comps = filter_market_transactions(candidate_identity, market_rows)
    snapshot = build_pricing_snapshot(
        purchase_price_jpy=purchase_price_jpy,
        import_tax_jpy=import_tax_jpy,
        comps=comps,
    )
    save_pricing_snapshot(candidate_identity.candidate_id, snapshot)
    return snapshot


# ============================================================
# Debug helpers
# ============================================================

def get_latest_pricing_snapshot(candidate_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    result = (
        client.table("candidate_pricing_snapshots")
        .select("*")
        .eq("candidate_id", str(candidate_id))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None
