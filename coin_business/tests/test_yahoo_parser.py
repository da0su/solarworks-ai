"""
tests/test_yahoo_parser.py
============================
yahoo.parser モジュールのユニットテスト。

テスト項目:
  1. cert_company 抽出 (NGC / PCGS / None)
  2. grade_text 抽出
  3. cert_number 抽出
  4. year 抽出
  5. denomination 抽出
  6. parse_confidence 計算
  7. 空文字・None の安全処理

実行:
  cd coin_business
  python -m pytest tests/test_yahoo_parser.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from yahoo.parser import parse_lot_title, ParseResult


# ================================================================
# 1. cert_company 抽出
# ================================================================

class TestCertCompanyExtraction:
    """NGC / PCGS が各種表記で正しく取得できることを確認する。"""

    def test_ngc_space_grade(self):
        r = parse_lot_title("1921 Morgan Silver Dollar NGC MS63")
        assert r.cert_company == "NGC"

    def test_pcgs_space_grade(self):
        r = parse_lot_title("PCGS MS65 1986 American Gold Eagle 1oz")
        assert r.cert_company == "PCGS"

    def test_ngc_hyphen_grade(self):
        r = parse_lot_title("1914 Prussia Germany 20 Mark Gold NGC-MS62")
        assert r.cert_company == "NGC"

    def test_ngc_paren_grade(self):
        r = parse_lot_title("イギリス ソブリン金貨 NGC(MS64) 1872年")
        assert r.cert_company == "NGC"

    def test_pcgs_fullwidth_hyphen(self):
        # 全角ハイフン
        r = parse_lot_title("1881 Morgan Dollar PCGS－MS64")
        assert r.cert_company == "PCGS"

    def test_ngc_japanese_prefix(self):
        r = parse_lot_title("NGC 鑑定品 PF69 UC アメリカ プルーフ")
        assert r.cert_company == "NGC"

    def test_no_grader(self):
        r = parse_lot_title("1921 Morgan Silver Dollar MS63 状態良好")
        assert r.cert_company is None

    def test_lowercase_ngc(self):
        r = parse_lot_title("ngc ms65 1986 American Eagle")
        assert r.cert_company == "NGC"

    def test_none_input(self):
        r = parse_lot_title(None)
        assert r.cert_company is None

    def test_empty_string(self):
        r = parse_lot_title("")
        assert r.cert_company is None


# ================================================================
# 2. grade_text 抽出
# ================================================================

class TestGradeTextExtraction:
    """grade_text が正しく抽出・正規化されることを確認する。"""

    def test_ms63(self):
        r = parse_lot_title("NGC MS63 Morgan Dollar 1921")
        assert r.grade_text == "MS63"

    def test_pf69_uc(self):
        r = parse_lot_title("PCGS PF69 ULTRA CAMEO 1986 American Eagle")
        # ULTRA CAMEO → UC に正規化
        assert r.grade_text is not None
        assert "UC" in r.grade_text

    def test_au58(self):
        r = parse_lot_title("1914 Germany 20 Mark NGC-AU58")
        assert r.grade_text == "AU58"

    def test_ms65rd(self):
        r = parse_lot_title("PCGS MS65RD 1955 Lincoln Cent")
        assert r.grade_text == "MS65RD"

    def test_grade_before_grader(self):
        """グレードがグレーダー名の前にある表記"""
        r = parse_lot_title("MS64 NGC 1881 Morgan Dollar")
        assert r.grade_text is not None
        assert "MS64" in r.grade_text
        assert r.cert_company == "NGC"

    def test_genuine(self):
        r = parse_lot_title("1893 S Morgan Dollar NGC Genuine")
        assert r.grade_text is not None
        assert "GENUINE" in r.grade_text.upper()

    def test_grader_only_no_grade(self):
        """グレーダー名だけでグレードが取れない場合"""
        r = parse_lot_title("NGC 鑑定品 Morgan Dollar")
        assert r.cert_company == "NGC"
        # grade_text は None でも OK (fallback パターン)


# ================================================================
# 3. cert_number 抽出
# ================================================================

class TestCertNumberExtraction:
    """cert_number (鑑定番号) が正しく抽出されることを確認する。"""

    def test_ngc_hash_number(self):
        r = parse_lot_title("NGC#12345678 MS63 1921 Morgan Dollar")
        assert r.cert_number == "12345678"

    def test_pcgs_hash_number(self):
        r = parse_lot_title("PCGS # 87654321 MS65 1986 Eagle")
        assert r.cert_number == "87654321"

    def test_cert_colon(self):
        r = parse_lot_title("NGC MS64 cert: 99887766 Morgan 1881")
        assert r.cert_number == "99887766"

    def test_no_cert_number(self):
        r = parse_lot_title("NGC MS63 1921 Morgan Dollar")
        # 8桁の数字がない場合は None
        assert r.cert_number is None

    def test_year_not_taken_as_cert(self):
        """4桁年号を cert_number として誤認しないことを確認。"""
        r = parse_lot_title("NGC MS63 1921 Morgan Dollar")
        assert r.cert_number is None  # 1921 は cert_number ではなく year

    def test_8digit_with_grader(self):
        """グレーダーあり + 8桁数字 → cert_number"""
        r = parse_lot_title("NGC MS65 1986 American Eagle 12345678")
        assert r.cert_number == "12345678"


# ================================================================
# 4. year 抽出
# ================================================================

class TestYearExtraction:
    """年号が正しく抽出されることを確認する。"""

    def test_basic_year(self):
        r = parse_lot_title("1921 Morgan Silver Dollar NGC MS63")
        assert r.year == 1921

    def test_year_with_kanji(self):
        r = parse_lot_title("1872年 イギリス ソブリン金貨 NGC MS64")
        assert r.year == 1872

    def test_multiple_years_takes_min(self):
        """複数の年号がある場合は最小値（製造年に近い）を返す。"""
        r = parse_lot_title("1914 Germany 20 Mark NGC MS62 (2023年鑑定)")
        assert r.year == 1914

    def test_no_year(self):
        r = parse_lot_title("Morgan Silver Dollar NGC MS63")
        assert r.year is None

    def test_year_1000(self):
        """1000年代のコイン"""
        r = parse_lot_title("1066 Anglo-Saxon Penny NGC MS61")
        assert r.year == 1066

    def test_year_2020(self):
        r = parse_lot_title("2020 American Silver Eagle PCGS MS70")
        assert r.year == 2020


# ================================================================
# 5. denomination 抽出
# ================================================================

class TestDenominationExtraction:
    """額面が正しく抽出されることを確認する。"""

    def test_dollar(self):
        r = parse_lot_title("1921 Morgan 1 Dollar NGC MS63")
        assert r.denomination == "1ドル"

    def test_mark_japanese(self):
        r = parse_lot_title("1914 ドイツ プロイセン 20マルク 金貨 NGC MS62")
        assert r.denomination == "20マルク"

    def test_oz(self):
        r = parse_lot_title("1986 American Gold Eagle 1oz PCGS MS70")
        assert r.denomination == "1oz"

    def test_half_oz(self):
        r = parse_lot_title("1986 American Gold Eagle 1/2oz NGC MS69")
        assert r.denomination == "1/2oz"

    def test_pound(self):
        r = parse_lot_title("2 Pound Gold Sovereign NGC MS64")
        assert r.denomination == "2ポンド"

    def test_no_denomination(self):
        r = parse_lot_title("Morgan Silver Dollar NGC MS63")
        assert r.denomination is None

    def test_franc(self):
        r = parse_lot_title("フランス 20フラン 金貨 NGC MS62 1869年")
        assert r.denomination == "20フラン"


# ================================================================
# 6. parse_confidence 計算
# ================================================================

class TestParseConfidence:
    """parse_confidence の範囲と計算が正しいことを確認する。"""

    def test_full_confidence(self):
        """全フィールド取得できれば 1.0"""
        r = parse_lot_title("1921 NGC MS63 1 Dollar Morgan #12345678")
        # cert_company=NGC(+0.25), cert_number=12345678(+0.25),
        # grade_text=MS63(+0.20), year=1921(+0.20), denomination=1ドル(+0.10)
        assert r.parse_confidence == 1.0

    def test_grader_and_grade_only(self):
        """cert + grade のみ (0.45)"""
        r = parse_lot_title("NGC MS63 Morgan Dollar")
        # cert_company(+0.25) + grade_text(+0.20) = 0.45
        assert r.parse_confidence == pytest.approx(0.45)

    def test_zero_confidence(self):
        """何も取れなければ 0.0"""
        r = parse_lot_title("状態良好 美品 銀貨")
        assert r.parse_confidence == 0.0

    def test_confidence_range(self):
        """常に 0.0〜1.0 の範囲に収まること"""
        titles = [
            "NGC MS63 1921 Morgan Dollar #12345678 1 Dollar",
            "PCGS PF69 Ultra Cameo 2020 American Silver Eagle",
            "古いコイン 珍しい",
            "",
            None,
            "1921 Morgan Dollar",
        ]
        for t in titles:
            r = parse_lot_title(t)
            assert 0.0 <= r.parse_confidence <= 1.0, f"Out of range for: {t!r}"

    def test_as_dict_includes_confidence(self):
        r = parse_lot_title("NGC MS63 1921 Morgan Dollar")
        d = r.as_dict()
        assert "parse_confidence" in d
        assert isinstance(d["parse_confidence"], float)


# ================================================================
# 7. 安全処理 (None / 空文字 / 非文字列)
# ================================================================

class TestEdgeCases:
    """異常入力でも例外が出ないことを確認する。"""

    def test_none_returns_empty_result(self):
        r = parse_lot_title(None)
        assert isinstance(r, ParseResult)
        assert r.cert_company is None
        assert r.parse_confidence == 0.0

    def test_empty_returns_empty_result(self):
        r = parse_lot_title("")
        assert isinstance(r, ParseResult)

    def test_integer_input(self):
        r = parse_lot_title(12345)  # type: ignore
        assert isinstance(r, ParseResult)

    def test_very_long_title(self):
        long_title = "NGC MS63 " + "A" * 5000
        r = parse_lot_title(long_title)
        assert r.cert_company == "NGC"

    def test_japanese_only(self):
        r = parse_lot_title("昭和時代の銀貨 美品 レア 送料無料")
        assert r.cert_company is None
        assert r.year is None

    def test_title_raw_preserved(self):
        title = "NGC MS63 1921 Morgan"
        r = parse_lot_title(title)
        assert r.title_raw == title
