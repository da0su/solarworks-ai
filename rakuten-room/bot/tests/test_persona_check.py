"""_persona_check + audit_persona unit tests (Codex 29回目 review 反映).

CEO 5/20 緊急止血で 強化した persona check の正常系/異常系を担保.
"""
from __future__ import annotations

import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

from planner.item_auditor import _persona_check, _normalize_for_persona


def test_normalize_full_width_to_half():
    """全角 → 半角 NFKC normalize."""
    assert _normalize_for_persona("０歳") == "0歳"
    assert _normalize_for_persona("ｵﾑﾂ") == "オムツ"
    assert _normalize_for_persona("ＭＥＮＳ") == "mens"


def test_persona_ng_basic():
    """basic NG keyword 検出."""
    status, reason = _persona_check("介護用エプロン")
    assert status == "fail", reason
    assert "介護" in reason


def test_persona_ng_in_shop_name():
    """shop_name にも NG 検出 (Codex 29回目 #3)."""
    status, reason = _persona_check(
        "便利なスティック",
        shop_name="シルバーケア介護専門店",
    )
    assert status == "fail", reason


def test_persona_ng_in_description():
    """description にも NG 検出 (Codex 29回目 #3)."""
    status, reason = _persona_check(
        "便利な杖",
        description="高齢者の介護にぴったり",
    )
    assert status == "fail", reason


def test_masculine_context_belt_men():
    """ベルト × メンズ → fail (Codex 29回目 #3)."""
    status, reason = _persona_check("メンズ ベルト 革 ビジネス")
    assert status == "fail"
    assert "ベルト" in reason or "masculine" in reason


def test_neutral_marker_avoids_false_positive():
    """ベルト + メンズ + レディース → pass (false positive 回避)."""
    status, reason = _persona_check("メンズ レディース 兼用 ベルト 革")
    # NG 単独 keyword なし & neutral marker あり → pass か boost
    assert status in ("pass", "boost"), f"expected pass/boost, got {status}: {reason}"


def test_unisex_sauna_suit():
    """サウナスーツ メンズ レディース 2点 → pass (false positive 回避)."""
    status, _ = _persona_check("サウナスーツ メンズ レディース 2点入り トレーニング")
    assert status in ("pass", "boost")


def test_boost_keyword_baby():
    """ベビー → boost."""
    status, reason = _persona_check("ベビー服 新生児")
    assert status == "boost"
    assert "ベビー" in reason or "新生児" in reason


def test_override_keyword_child_seat():
    """チャイルドシート (override) → NG check skip → boost."""
    status, _ = _persona_check("車載 チャイルドシート ISOFIX 安全")
    # 「車載」は NG だが override で skip → boost or pass
    assert status in ("boost", "pass"), status


def test_persona_ng_with_full_width():
    """全角介護 → fail (NFKC normalize)."""
    status, _ = _persona_check("シニア介護用品")  # 全角
    assert status == "fail"


def test_masculine_context_no_marker_pass():
    """ベルト 単体 (女性ベルトかも) → pass."""
    # ベルト だけだと判定不可 → pass
    status, _ = _persona_check("レザーベルト おしゃれ")
    assert status in ("pass", "boost")


def test_combined_text_evaluation():
    """title + shop + desc 全部 NFKC + lower + space joined."""
    status, reason = _persona_check(
        title="春のベルト",
        shop_name="メンズ専門店",  # NG kw 'メンズ専用' に近いが完全一致しない
        description="紳士向け 革 ベルト",  # 紳士 = NG
    )
    assert status == "fail", reason


# --- Codex 32回目: 「兼用」単独削除 + 男女複合語 regex バリアント 回帰テスト ---

def test_ih_兼用_should_fail():
    """IH/直火兼用 はジェンダー文脈なし → メンズ付きなら fail."""
    status, reason = _persona_check("メンズ フライパン IH 直火兼用")
    # IH/直火兼用 は非ジェンダー文脈 → neutral marker にならない → fail
    assert status == "fail", f"expected fail, got {status}: {reason}"


def test_屋内外兼用_should_fail():
    """屋内外兼用 はジェンダー文脈なし → メンズ付きなら fail."""
    status, reason = _persona_check("メンズ スニーカー 屋内外兼用")
    assert status == "fail", f"expected fail, got {status}: {reason}"


def test_男女_space_兼用_should_pass():
    """「男女 兼用」(スペース区切り) → regex で中立マーカとして認識 → pass."""
    status, reason = _persona_check("スニーカー メンズ 男女 兼用 サイズ多彩")
    assert status in ("pass", "boost"), f"expected pass, got {status}: {reason}"


def test_男女兼用可_should_pass():
    """「男女兼用可」(接尾語あり) → pass."""
    status, reason = _persona_check("スポーツウォッチ メンズ 男女兼用可")
    assert status in ("pass", "boost"), f"expected pass, got {status}: {reason}"


def test_男女共用_should_pass():
    """「男女共用」 → pass."""
    status, reason = _persona_check("メンズ レインコート 男女共用 防水")
    assert status in ("pass", "boost"), f"expected pass, got {status}: {reason}"


if __name__ == "__main__":
    import traceback
    tests = [
        test_normalize_full_width_to_half,
        test_persona_ng_basic,
        test_persona_ng_in_shop_name,
        test_persona_ng_in_description,
        test_masculine_context_belt_men,
        test_neutral_marker_avoids_false_positive,
        test_unisex_sauna_suit,
        test_boost_keyword_baby,
        test_override_keyword_child_seat,
        test_persona_ng_with_full_width,
        test_masculine_context_no_marker_pass,
        test_combined_text_evaluation,
        # Codex 32回目 追加
        test_ih_兼用_should_fail,
        test_屋内外兼用_should_fail,
        test_男女_space_兼用_should_pass,
        test_男女兼用可_should_pass,
        test_男女共用_should_pass,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception:
            print(f"ERR : {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n=> {passed} pass / {failed} fail")
    sys.exit(0 if failed == 0 else 1)
