"""
coin_business/yahoo/parser.py
==============================
Yahoo!落札タイトルから構造化フィールドを抽出するパーサー。

抽出対象:
  - cert_company  (NGC / PCGS / None)
  - cert_number   (鑑定番号 8-10桁 / None)
  - year          (西暦4桁 / None)
  - denomination  (額面 / None)
  - grade_text    (MS65 / PF69UC etc. / None)
  - parse_confidence  (0.0 - 1.0)

使い方:
  from yahoo.parser import parse_lot_title
  result = parse_lot_title("1921 NGC MS63 Morgan Dollar")
  # ParseResult(cert_company='NGC', grade_text='MS63', year=1921, ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ================================================================
# グレード・グレーダー パターン
# ================================================================

_GRADE_TYPES = r'(?:MS|PF|PR|AU|XF|EF|VF|SP|PL|UNC|Genuine|Details|VG|AG|FR|PO|F|G)'
_GRADE_SUFFIX = (
    r'(?:\s*\d{0,2})'
    r'(?:\s*(?:UC|ULTRA\s*CAMEO|CAMEO|CAM|RD|RB|BN|DCAM|DPL|PL|FB|FH|FT|FS|FBL|Details|\+|\*|W))*'
)
_GRADE_FULL = _GRADE_TYPES + _GRADE_SUFFIX

GRADER_GRADE_PATTERNS: list[re.Pattern] = [
    # NGC-PF68 ULTRA CAMEO  / NGC-AU58 (ハイフン)
    re.compile(r'\b(NGC|PCGS)[--]\s*(' + _GRADE_FULL + r')', re.IGNORECASE),
    # NGC(AU55) / PCGS(MS64) (括弧)
    re.compile(r'\b(NGC|PCGS)\s*[(\uff08]\s*(' + _GRADE_FULL + r')\s*[)\uff09]', re.IGNORECASE),
    # MS65 NGC / PF70 PCGS (グレード先行)
    re.compile(r'(' + _GRADE_FULL + r')\s+(NGC|PCGS)\b', re.IGNORECASE),
    # NGC MS70 / NGC PF69 UC (スペース区切り、標準)
    re.compile(r'\b(NGC|PCGS)\s+(?:鑑定品\s*)?(' + _GRADE_FULL + r')', re.IGNORECASE),
    # グレーダー名のみ (fallback)
    re.compile(r'\b(NGC|PCGS)\b', re.IGNORECASE),
]

# ================================================================
# cert_number パターン
# ================================================================

# NGC/PCGS の鑑定番号は通常 8〜10桁の数字
# タイトル内での例: "NGC#12345678", "NGC 12345678", "#87654321"
_CERT_PATTERNS: list[re.Pattern] = [
    # NGC# / PCGS# + 数字
    re.compile(r'(?:NGC|PCGS)\s*#\s*(\d{7,10})', re.IGNORECASE),
    # ハッシュ単体 + 数字 (タイトル後半)
    re.compile(r'#\s*(\d{7,10})\b'),
    # cert / no. / No + 数字
    re.compile(r'(?:cert|no\.?)\s*[:\s]?\s*(\d{7,10})', re.IGNORECASE),
    # スラッシュで区切られた長い数字 (eBay経由の場合)
    re.compile(r'\b(\d{8,10})\b'),
]

# ================================================================
# 年号パターン
# ================================================================

# 西暦 1000〜2029 (コインの年号範囲)
YEAR_PATTERN = re.compile(r'\b(1[0-9]{3}|20[0-2][0-9])\s*年?\b')

# ================================================================
# 額面パターン (優先度順)
# ================================================================

DENOMINATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # oz 表記 (重量金貨) — 分数パターンを整数より前に配置
    (re.compile(r'(1/20)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/10)\s*oz', re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/4)\s*oz',  re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1/2)\s*oz',  re.IGNORECASE), "{0}oz"),
    (re.compile(r'(5)\s*oz',    re.IGNORECASE), "{0}oz"),
    (re.compile(r'(2)\s*oz',    re.IGNORECASE), "{0}oz"),
    (re.compile(r'(1)\s*oz',    re.IGNORECASE), "{0}oz"),
    # 日本語通貨
    (re.compile(r'(\d+)\s*ポンド'),            "{0}ポンド"),
    (re.compile(r'(\d+)\s*ドル'),              "{0}ドル"),
    (re.compile(r'(\d+)\s*フラン'),            "{0}フラン"),
    (re.compile(r'(\d+)\s*マルク'),            "{0}マルク"),
    (re.compile(r'(\d+)\s*ルピー'),            "{0}ルピー"),
    (re.compile(r'(\d+)\s*ターラー'),          "{0}ターラー"),
    (re.compile(r'(\d+)\s*クローネ'),          "{0}クローネ"),
    (re.compile(r'(\d+)\s*ペソ'),              "{0}ペソ"),
    (re.compile(r'(\d+)\s*リラ'),              "{0}リラ"),
    (re.compile(r'(\d+)\s*レアル'),            "{0}レアル"),
    (re.compile(r'(\d+)\s*グルデン'),          "{0}グルデン"),
    (re.compile(r'(\d+)\s*ソブリン'),          "{0}ソブリン"),
    (re.compile(r'(\d+)\s*ギニー'),            "{0}ギニー"),
    (re.compile(r'(\d+)\s*シリング'),          "{0}シリング"),
    (re.compile(r'(\d+)\s*ペンス'),            "{0}ペンス"),
    (re.compile(r'(\d+)\s*ペニー'),            "{0}ペニー"),
    (re.compile(r'(\d+)\s*セント'),            "{0}セント"),
    (re.compile(r'(\d+)\s*円(?!ス)'),          "{0}円"),
    (re.compile(r'(\d+)\s*銭'),                "{0}銭"),
    # 英語表記
    (re.compile(r'(\d+)\s*Dollar', re.IGNORECASE),  "{0}ドル"),
    (re.compile(r'(\d+)\s*Pound', re.IGNORECASE),   "{0}ポンド"),
    (re.compile(r'(\d+)\s*Franc', re.IGNORECASE),   "{0}フラン"),
    (re.compile(r'(\d+)\s*Crown', re.IGNORECASE),   "{0}クラウン"),
    (re.compile(r'(\d+)\s*Mark', re.IGNORECASE),    "{0}マルク"),
    (re.compile(r'(\d+)\s*Cent', re.IGNORECASE),    "{0}セント"),
    (re.compile(r'(\d+)\s*Peso', re.IGNORECASE),    "{0}ペソ"),
    (re.compile(r'(\d+)\s*Sovereign', re.IGNORECASE), "{0}ソブリン"),
    (re.compile(r'(\d+)\s*Thaler', re.IGNORECASE),  "{0}ターラー"),
    (re.compile(r'(\d+)\s*Florin', re.IGNORECASE),  "{0}フローリン"),
    (re.compile(r'(\d+)\s*Guilder', re.IGNORECASE), "{0}グルデン"),
    (re.compile(r'(\d+)\s*Ducat', re.IGNORECASE),   "{0}ダカット"),
]

# ================================================================
# ParseResult
# ================================================================

@dataclass
class ParseResult:
    """parse_lot_title の戻り値"""
    title_raw: str = ""

    cert_company: Optional[str] = None    # "NGC" | "PCGS" | None
    cert_number: Optional[str] = None     # "12345678" | None
    grade_text: Optional[str] = None      # "MS63" | "PF69 UC" | None
    year: Optional[int] = None            # 1921 | None
    denomination: Optional[str] = None   # "20マルク" | "1oz" | None

    # confidence 要素ごとの詳細 (デバッグ用)
    _flags: dict = field(default_factory=dict, repr=False)

    @property
    def parse_confidence(self) -> float:
        """
        0.0 - 1.0 の信頼スコア。
        各フィールドの取得有無に応じて加算:
          cert_company  +0.25
          cert_number   +0.25
          grade_text    +0.20
          year          +0.20
          denomination  +0.10
        """
        score = 0.0
        if self.cert_company:
            score += 0.25
        if self.cert_number:
            score += 0.25
        if self.grade_text:
            score += 0.20
        if self.year:
            score += 0.20
        if self.denomination:
            score += 0.10
        return round(score, 2)

    def as_dict(self) -> dict:
        return {
            "cert_company":      self.cert_company,
            "cert_number":       self.cert_number,
            "grade_text":        self.grade_text,
            "year":              self.year,
            "denomination":      self.denomination,
            "parse_confidence":  self.parse_confidence,
        }


# ================================================================
# 公開 API
# ================================================================

def parse_lot_title(title: str) -> ParseResult:
    """
    Yahoo!落札タイトル (1行) から構造化フィールドを抽出する。

    Args:
        title: 生のタイトル文字列

    Returns:
        ParseResult
    """
    if not title or not isinstance(title, str):
        return ParseResult(title_raw=str(title or ""))

    result = ParseResult(title_raw=title)
    result.cert_company, result.grade_text = _extract_grader_grade(title)
    result.cert_number  = _extract_cert_number(title, result.cert_company)
    result.year         = _extract_year(title)
    result.denomination = _extract_denomination(title)
    return result


# ================================================================
# 内部実装
# ================================================================

def _extract_grader_grade(title: str) -> tuple[Optional[str], Optional[str]]:
    """(cert_company, grade_text) を返す。未取得の場合は None。"""
    for pat in GRADER_GRADE_PATTERNS:
        m = pat.search(title)
        if not m:
            continue
        groups = m.groups()
        if len(groups) == 2:
            # グレード先行パターンは (grade, grader) の順
            g0, g1 = groups
            if g1.upper() in ("NGC", "PCGS"):
                grader = g1.upper()
                grade  = _normalize_grade(g0)
            else:
                grader = g0.upper()
                grade  = _normalize_grade(g1)
        else:
            grader = groups[0].upper()
            grade  = None
        return grader, grade
    return None, None


def _normalize_grade(raw: str) -> str:
    """グレード文字列の表記揺れを吸収する。"""
    if not raw:
        return raw
    s = raw.strip()
    # ULTRA CAMEO → UC に短縮
    s = re.sub(r'ULTRA\s*CAMEO', 'UC', s, flags=re.IGNORECASE)
    # 余分な空白を1つに
    s = re.sub(r'\s+', ' ', s)
    return s.upper()


def _extract_cert_number(title: str, cert_company: Optional[str]) -> Optional[str]:
    """
    鑑定番号を抽出する。
    cert_company が取得済みの場合は会社名付きパターンを優先。
    """
    # 会社名付きパターン
    for pat in _CERT_PATTERNS[:2]:
        m = pat.search(title)
        if m:
            return m.group(1)

    # "cert / no." パターン
    m = _CERT_PATTERNS[2].search(title)
    if m:
        return m.group(1)

    # 8-10桁の数字 (cert_company が取れている場合のみ信用)
    if cert_company:
        m = _CERT_PATTERNS[3].search(title)
        if m:
            num = m.group(1)
            # 年号や価格と区別: 8桁以上のみ採用
            if len(num) >= 8:
                return num

    return None


def _extract_year(title: str) -> Optional[int]:
    """西暦年号を抽出する。複数ある場合は最小値（製造年に近い）を返す。"""
    matches = YEAR_PATTERN.findall(title)
    if not matches:
        return None
    years = [int(y) for y in matches]
    # コインの製造年として自然な範囲 (1000〜2029)
    valid = [y for y in years if 1000 <= y <= 2029]
    return min(valid) if valid else None


def _extract_denomination(title: str) -> Optional[str]:
    """額面を抽出する。最初にマッチしたパターンを返す。"""
    for pat, fmt in DENOMINATION_PATTERNS:
        m = pat.search(title)
        if m:
            return fmt.format(m.group(1))
    return None
