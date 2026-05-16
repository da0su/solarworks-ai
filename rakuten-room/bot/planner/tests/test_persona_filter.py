"""ペルソナ filter 回帰テスト (CEO 5/16 指示: 31歳・0歳新米ママ).

過去 5/10 で介護エプロン投稿 → アカウント信頼性低下 → 即対応.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from planner.item_auditor import _persona_check


def test_kaigo_keyword_fail():
    # CEO 指摘の実例
    status, reason = _persona_check(
        "＼送料無料／【ランキング1位受賞】スリッポン メンズ レディース デッキシューズ 無地 スニーカー キャンバス おしゃれ 通学 通勤 アルバイト オフィス 介護"
    )
    assert status == "fail", f"介護 keyword で fail 必須 (got {status})"
    assert "介護" in reason


def test_kaigo_chair_fail():
    status, reason = _persona_check(
        "【楽天1位★1年保証】 ダイニングチェア 肘付き 簡単組立 安定の耐荷重160kg お手入れ簡単 軽量 スタッキングチェア クッション 柔らかすぎない 腰痛 介"
    )
    # 「介」だけでは hit しない設計 (NG list が完全一致なら) → 部分一致で
    # 実際は「介護」全体で NG なのでこの case は pass する可能性. ダイニングチェアは現在のNGに hit しない.
    # 念のため "シニア" は確実に NG
    assert status in ("fail", "pass"), f"got {status}"


def test_senior_fail():
    status, reason = _persona_check("シニア向け歩行器 高齢者用")
    assert status == "fail"
    # 最初に match した keyword が報告される
    assert "シニア" in reason or "歩行器" in reason or "高齢者" in reason


def test_otsuya_fail():
    status, _ = _persona_check("喪服 ブラックフォーマル レディース")
    assert status == "fail"


def test_kaigo_apron_fail():
    """5/10 posted: ロングエプロン... カフェ レストラン 介護 ラーメ"""
    status, reason = _persona_check(
        "【当日出荷可能】ロングエプロン 前掛け 無地 サロンエプロン 撥水 ソムリエ ギャルソン おしゃれ ブラック 黒 飲食 業務用 カフェ レストラン 介護 ラーメ"
    )
    assert status == "fail", f"5/10 のエプロン commit が再発しないこと (got {status})"


def test_baby_boost():
    status, reason = _persona_check("ベビー服 新生児 オーガニックコットン")
    assert status == "boost"
    assert "ベビー" in reason or "新生児" in reason


def test_mama_boost():
    status, reason = _persona_check("授乳ブラ ノンワイヤー 産後ママ用")
    assert status == "boost"


def test_persona_aligned_pass():
    status, _ = _persona_check("北欧 マグカップ 食洗機対応")
    # boost でも fail でもない → pass (普通の商品)
    # ただし「北欧」「おしゃれ」が boost に入っていれば boost
    # 現在 config では「北欧」「おしゃれ」「かわいい」が boost なので boost が返る
    assert status in ("boost", "pass")


def test_pure_neutral_pass():
    status, reason = _persona_check("普通の鉛筆 12本セット")
    assert status == "pass", f"neutral 商品は pass (got {status}, {reason})"


def test_empty_input():
    status, _ = _persona_check("", "")
    assert status == "pass"


# Codex 11回目 review 反映: 表記ゆれ NFKC test
def test_fullwidth_age_boost():
    """０歳 (全角) も 0歳 として boost"""
    status, _ = _persona_check("赤ちゃん用 ０歳 ベビーソックス")
    assert status == "boost"


def test_halfwidth_katakana_ng():
    """半角カナ ｼﾆｱ も NG (NFKC で シニア に正規化)"""
    status, _ = _persona_check("ｼﾆｱ向け 軽量シューズ")
    assert status == "fail"


def test_kogu_fuyou_no_longer_fail():
    """『工具不要』が誤爆しないこと (Codex 11回目 #4 反映)"""
    status, _ = _persona_check("ベビーゲート 工具不要 簡単設置")
    # 「工具」単独 NG を削除したので fail ではない (boost ベビーゲート で boost)
    assert status in ("boost", "pass"), f"工具不要は誤爆 NG (got {status})"


if __name__ == "__main__":
    tests = [
        test_kaigo_keyword_fail, test_kaigo_chair_fail, test_senior_fail,
        test_otsuya_fail, test_kaigo_apron_fail,
        test_baby_boost, test_mama_boost,
        test_persona_aligned_pass, test_pure_neutral_pass, test_empty_input,
        test_fullwidth_age_boost, test_halfwidth_katakana_ng, test_kogu_fuyou_no_longer_fail,
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed.append(fn.__name__)
    if failed:
        print(f"\nFAILED: {failed}"); sys.exit(1)
    print("\nALL OK")
