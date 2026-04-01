# scripts/eligibility_rules.py
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

from config.business_rules import (
    DEFAULT_HIGH_VALUE_REVIEW_THRESHOLD_JPY,
    DEFAULT_MIN_EVIDENCE_COUNT,
    validate_candidate_hard_rules,
)

# Day3 時点の暫定しきい値
# CEO確定ではないため、必要なら後で business_rules.py 側へ昇格
MIN_COMPARISON_QUALITY_SCORE = 0.80


AUTO_PASS = "AUTO_PASS"
AUTO_REVIEW = "AUTO_REVIEW"
AUTO_REJECT = "AUTO_REJECT"

STATUS_ELIGIBLE = "eligible"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"


@dataclass
class EligibilityEvaluation:
    candidate_id: str
    auto_tier: str
    eligibility_status: str
    hard_fail_codes: List[str] = field(default_factory=list)
    warning_codes: List[str] = field(default_factory=list)
    info_codes: List[str] = field(default_factory=list)
    approval_blocked: bool = False
    evidence_count: int = 0
    projected_profit_jpy: Optional[float] = None
    projected_roi: Optional[float] = None
    comparison_quality_score: Optional[float] = None
    source_currency: Optional[str] = None
    shipping_from_country: Optional[str] = None
    lot_size: Optional[int] = None
    is_active: Optional[bool] = None
    cert_number_present: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _as_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _pick(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _reason_label(code: str) -> str:
    labels = {
        "banknote": "紙幣/紙モノ混入",
        "raw_coin": "未鑑定・スラブ無し",
        "non_ngc_pcgs": "NGC/PCGS以外",
        "missing_cert": "cert番号なし",
        "multi_lot": "複数ロット",
        "inactive": "終了済み / 非アクティブ",
        "ship_from_invalid": "発送元がUS/UK以外",
        "currency_invalid": "eBay通貨がUSDではない",
        "evidence_insufficient": "証拠不足",
        "pricing_missing": "価格評価未生成",
        "comparison_quality_low": "比較品質が低い",
        "high_value_review": "高額案件のためレビュー必須",
        "profit_thin": "利益が薄い/マイナス",
        "roi_missing": "ROI未計算",
        "lot_unknown": "lotサイズ不明",
    }
    return labels.get(code, code)


def extract_candidate_snapshot(candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": str(_pick(candidate_row, "id")),
        "title": _as_text(_pick(candidate_row, "title", "item_title", "auction_title")),
        "category_text": _as_text(_pick(candidate_row, "category", "coin_type", "category_text")),
        "grader": _as_text(_pick(candidate_row, "grader", "grading_company")),
        "cert_number": _as_text(_pick(candidate_row, "cert_number", "cert", "slab_cert_number")),
        "lot_size": _as_int(_pick(candidate_row, "lot_size")),
        "is_active": _as_bool(_pick(candidate_row, "is_active")),
        "is_sold": _as_bool(_pick(candidate_row, "is_sold")),
        "source_currency": _as_text(_pick(candidate_row, "source_currency", "currency")),
        "shipping_from_country": _as_text(_pick(candidate_row, "shipping_from_country", "ship_from_country")),
        "evidence_count": _as_int(_pick(candidate_row, "evidence_count")) or 0,
        "projected_profit_jpy": _as_float(_pick(candidate_row, "projected_profit_jpy")),
        "projected_roi": _as_float(_pick(candidate_row, "projected_roi")),
        "comparison_quality_score": _as_float(_pick(candidate_row, "comparison_quality_score")),
        "recommended_max_bid_jpy": _as_float(_pick(candidate_row, "recommended_max_bid_jpy")),
        "current_price": _as_float(_pick(candidate_row, "current_price", "price", "current_price_jpy")),
    }


def evaluate_candidate_eligibility(candidate_row: Dict[str, Any]) -> EligibilityEvaluation:
    snapshot = extract_candidate_snapshot(candidate_row)

    hard_result = validate_candidate_hard_rules(
        grader=snapshot["grader"],
        title=snapshot["title"],
        category_text=snapshot["category_text"],
        source_currency=snapshot["source_currency"],
        ship_from_country=snapshot["shipping_from_country"],
        cert_number=snapshot["cert_number"],
        lot_size=snapshot["lot_size"],
    )

    hard_fail_codes: List[str] = list(hard_result["reason_codes"])
    warning_codes: List[str] = []
    info_codes: List[str] = []

    # active / sold は hard fail
    if snapshot["is_active"] is False or snapshot["is_sold"] is True:
        if "inactive" not in hard_fail_codes:
            hard_fail_codes.append("inactive")

    # lot unknown は review
    if snapshot["lot_size"] is None:
        warning_codes.append("lot_unknown")

    # 証拠不足は review
    if snapshot["evidence_count"] < DEFAULT_MIN_EVIDENCE_COUNT:
        warning_codes.append("evidence_insufficient")
    else:
        info_codes.append("evidence_sufficient")

    # pricing未生成は review
    if snapshot["projected_profit_jpy"] is None:
        warning_codes.append("pricing_missing")

    # ROI未計算は review
    if snapshot["projected_roi"] is None:
        warning_codes.append("roi_missing")

    # 利益が負 or 0 は review
    if snapshot["projected_profit_jpy"] is not None and snapshot["projected_profit_jpy"] <= 0:
        warning_codes.append("profit_thin")

    # 比較品質が低い場合は review
    if (
        snapshot["comparison_quality_score"] is not None
        and snapshot["comparison_quality_score"] < MIN_COMPARISON_QUALITY_SCORE
    ):
        warning_codes.append("comparison_quality_low")

    # 高額案件は review
    price_proxy = snapshot["recommended_max_bid_jpy"] or snapshot["current_price"]
    if price_proxy is not None and price_proxy >= DEFAULT_HIGH_VALUE_REVIEW_THRESHOLD_JPY:
        warning_codes.append("high_value_review")

    # 重複除去
    hard_fail_codes = list(dict.fromkeys(hard_fail_codes))
    warning_codes = list(dict.fromkeys(warning_codes))
    info_codes = list(dict.fromkeys(info_codes))

    if hard_fail_codes:
        auto_tier = AUTO_REJECT
        eligibility_status = STATUS_REJECTED
        approval_blocked = True
    elif warning_codes:
        auto_tier = AUTO_REVIEW
        eligibility_status = STATUS_REVIEW
        approval_blocked = False
    else:
        auto_tier = AUTO_PASS
        eligibility_status = STATUS_ELIGIBLE
        approval_blocked = False

    return EligibilityEvaluation(
        candidate_id=snapshot["candidate_id"],
        auto_tier=auto_tier,
        eligibility_status=eligibility_status,
        hard_fail_codes=hard_fail_codes,
        warning_codes=warning_codes,
        info_codes=info_codes,
        approval_blocked=approval_blocked,
        evidence_count=snapshot["evidence_count"],
        projected_profit_jpy=snapshot["projected_profit_jpy"],
        projected_roi=snapshot["projected_roi"],
        comparison_quality_score=snapshot["comparison_quality_score"],
        source_currency=snapshot["source_currency"],
        shipping_from_country=snapshot["shipping_from_country"],
        lot_size=snapshot["lot_size"],
        is_active=snapshot["is_active"],
        cert_number_present=bool(snapshot["cert_number"]),
    )


def reason_labels(codes: List[str]) -> List[str]:
    return [_reason_label(code) for code in codes]


def build_badges(evaluation: EligibilityEvaluation) -> Dict[str, List[str]]:
    return {
        "hard_fail_labels": reason_labels(evaluation.hard_fail_codes),
        "warning_labels": reason_labels(evaluation.warning_codes),
        "info_labels": reason_labels(evaluation.info_codes),
    }


def candidate_should_appear_in_ceo_default_queue(evaluation: EligibilityEvaluation) -> bool:
    return evaluation.auto_tier in {AUTO_PASS, AUTO_REVIEW}
