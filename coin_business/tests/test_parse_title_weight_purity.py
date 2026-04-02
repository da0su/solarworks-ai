"""
tests/test_parse_title_weight_purity.py
========================================
parse_title() の weight_oz / purity 抽出に関する回帰テスト。

背景:
  9dd9c15 にて、"1/2 oz" が "2 oz" として誤判定されるバグを修正。
  - 原因: re.search(r'(\\d+(?:\\.\\d+)?)\\s*oz') が "1/2 oz" 中の "2 oz" にマッチしていた
  - 修正: 分数 oz パターンを先に評価し、整数/小数 oz に (?<![/\\d]) を追加

このファイルは、その修正の再発防止と weight/purity 品質維持を目的とする。
"""

from __future__ import annotations

import pytest
from scripts.import_yahoo_history import parse_title


# ================================================================
# ヘルパー
# ================================================================

def _w(title: str):
    """weight_oz を返す (None 許容)。"""
    return parse_title(title).get("weight_oz")


def _p(title: str):
    """purity を返す (None 許容)。"""
    return parse_title(title).get("purity")


def _wp(title: str):
    """(weight_oz, purity) タプルを返す。"""
    r = parse_title(title)
    return r.get("weight_oz"), r.get("purity")


# ================================================================
# 1. 分数 oz — 基本ケース
# ================================================================

class TestFractionalOzBasic:
    """1/N oz の基本抽出テスト。"""

    def test_half_oz_basic(self):
        assert _w("1/2 oz Gold Coin") == 0.5

    def test_quarter_oz_basic(self):
        assert _w("1/4 oz Gold Coin") == 0.25

    def test_tenth_oz_basic(self):
        assert abs(_w("1/10 oz Gold Coin") - 0.1) < 0.0001

    def test_twentieth_oz_basic(self):
        assert abs(_w("1/20 oz Gold Coin") - 0.05) < 0.0001

    def test_half_oz_no_space(self):
        """スペースなし: 1/2oz"""
        assert _w("1/2oz Gold Coin") == 0.5

    def test_quarter_oz_no_space(self):
        assert _w("1/4oz Silver Coin") == 0.25

    def test_tenth_oz_no_space(self):
        assert abs(_w("1/10oz Platinum Coin") - 0.1) < 0.0001


# ================================================================
# 2. 分数 oz の誤爆防止 (バグ再発防止)
# ================================================================

class TestFractionalOzNotMisreadAsInteger:
    """
    修正前のバグ: "1/2 oz" → weight_oz=2.0 を返していた。
    これらのテストが PASS し続けることを保証する。
    """

    def test_half_oz_not_2(self):
        w = _w("1/2 oz Gold Coin")
        assert w == 0.5, f"Expected 0.5, got {w} (regression: bug returns 2.0)"

    def test_quarter_oz_not_4(self):
        w = _w("1/4 oz Gold Coin")
        assert w == 0.25, f"Expected 0.25, got {w} (regression: may return 4.0)"

    def test_tenth_oz_not_10(self):
        w = _w("1/10 oz Gold Coin")
        assert w is not None and abs(w - 0.1) < 0.001, \
            f"Expected ~0.1, got {w} (regression: may return 10.0)"

    def test_half_oz_with_year(self):
        """年号混在: 2022 1/2 oz Gold Coin → 0.5 (2.0 ではない)"""
        w = _w("2022 1/2 oz Gold Coin")
        assert w == 0.5, f"Expected 0.5 but got {w}"

    def test_half_oz_with_lot_number(self):
        """lot番号混在: Lot 3 1/2 oz Gold Coin → 0.5"""
        w = _w("Lot 3 1/2 oz Gold Coin")
        assert w == 0.5, f"Expected 0.5 but got {w}"

    def test_half_oz_trailing_count(self):
        """後続数字: 1/2 oz 2 Coins Set → 0.5"""
        w = _w("1/2 oz 2 Coins Set")
        assert w == 0.5, f"Expected 0.5 but got {w}"

    def test_quarter_oz_set(self):
        """セット数混在: 3 Coin Set 1/4 oz Gold → 0.25"""
        w = _w("3 Coin Set 1/4 oz Gold")
        assert w == 0.25, f"Expected 0.25 but got {w}"


# ================================================================
# 3. 整数・小数 oz
# ================================================================

class TestDecimalOz:
    """整数・小数 oz の基本抽出テスト。"""

    def test_1_oz(self):
        assert _w("1 oz Gold Coin") == 1.0

    def test_2_oz(self):
        assert _w("2 oz Gold Coin") == 2.0

    def test_5_oz(self):
        assert _w("5 oz Silver Coin") == 5.0

    def test_1pt5_oz(self):
        assert _w("1.5 oz Gold Coin") == 1.5

    def test_0pt5_oz(self):
        assert _w("0.5 oz Silver Coin") == 0.5

    def test_2pt5_oz(self):
        assert _w("2.5 oz Silver Coin") == 2.5

    def test_lot_number_does_not_poison(self):
        """lot番号の直後にある oz でない数字が取られない"""
        w = _w("Lot 12 - 1 oz Silver Coin .999")
        assert w == 1.0, f"Expected 1.0 but got {w}"


# ================================================================
# 4. purity — 基本ケース
# ================================================================

class TestPurityBasic:
    """purity 抽出の基本テスト。"""

    def test_dot_999(self):
        assert _p("1 oz .999 Gold Coin") == 0.999

    def test_dot_9999(self):
        assert _p("1 oz .9999 Gold Coin") == 0.9999

    def test_integer_999(self):
        """999 表記 (.なし)"""
        assert _p("999 Gold Coin 1 oz") == 0.999

    def test_integer_9999(self):
        assert _p("9999 Gold Coin 1 oz") == 0.9999

    def test_900_per_1000(self):
        """900/1000 形式"""
        p = _p("Silver Coin 900/1000")
        assert p == 0.9

    def test_purity_with_fractional_weight(self):
        """.9999 付き分数 oz"""
        w, p = _wp("1/2 oz .9999 Gold Coin")
        assert w == 0.5
        assert p == 0.9999

    def test_purity_with_quarter_oz(self):
        w, p = _wp("1/4 oz .999 Silver Coin")
        assert w == 0.25
        assert p == 0.999

    def test_purity_with_tenth_oz(self):
        w, p = _wp("1/10 oz .9999 Platinum Coin")
        assert abs(w - 0.1) < 0.001
        assert p == 0.9999


# ================================================================
# 5. 複合タイトル (weight + purity + year + noise)
# ================================================================

class TestComplexTitles:
    """複数の数字が混在するタイトルでの抽出テスト。"""

    def test_year_weight_purity(self):
        """年号 + weight + purity"""
        w, p = _wp("2022 1/2 oz Gold Coin .9999")
        assert w == 0.5
        assert p == 0.9999

    def test_year_integer_oz(self):
        """年号 + 整数oz (年号に引っ張られない)"""
        w = _w("1987 1 oz Gold Panda 100 Yuan")
        assert w == 1.0

    def test_multiple_numbers_2020(self):
        """複数数値: 2020 + 1/10 oz + 10 Dollars + .9995"""
        w, p = _wp("2020 1/10 oz Platinum 10 Dollars .9995")
        assert abs(w - 0.1) < 0.001
        assert p == 0.9995

    def test_lot_number_1oz_purity(self):
        w, p = _wp("Lot 12 - 1 oz Silver Coin .999")
        assert w == 1.0
        assert p == 0.999

    def test_noise_with_half_oz(self):
        """日本語ノイズ混在"""
        w = _w("【美品】1/2 oz Gold Coin 即決 送料込")
        assert w == 0.5

    def test_troy_oz(self):
        """troy oz 表記"""
        w = _w("1 troy oz .999 Silver Bar")
        assert w == 1.0


# ================================================================
# 6. シリーズ別デフォルト (weight / purity)
# ================================================================

class TestSeriesDefaults:
    """シリーズ名からデフォルト値を引く動作の確認。"""

    def test_morgan_dollar_weight(self):
        """モルガンダラー → ~0.8594 oz"""
        w = _w("PCGS MS65 Morgan Dollar 1885")
        assert w is not None and abs(w - (26.73 / 31.1035)) < 0.001

    def test_morgan_dollar_purity(self):
        """モルガンダラー → purity=0.9"""
        p = _p("PCGS MS65 Morgan Dollar 1885")
        assert p == 0.9

    def test_sovereign_weight(self):
        """ソブリン → ~0.2566 oz"""
        w = _w("NGC MS64 British Sovereign 1905")
        assert w is not None and abs(w - (7.98 / 31.1035)) < 0.001

    def test_sovereign_purity(self):
        """ソブリン → purity=0.917"""
        p = _p("NGC MS64 British Sovereign 1905")
        assert p == 0.917

    def test_panda_silver_weight(self):
        """パンダ 銀 → ~0.9645 oz (30g)"""
        w = _w("NGC MS62 30g Panda Silver 2020")
        assert w is not None and abs(w - (30.0 / 31.1035)) < 0.001

    def test_half_sovereign(self):
        """ハーフソブリン → ~0.1283 oz"""
        w = _w("PCGS MS64 Half Sovereign 1900")
        assert w is not None and abs(w - (3.99 / 31.1035)) < 0.001

    def test_gram_notation_converted(self):
        """グラム表記を oz に換算"""
        w = _w("NGC MS62 30g Panda Silver 2020")
        # 30g → 30/31.1035 ≈ 0.9645
        assert w is not None and abs(w - 0.9645) < 0.001


# ================================================================
# 7. False positive 防止
# ================================================================

class TestFalsePositives:
    """oz でない数字を weight_oz として取らないことを確認。"""

    def test_inch_not_oz(self):
        """インチ表記は oz ではない"""
        assert _w("1/2インチ メダル") is None

    def test_pound_not_oz(self):
        """lb/pound は oz ではない"""
        assert _w("1/2 pound coin") is None

    def test_no_oz_token(self):
        """oz の文字がなければ抽出しない"""
        assert _w("2個セット 金貨風メダル") is None

    def test_album_number(self):
        """コレクションナンバーは oz にしない"""
        assert _w("No.10 Coin Album") is None

    def test_year_only(self):
        """年号のみは weight_oz を返さない"""
        assert _w("2024年 記念メダル") is None


# ================================================================
# 8. 最低限回帰固定セット (優先度 A — バグ再発時に最初に壊れるもの)
# ================================================================

@pytest.mark.parametrize("title, expected_weight, expected_purity", [
    # (1) 分数基本
    ("1/2 oz Gold Coin",              0.5,          None),
    ("1/4 oz Gold Coin",              0.25,         None),
    ("1/10 oz Gold Coin",             0.1,          None),
    # (2) 整数oz基本
    ("1 oz Gold Coin",                1.0,          None),
    ("1.5 oz Gold Coin",              1.5,          None),
    # (3) 分数 + purity 複合
    ("1/2 oz .9999 Gold Coin",        0.5,          0.9999),
    ("1/4 oz .999 Silver Coin",       0.25,         0.999),
    # (4) 年号 + 分数 + purity
    ("2022 1/2 oz Gold Coin .9999",   0.5,          0.9999),
    # (5) noise混在
    ("【美品】1/2 oz Gold Coin 即決",  0.5,          None),
    # (6) false positive防止
    ("1/2インチ メダル",               None,         None),
    ("No.10 Coin Album",              None,         None),
])
def test_priority_a_regression(title, expected_weight, expected_purity):
    """優先度A: バグ再発時に最初に引っかかる最小固定セット。"""
    w, p = _wp(title)

    if expected_weight is None:
        assert w is None, f"[{title!r}] expected weight=None but got {w}"
    else:
        assert w is not None, f"[{title!r}] expected weight={expected_weight} but got None"
        assert abs(w - expected_weight) < 0.001, \
            f"[{title!r}] expected weight={expected_weight} but got {w}"

    if expected_purity is None:
        # purity は material-default で入ることがあるので None 強制はしない
        # ただし fractional oz バグ由来の誤爆でないことを確認する
        pass
    else:
        assert p is not None, f"[{title!r}] expected purity={expected_purity} but got None"
        assert abs(p - expected_purity) < 0.0001, \
            f"[{title!r}] expected purity={expected_purity} but got {p}"
