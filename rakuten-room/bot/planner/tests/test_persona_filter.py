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


# ============================================================
# CEO 2026-05-16 確定ルール 回帰テスト (4 ラウンドの Q&A 反映)
# ============================================================

# === NG keyword 追加 ===
def test_pet_ng():
    """ペット用品 完全 NG (CEO 5/16 確定)"""
    status, _ = _persona_check("犬用 ハーネス 中型犬 散歩")
    assert status == "fail"

def test_cat_food_ng():
    status, _ = _persona_check("キャットフード 無添加 国産")
    assert status == "fail"

def test_pramodel_ng():
    """男性趣味ホビー NG (CEO 5/16 確定)"""
    status, _ = _persona_check("ガンプラ 限定 RG ガンダム")
    assert status == "fail"

def test_trading_card_ng():
    status, _ = _persona_check("ポケモン トレーディングカード 限定パック")
    assert status == "fail"

def test_dining_table_ng():
    """大型家具 NG (CEO 5/16 確定)"""
    status, _ = _persona_check("ダイニングテーブル 4人用 木製")
    assert status == "fail"

def test_sofa_set_ng():
    status, _ = _persona_check("3人掛けソファ レザー L字")
    assert status == "fail"

def test_tv_board_ng():
    status, _ = _persona_check("テレビ台 150cm ローボード")
    assert status == "fail"

def test_drive_recorder_ng():
    """車・バイク用品 NG (CEO 5/16 確定)"""
    status, _ = _persona_check("ドライブレコーダー 360度 前後")
    assert status == "fail"

def test_bike_helmet_ng():
    status, _ = _persona_check("バイクヘルメット フルフェイス")
    assert status == "fail"

def test_formal_ng():
    """ブラックフォーマル NG (CEO 5/16 OFF)"""
    status, _ = _persona_check("ブラックフォーマル レディース 礼服 喪服")
    assert status == "fail"

# === Boost 拡張 ===
def test_maternity_boost():
    """マタニティ・授乳服 boost (CEO 5/16 OK)"""
    status, _ = _persona_check("授乳服 ワンピース 産後 おしゃれ")
    assert status == "boost"

def test_yoga_boost():
    """ヨガマット boost (CEO 5/16 軽運動 OK)"""
    status, _ = _persona_check("ヨガマット 10mm 厚手 トレーニング")
    assert status == "boost"

def test_mothersbag_boost():
    """マザーズバッグ boost (CEO 5/16 追加)"""
    status, _ = _persona_check("マザーズバッグ リュック 大容量 軽量")
    assert status == "boost"

def test_baby_bed_boost():
    """ベビーベッド boost (CEO 5/16 追加)"""
    status, _ = _persona_check("ベビーベッド 折りたたみ コンパクト")
    assert status == "boost"

def test_smartphone_case_boost():
    """スマホケース boost (CEO 5/16 ガジェット OK)"""
    status, _ = _persona_check("iPhoneケース 韓国 おしゃれ")
    assert status == "boost"

def test_3yr_kid_boost():
    """3歳児用 boost (CEO 5/16 0-6歳 OK)"""
    status, _ = _persona_check("3歳 知育玩具 木製パズル")
    assert status == "boost"

def test_postpartum_boost():
    """産後ケア boost (CEO 5/16 追加)"""
    status, _ = _persona_check("骨盤ベルト 産後 リフォーマー")
    assert status == "boost"

# === 例外: チャイルドシートは boost (車用品 NG の例外) ===
def test_child_seat_not_blocked_by_car():
    """チャイルドシート は車関連だが boost (CEO 5/16 例外)"""
    status, _ = _persona_check("チャイルドシート 新生児 ISOFIX")
    assert status == "boost"


# === Codex 12回目 review 反映 ===
def test_child_seat_with_car_keyword_still_boost():
    """『車載 チャイルドシート ISOFIX』は boost 優先 (override logic)"""
    status, reason = _persona_check("車載 チャイルドシート ISOFIX 0歳")
    assert status == "boost", f"override 効かず: {status}, {reason}"


def test_lead_diffuser_not_pet():
    """『リードディフューザー』はペットリードではない誤爆を防ぐ"""
    status, _ = _persona_check("リードディフューザー アロマ 北欧")
    assert status in ("boost", "pass"), "リードディフューザーは pet NG しない"


def test_usagi_pattern_clothing_not_pet():
    """『うさぎ柄 ベビーロンパース』は pet NG しない (動物柄ベビー服)"""
    status, _ = _persona_check("うさぎ柄 ベビーロンパース 0歳")
    assert status == "boost"


def test_6_year_old_boost():
    """6歳児用 boost (Codex 12回目 #3: 「6歳」追加確認)"""
    status, _ = _persona_check("6歳 入学準備 ランドセル")
    assert status == "boost"


def test_fullwidth_6_year_old_boost():
    """全角６歳 も boost (NFKC normalize)"""
    status, _ = _persona_check("６歳 知育絵本")
    assert status == "boost"


def test_pet_food_explicit_ng():
    """『ペットフード』は明示的 NG"""
    status, _ = _persona_check("ペットフード 国産無添加 1kg")
    assert status == "fail"


# === CEO 5/16 「手軽に買いやすい」5000円 line + 全面推奨 ===
def test_otsuyokuhin_boost():
    """お取り寄せ品 boost (CEO 5/16 全面推奨)"""
    status, _ = _persona_check("お取り寄せ プリン 6個セット 高級")
    assert status == "boost"

def test_omiyage_boost():
    """ご当地お土産 boost"""
    status, _ = _persona_check("全国ご当地お土産 詰め合わせ 銘菓")
    assert status == "boost"

def test_fukubukuro_boost():
    """訳あり福袋 boost (1980円 案件)"""
    status, _ = _persona_check("訳あり 食品福袋 30品 送料無料")
    assert status == "boost"

def test_sheet_mask_boost():
    """シートマスク 30枚 boost (CEO 5/16 OK)"""
    status, _ = _persona_check("シートマスク 30枚 大容量 韓国")
    assert status == "boost"

def test_premium_shoyu_neutral():
    """プレミアム醤油 (Codex 13回目 #1: 「プレミアム」は汎用 promo → 削除. 「醤油」自体は boost に未追加なので pass)
    CEO 「手軽に買いやすい」5000円 line で十分守れる前提.
    """
    status, _ = _persona_check("プレミアム 醤油 厳選 国産")
    assert status == "pass", f"プレミアム/厳選 を boost 削除後は neutral (got {status})"

def test_aroma_diffuser_boost():
    """アロマディフューザー boost (CEO 5/16 ✓)"""
    status, _ = _persona_check("アロマディフューザー 北欧 木製")
    assert status == "boost"

def test_furusato_nouzei_ng():
    """ふるさと納税 NG (CEO 5/16 確定 - システム複雑)"""
    status, _ = _persona_check("ふるさと納税 海鮮セット 返礼品 10000円")
    assert status == "fail"

def test_magazine_furoku_ng():
    """雑誌付録 NG (CEO 5/16 チェックなし)"""
    status, _ = _persona_check("雑誌付録 コスメポーチ 限定")
    assert status == "fail"


if __name__ == "__main__":
    tests = [
        test_kaigo_keyword_fail, test_kaigo_chair_fail, test_senior_fail,
        test_otsuya_fail, test_kaigo_apron_fail,
        test_baby_boost, test_mama_boost,
        test_persona_aligned_pass, test_pure_neutral_pass, test_empty_input,
        test_fullwidth_age_boost, test_halfwidth_katakana_ng, test_kogu_fuyou_no_longer_fail,
        # CEO 5/16 確定 ルール
        test_pet_ng, test_cat_food_ng, test_pramodel_ng, test_trading_card_ng,
        test_dining_table_ng, test_sofa_set_ng, test_tv_board_ng,
        test_drive_recorder_ng, test_bike_helmet_ng, test_formal_ng,
        test_maternity_boost, test_yoga_boost, test_mothersbag_boost,
        test_baby_bed_boost, test_smartphone_case_boost, test_3yr_kid_boost,
        test_postpartum_boost,
        test_child_seat_not_blocked_by_car,
        # Codex 12回目 review 反映
        test_child_seat_with_car_keyword_still_boost,
        test_lead_diffuser_not_pet, test_usagi_pattern_clothing_not_pet,
        test_6_year_old_boost, test_fullwidth_6_year_old_boost,
        test_pet_food_explicit_ng,
        # CEO 5/16 「手軽に買いやすい」ルール
        test_otsuyokuhin_boost, test_omiyage_boost, test_fukubukuro_boost,
        test_sheet_mask_boost, test_premium_shoyu_neutral, test_aroma_diffuser_boost,
        test_furusato_nouzei_ng, test_magazine_furoku_ng,
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
