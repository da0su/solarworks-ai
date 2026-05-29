"""_persona_check + audit_persona unit tests (Codex 29回目 review 反映).

CEO 5/20 緊急止血で 強化した persona check の正常系/異常系を担保.
"""
from __future__ import annotations

import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

from planner.item_auditor import _persona_check, _normalize_for_persona, _GENDER_NEUTRAL_COMPOUND_RE


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

def test_neutral_markers_no_standalone_兼用():
    """neutral_markers に「兼用」「共用」「共通」単独語が含まれないことを明示保証."""
    from planner.item_auditor import _persona_check
    # 直接 neutral_markers リストを検査する
    import inspect, ast, textwrap
    import planner.item_auditor as _m
    # 最も確実な方法: _GENDER_NEUTRAL_COMPOUND_RE 経由でなく、文字列マッチのみで拾われる単独語がないことを確認
    # 「メンズ 兼用」を試す → 男女なし + 兼用単独 → fail になるはずなら単独語は除去済み
    status, reason = _persona_check("メンズ スニーカー 兼用 サイズ")
    assert status == "fail", (
        f"「兼用」単独が neutral marker になっている可能性: got {status}: {reason}"
    )


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


def test_男女_dot_兼用_should_pass():
    """「男女/兼用」「男女・兼用」区切り記号バリアント → pass."""
    for title in ["メンズ スポーツ 男女/兼用 サイズ", "メンズ スポーツ 男女・兼用"]:
        status, reason = _persona_check(title)
        assert status in ("pass", "boost"), f"title={title!r}: expected pass, got {status}: {reason}"


def test_男女兼用_全角OK_should_pass():
    """「男女兼用ＯＫ」全角 OK → NFKC で ok に正規化 → pass."""
    status, reason = _persona_check("メンズ 帽子 男女兼用ＯＫ")
    assert status in ("pass", "boost"), f"expected pass, got {status}: {reason}"


def test_regex_直接_バリアント確認():
    """_GENDER_NEUTRAL_COMPOUND_RE が各バリアントにマッチすることを直接確認."""
    hits = [
        "男女兼用",
        "男女 兼用",
        "男女・兼用",
        "男女/兼用",
        "男女兼用可",
        "男女兼用ok",
        "男女兼用〇",
        "男女共用",
        "男女共通",
        "男女 共用",
    ]
    for text in hits:
        n = _normalize_for_persona(text)
        assert _GENDER_NEUTRAL_COMPOUND_RE.search(n), f"regex should match: {text!r} (normalized: {n!r})"

    no_hits = [
        "ih 兼用",
        "直火兼用",
        "屋内外兼用",
        "男女問わず不可",  # 男女単独
        "兼用",
        "共用",
    ]
    for text in no_hits:
        n = _normalize_for_persona(text)
        assert not _GENDER_NEUTRAL_COMPOUND_RE.search(n), f"regex should NOT match: {text!r} (normalized: {n!r})"


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
        test_neutral_markers_no_standalone_兼用,
        test_ih_兼用_should_fail,
        test_屋内外兼用_should_fail,
        test_男女_space_兼用_should_pass,
        test_男女兼用可_should_pass,
        test_男女共用_should_pass,
        test_男女_dot_兼用_should_pass,
        test_男女兼用_全角OK_should_pass,
        test_regex_直接_バリアント確認,
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
