# config/business_rules.py
# ============================================================
# CEO承認済みハードルール
# このファイルの値はCEO承認なく変更しないこと
# 変更権限はCEOのみ
# ============================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


APP_NAME = "coin_business"

# ---------- 許可・禁止カテゴリ ----------
ALLOWED_GRADERS = {"NGC", "PCGS"}
ALLOWED_EBAY_SHIP_FROM = {"US", "UK"}

FORBIDDEN_CATEGORY_KEYWORDS = {
    "banknote",
    "paper money",
    "paper_money",
    "note",
    "currency note",
    "raw coin",
    "unslabbed",
    "unslab",
    "ungraded",
}

REVIEW_ONLY_OPTIONAL_TYPES = {
    "commemorative",
    "medal",
}

# ---------- eBay ルール ----------
# Hard Rule 1: eBay価格はUSD基準固定
# 日本IPでeBay検索すると価格フィルタが円扱い化する事故が起きる
EBAY_REQUIRED_SOURCE_CURRENCY = "USD"
EBAY_ENFORCE_USD = True
EBAY_ENFORCE_SHIP_FROM = True  # Hard Rule 4: 発送元US/UKのみ

# ---------- 比較ルール ----------
# Hard Rule 2: コイン比較は変数1つまで
# Hard Rule 3: Numista年号一致必須
NUMISTA_REQUIRE_EXACT_YEAR = True
MAX_ALLOWED_COMPARISON_DIFFERENCES = 1

COMPARISON_FIELDS = (
    "year",
    "mintmark",
    "grade",
    "size",
    "signature",
)

# ---------- 相場区分（4区分必須） ----------
# Hard Rule 5: 相場分析は直近3か月最重視
RECENCY_BUCKETS: Dict[str, Tuple[int, int]] = {
    "recent_3m":       (0,   90),
    "recent_3_6m":     (91,  180),
    "recent_6_12m":    (181, 365),
    "older_12m_plus":  (366, 99999),
}

PRIMARY_PRICING_BUCKET = "recent_3m"

RECENCY_WEIGHTS = {
    "recent_3m":      1.00,   # 主指標
    "recent_3_6m":    0.70,   # 補助
    "recent_6_12m":   0.40,   # 参考
    "older_12m_plus": 0.15,   # 警告付き参考値
}

# ---------- 利益計算式（CEO確定） ----------
# Hard Rule 6: この式はCEO確定。AI勝手に変更禁止
TARGET_GROSS_MARGIN     = 0.15          # 粗利率15%
IMPORT_TAX_MULTIPLIER   = 1.10          # 関税×1.1
US_FORWARDING_JPY       = 2_000         # US転送¥2,000
DOMESTIC_SHIPPING_JPY   = 750           # 国内¥750
YAHOO_AUCTION_FEE_RATE  = 0.10          # ヤフオク10%

# ---------- 候補判定閾値 ----------
DEFAULT_HIGH_VALUE_REVIEW_THRESHOLD_JPY = 300_000
DEFAULT_MIN_EVIDENCE_COUNT = 3
DEFAULT_STALE_HOURS = 6

# ---------- 判断値 ----------
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_HELD     = "held"
DECISION_PENDING  = "pending"

VALID_DECISIONS = {
    DECISION_APPROVED,
    DECISION_REJECTED,
    DECISION_HELD,
    DECISION_PENDING,
    "auto_rejected",
    "auto_review",
}

# ---------- 理由コード ----------
REASON_CODES: Dict[str, str] = {
    "approved":             "CEO承認",
    "profit_thin":          "想定利益が薄い",
    "roi_thin":             "ROIが目標未達",
    "banknote":             "紙幣・紙幣類",
    "raw_coin":             "生コイン（未鑑定）",
    "non_ngc_pcgs":         "NGC/PCGS以外の鑑定",
    "missing_cert":         "cert番号なし",
    "multi_lot":            "複数枚ロット",
    "inactive":             "出品終了・売り切れ",
    "ship_from_invalid":    "発送元がUS/UK以外",
    "currency_invalid":     "ソース通貨がUSD以外",
    "year_mismatch":        "年号不一致",
    "too_many_differences": "比較変数が2つ以上違う",
    "evidence_insufficient":"証拠リンクが不足",
    "different_coin":       "別のコイン・間違った比較対象",
    "sellability_risk":     "ヤフオク再販力が弱い",
    "manual_hold":          "CEO手動保留",
}


# ============================================================
# ユーティリティ関数
# ============================================================

def normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def normalize_ship_from_country(value: Optional[str]) -> str:
    val = normalize_text(value)
    mapping = {
        "USA": "US",
        "UNITED STATES": "US",
        "UNITED STATES OF AMERICA": "US",
        "U.S.": "US",
        "U.S.A.": "US",
        "UNITED KINGDOM": "UK",
        "GREAT BRITAIN": "UK",
        "ENGLAND": "UK",
    }
    return mapping.get(val, val)


def is_allowed_grader(grader: Optional[str]) -> bool:
    return normalize_text(grader) in ALLOWED_GRADERS


def is_allowed_ship_from_country(country: Optional[str]) -> bool:
    return normalize_ship_from_country(country) in ALLOWED_EBAY_SHIP_FROM


def is_valid_ebay_source_currency(currency: Optional[str]) -> bool:
    if not EBAY_ENFORCE_USD:
        return True
    return normalize_text(currency) == EBAY_REQUIRED_SOURCE_CURRENCY


def contains_forbidden_category_keyword(text: Optional[str]) -> bool:
    t = (text or "").lower()
    return any(keyword in t for keyword in FORBIDDEN_CATEGORY_KEYWORDS)


def year_matches_strict(base_year: Optional[str], comp_year: Optional[str]) -> bool:
    """Hard Rule 3: 年号完全一致必須"""
    if not NUMISTA_REQUIRE_EXACT_YEAR:
        return True
    return (base_year or "").strip() == (comp_year or "").strip()


def count_comparison_differences(base: Dict[str, Any], comp: Dict[str, Any]) -> int:
    """Hard Rule 2: 比較変数カウント"""
    diff_count = 0
    for field in COMPARISON_FIELDS:
        bv = base.get(field)
        cv = comp.get(field)
        bv = bv.strip() if isinstance(bv, str) else bv
        cv = cv.strip() if isinstance(cv, str) else cv
        if bv != cv:
            diff_count += 1
    return diff_count


def comparison_is_allowed(
    base: Dict[str, Any], comp: Dict[str, Any]
) -> Tuple[bool, int]:
    diff = count_comparison_differences(base, comp)
    return diff <= MAX_ALLOWED_COMPARISON_DIFFERENCES, diff


def classify_recency_bucket(days_ago: int) -> str:
    for name, (lo, hi) in RECENCY_BUCKETS.items():
        if lo <= days_ago <= hi:
            return name
    return "older_12m_plus"


@dataclass(frozen=True)
class ProfitInputs:
    purchase_price_jpy: float
    expected_sale_price_jpy: float
    import_tax_jpy: float = 0.0


@dataclass(frozen=True)
class ProfitResult:
    purchase_price_jpy: float
    expected_sale_price_jpy: float
    import_tax_jpy: float
    import_tax_adjusted_jpy: float
    yahoo_fee_jpy: float
    forwarding_jpy: float
    domestic_shipping_jpy: float
    additional_costs_jpy: float
    total_cost_jpy: float
    projected_profit_jpy: float
    projected_roi: float
    projected_margin: float
    meets_target_margin: bool


def compute_profit_snapshot(inputs: ProfitInputs) -> ProfitResult:
    """Hard Rule 6: CEO確定利益計算式"""
    purchase      = float(inputs.purchase_price_jpy or 0)
    expected_sale = float(inputs.expected_sale_price_jpy or 0)
    import_tax    = float(inputs.import_tax_jpy or 0)

    import_tax_adj  = import_tax * IMPORT_TAX_MULTIPLIER
    yahoo_fee       = expected_sale * YAHOO_AUCTION_FEE_RATE
    forwarding      = float(US_FORWARDING_JPY)
    domestic        = float(DOMESTIC_SHIPPING_JPY)

    additional  = import_tax_adj + forwarding + domestic + yahoo_fee
    total_cost  = purchase + additional
    profit      = expected_sale - total_cost

    roi     = (profit / purchase)      if purchase      > 0 else 0.0
    margin  = (profit / expected_sale) if expected_sale > 0 else 0.0

    return ProfitResult(
        purchase_price_jpy      = purchase,
        expected_sale_price_jpy = expected_sale,
        import_tax_jpy          = import_tax,
        import_tax_adjusted_jpy = round(import_tax_adj, 2),
        yahoo_fee_jpy           = round(yahoo_fee, 2),
        forwarding_jpy          = round(forwarding, 2),
        domestic_shipping_jpy   = round(domestic, 2),
        additional_costs_jpy    = round(additional, 2),
        total_cost_jpy          = round(total_cost, 2),
        projected_profit_jpy    = round(profit, 2),
        projected_roi           = round(roi, 4),
        projected_margin        = round(margin, 4),
        meets_target_margin     = margin >= TARGET_GROSS_MARGIN,
    )


def validate_candidate_hard_rules(
    *,
    grader: Optional[str],
    title: Optional[str],
    category_text: Optional[str],
    source_currency: Optional[str],
    ship_from_country: Optional[str],
    cert_number: Optional[str],
    lot_size: Optional[int],
) -> Dict[str, Any]:
    """適格フィルタ: NG理由コードリストを返す"""
    reasons = []

    if contains_forbidden_category_keyword(f"{title or ''} {category_text or ''}"):
        reasons.append("banknote")

    if not is_allowed_grader(grader):
        reasons.append("non_ngc_pcgs")

    if not cert_number or not str(cert_number).strip():
        reasons.append("missing_cert")

    if lot_size is not None and int(lot_size) != 1:
        reasons.append("multi_lot")

    if EBAY_ENFORCE_USD and not is_valid_ebay_source_currency(source_currency):
        reasons.append("currency_invalid")

    if EBAY_ENFORCE_SHIP_FROM and not is_allowed_ship_from_country(ship_from_country):
        reasons.append("ship_from_invalid")

    return {
        "is_valid": len(reasons) == 0,
        "reason_codes": reasons,
    }
