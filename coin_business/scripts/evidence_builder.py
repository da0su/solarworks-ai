# scripts/evidence_builder.py  — Day6 完成版
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from scripts.supabase_client import get_supabase_client

EVIDENCE_GROUP_ORDER = [
    "source_listing",
    "cert_verification",
    "numista_ref",
    "yahoo_comp",
    "heritage_comp",
    "spink_comp",
    "image",
    "note",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick(row: Dict[str, Any], *keys: str, default=None):
    for key in keys:
        if key in row and row.get(key) not in (None, "", []):
            return row.get(key)
    return default


def get_candidate_row(candidate_id: str) -> Optional[Dict[str, Any]]:
    supabase = get_supabase_client()
    res = (
        supabase.table("daily_candidates")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


def get_candidate_evidence(candidate_id: str) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    res = (
        supabase.table("candidate_evidence")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at")
        .execute()
    )
    return res.data or []


def group_candidate_evidence(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("evidence_type", "note")].append(row)
    ordered: Dict[str, List[Dict[str, Any]]] = {}
    for key in EVIDENCE_GROUP_ORDER:
        if key in grouped:
            ordered[key] = grouped[key]
    for key, value in grouped.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def evidence_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = defaultdict(int)
    for row in rows:
        summary[row.get("evidence_type", "note")] += 1
    return dict(summary)


def refresh_candidate_evidence_count(candidate_id: Optional[str] = None) -> None:
    supabase = get_supabase_client()
    if candidate_id:
        count_res = (
            supabase.table("candidate_evidence")
            .select("id", count="exact")
            .eq("candidate_id", candidate_id)
            .execute()
        )
        count_value = count_res.count or 0
        (
            supabase.table("daily_candidates")
            .update({"evidence_count": count_value})
            .eq("id", candidate_id)
            .execute()
        )
        return

    # 全候補を更新
    candidates_res = supabase.table("daily_candidates").select("id").execute()
    for row in candidates_res.data or []:
        refresh_candidate_evidence_count(str(row["id"]))


def upsert_candidate_evidence(
    candidate_id: str,
    evidence_type: str,
    evidence_url: str,
    title: str,
    meta_json: Optional[Dict[str, Any]] = None,
    evidence_source: str = "system",
    is_generated: bool = True,
) -> Dict[str, Any]:
    supabase = get_supabase_client()
    payload = {
        "candidate_id": candidate_id,
        "evidence_type": evidence_type,
        "evidence_url": evidence_url,
        "title": title,
        "meta_json": meta_json or {},
        "evidence_source": evidence_source,
        "is_generated": is_generated,
        "updated_at": _now_iso(),
    }
    try:
        res = (
            supabase.table("candidate_evidence")
            .upsert(payload, on_conflict="candidate_id,evidence_type,evidence_url")
            .execute()
        )
    except Exception:
        # unique index 未作成の場合は insert fallback
        res = supabase.table("candidate_evidence").insert(payload).execute()

    refresh_candidate_evidence_count(candidate_id)
    return (res.data or [payload])[0]


# ────────────────────────────────────────────
# URL builders
# ────────────────────────────────────────────

def build_cert_verification_url(grader: Optional[str], cert_number: Optional[str]) -> Optional[str]:
    if not grader or not cert_number:
        return None
    grader = grader.upper().strip()
    cert_number = str(cert_number).strip()
    if grader == "NGC":
        return f"https://www.ngccoin.com/certlookup/{quote_plus(cert_number)}/"
    if grader == "PCGS":
        return f"https://www.pcgs.com/cert/{quote_plus(cert_number)}"
    return None


def _build_numista_search_url(row: Dict[str, Any]) -> str:
    title = _pick(row, "title", "normalized_title", default="")
    year  = _pick(row, "year", "coin_year", default="")
    query = f"{title} {year}".strip()
    return f"https://en.numista.com/catalogue/index.php?r={quote_plus(query)}"


# ────────────────────────────────────────────
# Evidence builders (per type)
# ────────────────────────────────────────────

def _build_source_evidence(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    source_url = _pick(row, "source_url", "listing_url", "url", "lot_url")
    if not source_url:
        return []
    return [{
        "evidence_type": "source_listing",
        "evidence_url":  source_url,
        "title": _pick(row, "title", "lot_title", "normalized_title", default="source listing"),
        "meta_json": {
            "source":         _pick(row, "source", "auction_house"),
            "price":          _pick(row, "price", "current_price", "buy_limit_jpy"),
            "currency":       _pick(row, "source_currency", "currency"),
            "ship_from":      _pick(row, "shipping_from_country", "ship_from"),
            "lot_size":       _pick(row, "lot_size"),
            "is_active":      _pick(row, "is_active"),
        },
    }]


def _build_cert_evidence(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    grader      = _pick(row, "grader")
    cert_number = _pick(row, "cert_number")
    cert_url    = build_cert_verification_url(grader, cert_number)
    if not cert_url:
        return []
    return [{
        "evidence_type": "cert_verification",
        "evidence_url":  cert_url,
        "title": f"{grader} cert {cert_number}",
        "meta_json": {
            "grader":      grader,
            "cert_number": cert_number,
            "grade":       _pick(row, "grade"),
            "year":        _pick(row, "year", "coin_year"),
            "mintmark":    _pick(row, "mintmark"),
            "country":     _pick(row, "country"),
            "denomination": _pick(row, "denomination"),
        },
    }]


def _build_numista_evidence(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    numista_url = _pick(row, "numista_url") or _build_numista_search_url(row)
    return [{
        "evidence_type": "numista_ref",
        "evidence_url":  numista_url,
        "title": "Numista reference",
        "meta_json": {
            "year":        _pick(row, "year", "coin_year"),
            "denomination": _pick(row, "denomination"),
            "country":     _pick(row, "country"),
        },
    }]


def _build_archive_search_links(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    title  = _pick(row, "title", "lot_title", "normalized_title", default="")
    year   = _pick(row, "year", "coin_year", default="")
    grader = _pick(row, "grader", default="")
    grade  = _pick(row, "grade", default="")
    query  = " ".join([str(v) for v in [year, title, grader, grade] if v]).strip()
    encoded = quote_plus(query)
    return [
        {
            "evidence_type": "heritage_comp",
            "evidence_url":  f"https://coins.ha.com/c/search/results.zx?Nty=1&Ntt={encoded}",
            "title": "Heritage search",
            "meta_json": {"query": query},
        },
        {
            "evidence_type": "spink_comp",
            "evidence_url":  (
                "https://www.spink.com/archive/index?"
                f"AuctionLotSearch%5Bkeyword%5D={encoded}"
            ),
            "title": "Spink archive search",
            "meta_json": {"query": query},
        },
    ]


def _build_yahoo_comp_evidence(candidate_id: str, row: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    year        = _pick(row, "year", "coin_year")
    denomination = _pick(row, "denomination")
    country     = _pick(row, "country")
    grade       = _pick(row, "grade")
    numista_id  = _pick(row, "numista_id")

    res  = supabase.table("market_transactions").select("*").limit(200).execute()
    rows = res.data or []

    def is_match(tx: Dict[str, Any]) -> bool:
        tx_year     = _pick(tx, "year", "coin_year")
        tx_denom    = _pick(tx, "denomination")
        tx_country  = _pick(tx, "country")
        tx_grade    = _pick(tx, "grade")
        tx_numista  = _pick(tx, "numista_id")
        # Numista 完全一致（両方あれば）
        if numista_id and tx_numista and str(numista_id) != str(tx_numista):
            return False
        # 年号完全一致必須
        if year and tx_year and str(year) != str(tx_year):
            return False
        if denomination and tx_denom and str(denomination).lower() != str(tx_denom).lower():
            return False
        if country and tx_country and str(country).lower() != str(tx_country).lower():
            return False
        # グレード差1変数まで
        diff = 0
        if grade and tx_grade and str(grade).upper() != str(tx_grade).upper():
            diff += 1
        return diff <= 1

    matches = [tx for tx in rows if is_match(tx)][:limit]
    evidence = []
    for tx in matches:
        url = _pick(tx, "url", "source_url", "auction_url")
        if not url:
            continue
        evidence.append({
            "evidence_type": "yahoo_comp",
            "evidence_url":  url,
            "title": _pick(tx, "title", default="Yahoo sold comp"),
            "meta_json": {
                "sale_price_jpy": _pick(tx, "price_jpy", "sold_price_jpy", "sale_price_jpy"),
                "sale_date":      _pick(tx, "sold_at", "ended_at", "sale_date"),
                "bucket":         _pick(tx, "recency_bucket", "bucket"),
                "grade":          _pick(tx, "grade"),
                "year":           _pick(tx, "year", "coin_year"),
                "difference_count": 0,
            },
        })
    return evidence


# ────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────

def build_candidate_evidence_bundle(
    candidate_id: str,
    replace_generated: bool = False,
) -> Dict[str, Any]:
    supabase = get_supabase_client()
    row = get_candidate_row(candidate_id)
    if not row:
        raise ValueError(f"candidate not found: {candidate_id}")

    if replace_generated:
        (
            supabase.table("candidate_evidence")
            .delete()
            .eq("candidate_id", candidate_id)
            .eq("is_generated", True)
            .execute()
        )

    items: List[Dict[str, Any]] = []
    items.extend(_build_source_evidence(row))
    items.extend(_build_cert_evidence(row))
    items.extend(_build_numista_evidence(row))
    items.extend(_build_archive_search_links(row))
    items.extend(_build_yahoo_comp_evidence(candidate_id, row))

    inserted = []
    for item in items:
        inserted.append(
            upsert_candidate_evidence(
                candidate_id=candidate_id,
                evidence_type=item["evidence_type"],
                evidence_url=item["evidence_url"],
                title=item["title"],
                meta_json=item.get("meta_json", {}),
                evidence_source="system",
                is_generated=True,
            )
        )

    refresh_candidate_evidence_count(candidate_id)
    return {
        "candidate_id":   candidate_id,
        "inserted_count": len(inserted),
        "summary":        evidence_summary(get_candidate_evidence(candidate_id)),
    }
