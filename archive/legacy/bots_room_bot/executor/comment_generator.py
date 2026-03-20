"""ROOM BOT v2 - コメント自動生成（120点ROOM運用BOT版 v2）

30〜40代女性向けの自然なROOM投稿文を生成する。
- 5テンプレート + book専用テンプレート
- ジャンル別語彙（9ジャンル + default）
- 語尾バリエーション辞書 + 直近3件重複防止
- 季節感・ROOMらしい言い回し・使ってみたい感
- ヘッドライン: ブランド名+カテゴリ名
- クロージングライン
- 80〜140文字目安
"""

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# ジャンル判定キーワード
# ============================================================
GENRE_KEYWORDS = {
    "beauty": [
        "コスメ", "美容", "化粧", "スキンケア", "ヘアアイロン", "ドライヤー",
        "シャンプー", "リップ", "ファンデ", "日焼け止め", "UVケア", "ネイル",
        "美顔器", "ヘアケア", "ブラシ", "ミラー", "鏡", "ポーチ", "耐熱ケース",
        "ヘアオイル", "トリートメント", "アイシャドウ", "マスカラ", "クレンジング",
        "化粧水", "乳液", "美容液", "フェイスマスク", "フェイスパック",
        "カール", "ストレート", "コテ", "2WAY",
    ],
    "appliance": [
        "家電", "スチームアイロン", "衣類スチーマー", "掃除機", "加湿器", "除湿", "扇風機",
        "ヒーター", "電気", "充電", "ワイヤレス", "イヤホン", "スピーカー",
        "時計", "照明", "ライト", "USB", "モバイルバッテリー",
        "炊飯", "トースター", "ケトル", "コーヒーメーカー",
    ],
    "kitchen": [
        "フライパン", "鍋", "包丁", "まな板", "キッチンツール", "調理器具",
        "食洗機", "水切り", "保存容器", "ラップ",
        "エプロン", "ミトン", "計量", "泡立て", "おたま", "菜箸",
        "キッチン家電", "ミキサー", "ブレンダー", "フードプロセッサー",
        "耐熱ガラス", "レンジ", "パック&レンジ", "iwaki", "容器",
    ],
    "living": [
        "収納", "インテリア", "タオル", "マット", "カーテン", "クッション",
        "食器", "マグカップ", "ボトル", "水筒", "弁当", "ランチ",
        "雑貨", "オーガナイザー", "ラック", "フック", "洗濯", "掃除",
        "アロマ", "ディフューザー", "キャンドル", "ソープ",
        "tower", "山崎実業", "ホルダー", "ディスペンサー", "トレー",
        "マグネット", "ペーパーホルダー",
    ],
    "fashion": [
        "バッグ", "靴", "スニーカー", "サンダル", "ワンピース", "トップス",
        "パンツ", "スカート", "帽子", "ストール", "マフラー", "手袋",
        "アクセサリー", "ピアス", "ネックレス", "リング", "ブレスレット",
        "財布", "ウォレット", "サングラス",
    ],
    "book": [
        "本", "書籍", "文庫", "新書", "漫画", "マンガ", "コミック", "雑誌",
        "レシピ本", "絵本", "図鑑", "ノート", "手帳", "ダイアリー", "読書",
        "自己啓発", "勇気", "教え", "入門", "著", "出版",
        "books.rakuten.co.jp",
    ],
    "pet": [
        "犬", "猫", "ペット", "ドッグ", "キャット", "わんこ", "にゃんこ",
        "ペットベッド", "ドライブ", "キャリー", "フード", "おやつ", "リード",
        "首輪", "トイレ", "ケージ",
    ],
    "kids": [
        "ベビー", "キッズ", "子供", "こども", "赤ちゃん", "おもちゃ",
        "知育", "ランドセル", "入園", "入学", "お弁当箱", "水筒",
        "抱っこ紐", "ベビーカー", "チャイルドシート",
    ],
    "food": [
        "お菓子", "スイーツ", "チョコ", "ケーキ", "コーヒー", "紅茶",
        "お茶", "ジュース", "ワイン", "ビール", "おつまみ", "ナッツ",
        "グルメ", "お取り寄せ", "ギフト", "詰め合わせ",
    ],
}

# ============================================================
# ジャンル別語彙
# ============================================================
GENRE_VOCABULARY = {
    "beauty": {
        "reasons": [
            "持ち運びしやすいサイズだ",
            "コンパクトで軽い",
            "デザインがシンプルだ",
            "カラバリが豊富だ",
            "耐熱素材で安心だ",
            "口コミが良い",
            "使い心地が良さそうだ",
        ],
        "points": [
            "朝の準備が楽になりそうな",
            "旅行にも持っていける",
            "カラーが選べる",
            "コンパクトな",
            "時短できそうな",
        ],
        "scenes": [
            "旅行や出張",
            "毎朝の身支度",
            "お出かけ前の準備",
            "出先でのお直し",
            "プレゼント",
        ],
        "merits": [
            "送料無料なのも嬉しい",
            "カラーが選べるのも良い",
            "この価格でこのクオリティは嬉しい",
            "レビューも高評価で安心",
            "クーポン使えるのもポイント高い",
        ],
        "emojis": ["💄", "✨", "💡", "👜", "🪞", "💐"],
        "tags_pool": [
            "美容", "コスメ", "スキンケア", "身だしなみ", "メイク",
            "美容グッズ", "旅行グッズ", "持ち運び", "おしゃれ",
        ],
    },
    "appliance": {
        "reasons": [
            "立ち上がりが早い",
            "コンパクトで場所を取らない",
            "操作がシンプルだ",
            "デザインがスタイリッシュだ",
            "パワーがしっかりある",
            "音が静かだ",
        ],
        "points": [
            "出しっぱなしでも気にならないデザインな",
            "お手入れしやすそうな",
            "省エネな",
            "コンパクトな",
            "使い勝手良さそうな",
        ],
        "scenes": [
            "忙しい朝",
            "時短したいとき",
            "一人暮らし",
            "毎日の家事",
            "新生活の準備",
        ],
        "merits": [
            "クーポンでさらにお得",
            "この価格はコスパ良い気がする",
            "レビュー評価も高い",
            "送料無料なのが嬉しい",
            "省エネなのも助かる",
        ],
        "emojis": ["⚡", "✨", "👔", "🏠", "💡", "🔌"],
        "tags_pool": [
            "家電", "時短家電", "時短", "便利グッズ", "生活家電",
            "おすすめ家電", "暮らし", "新生活",
        ],
    },
    "kitchen": {
        "reasons": [
            "使いやすそうだ",
            "デザインがおしゃれだ",
            "お手入れしやすそうだ",
            "コンパクトで場所を取らない",
            "機能的だ",
            "軽くて扱いやすそうだ",
        ],
        "points": [
            "毎日の料理が楽しくなりそうな",
            "出しっぱなしでも様になる",
            "食洗機対応な",
            "収納しやすそうな",
            "お手入れが楽そうな",
        ],
        "scenes": [
            "毎日の料理",
            "時短料理",
            "週末のまとめ調理",
            "おうちカフェ",
            "新生活の準備",
        ],
        "merits": [
            "この価格でこの品質はお得",
            "送料無料なのも嬉しい",
            "レビューも高評価で安心",
            "カラー展開があるのも良い",
            "まとめ買いでさらにお得",
        ],
        "emojis": ["🍳", "✨", "🏠", "☕", "💡", "🍽️"],
        "tags_pool": [
            "キッチン", "キッチングッズ", "料理", "時短", "台所",
            "調理器具", "おしゃれキッチン", "暮らし",
        ],
    },
    "living": {
        "reasons": [
            "シンプルなデザインだ",
            "使い勝手が良さそうだ",
            "サイズ感がちょうど良い",
            "素材がしっかりしてそうだ",
            "見た目がおしゃれだ",
            "インテリアに馴染みそうだ",
        ],
        "points": [
            "暮らしに馴染みそうな",
            "どんな部屋にも合いそうな",
            "カラー展開が豊富な",
            "シンプルな",
            "お手入れしやすそうな",
        ],
        "scenes": [
            "毎日の暮らし",
            "キッチン",
            "リビング",
            "一人暮らし",
            "新生活の準備",
        ],
        "merits": [
            "お値段以上な気がする",
            "シンプルでどんな部屋にも合いそう",
            "カラー展開が豊富なのも良い",
            "プレゼントにも良さそう",
            "まとめ買いでお得",
        ],
        "emojis": ["🏠", "✨", "☕", "🍽️", "💡", "🌿"],
        "tags_pool": [
            "暮らし", "生活雑貨", "インテリア", "キッチン雑貨",
            "シンプルライフ", "おしゃれ", "収納", "新生活",
        ],
    },
    "fashion": {
        "reasons": [
            "デザインが可愛い",
            "シンプルで合わせやすそうだ",
            "素材がしっかりしてそうだ",
            "カラバリが豊富だ",
            "軽くて使いやすそうだ",
            "シルエットがきれいだ",
        ],
        "points": [
            "どんなコーデにも合いそうな",
            "シンプルで合わせやすそうな",
            "軽い",
            "カラバリが豊富な",
            "着回ししやすそうな",
        ],
        "scenes": [
            "普段使い",
            "お出かけ",
            "通勤",
            "カジュアルコーデ",
            "季節の変わり目",
        ],
        "merits": [
            "この価格で手に入るのは嬉しい",
            "レビューも良い",
            "送料無料なのもありがたい",
            "ギフトにも良さそう",
            "サイズ交換可能なのも安心",
        ],
        "emojis": ["👗", "👜", "✨", "👒", "💐", "🎀"],
        "tags_pool": [
            "ファッション", "コーデ", "プチプラ", "おしゃれ",
            "大人カジュアル", "シンプルコーデ", "通勤コーデ",
        ],
    },
    "book": {
        "reasons": [
            "読みやすそうだ",
            "話題の1冊だ",
            "レビューで高評価だ",
            "サクッと読めそうだ",
            "気になっていた本だ",
            "内容が面白そうだ",
            "こういうテーマが気になる",
        ],
        "points": [
            "内容が気になる",
            "サクッと読めそうな",
            "読みやすそうな",
            "話題になっている",
            "ためになりそうな",
        ],
        "scenes": [
            "空き時間に少しずつ",
            "寝る前の読書",
            "通勤中",
            "カフェでゆっくり",
            "休日のおうち時間",
            "自分時間",
            "気分転換",
        ],
        "merits": [
            "送料無料で届くのが嬉しい",
            "Kindle版もあるみたい",
            "プレゼントにも良さそう",
            "シリーズで揃えたくなる",
            "ポイント還元もあって嬉しい",
        ],
        "emojis": ["📚", "✨", "💡", "☕", "📖", "🔖"],
        "tags_pool": [
            "読書", "おすすめ本", "本", "読書記録",
            "暮らし", "学び", "自分磨き",
        ],
    },
    "pet": {
        "reasons": [
            "デザインが可愛い",
            "サイズ展開がある",
            "使い勝手が良さそうだ",
            "丈夫そうだ",
            "安全素材で安心だ",
        ],
        "points": [
            "洗えるのが嬉しい",
            "ペットも気に入りそうな",
            "お手入れしやすそうな",
            "丈夫そうな",
        ],
        "scenes": [
            "お出かけ",
            "おうちでのくつろぎタイム",
            "ドライブ",
            "お散歩",
        ],
        "merits": [
            "ペットも気に入りそう",
            "飼い主も嬉しいデザイン",
            "お手入れしやすそう",
            "この価格は嬉しい",
        ],
        "emojis": ["🐶", "🐱", "🐾", "✨", "💕", "🏠"],
        "tags_pool": [
            "ペットグッズ", "犬のいる暮らし", "猫のいる暮らし",
            "ペット用品", "わんこ", "にゃんこ",
        ],
    },
    "kids": {
        "reasons": [
            "デザインが可愛い",
            "子どもが喜びそうだ",
            "安全素材で安心だ",
            "サイズ調整ができる",
            "丈夫そうだ",
        ],
        "points": [
            "丈夫そうな",
            "洗えるのが助かる",
            "名入れできる",
            "お揃いにもできそうな",
        ],
        "scenes": [
            "入園・入学準備",
            "お出かけ",
            "毎日の通園",
            "おうち遊び",
        ],
        "merits": [
            "名入れできるのも嬉しい",
            "洗えるのが助かる",
            "お値段もお手頃",
            "兄弟でお揃いにもできそう",
        ],
        "emojis": ["🎒", "✨", "🌈", "💕", "🧸", "🎀"],
        "tags_pool": [
            "キッズ", "子育て", "入園準備", "入学準備",
            "こどもグッズ", "ママおすすめ",
        ],
    },
    "food": {
        "reasons": [
            "美味しそうだ",
            "パッケージが可愛い",
            "個包装で便利だ",
            "素材にこだわってそうだ",
            "種類が豊富だ",
        ],
        "points": [
            "個包装で便利な",
            "パッケージが可愛い",
            "種類が豊富な",
            "素材にこだわってそうな",
        ],
        "scenes": [
            "おうちでのご褒美",
            "ティータイム",
            "手土産",
            "自分へのご褒美",
        ],
        "merits": [
            "送料無料なのが嬉しい",
            "ギフトにもぴったり",
            "まとめ買いでお得",
            "リピートしたくなりそう",
        ],
        "emojis": ["🍰", "✨", "☕", "🎁", "💕", "🍪"],
        "tags_pool": [
            "グルメ", "お取り寄せ", "スイーツ", "おやつ",
            "ギフト", "おうちカフェ",
        ],
    },
}

DEFAULT_VOCABULARY = {
    "reasons": [
        "使いやすそうだ", "デザインが良い", "シンプルで良さそうだ",
        "コスパが良さそうだ", "しっかりした作りだ",
    ],
    "points": [
        "使いやすそうな", "デザインが良い", "シンプルな",
        "コスパが良さそうな",
    ],
    "scenes": ["普段使い", "毎日の暮らし", "ちょっとした贈り物"],
    "merits": [
        "送料無料なのが嬉しい", "レビューも高評価",
        "この価格はお得感ある",
    ],
    "emojis": ["✨", "💡", "👀", "💕"],
    "tags_pool": ["おすすめ", "暮らし", "買ってよかった", "楽天ROOM"],
}


# ============================================================
# クロージングライン
# ============================================================
CLOSING_LINES = {
    "beauty":    ["こういう美容グッズ好き", "こういうの待ってた", "毎日のケアに取り入れたい",
                  "あると地味に便利そう", "手元にあると安心"],
    "appliance": ["こういう家電好き", "こういうの探してた", "暮らしがラクになりそう",
                  "あると地味に助かりそう", "届いたら使うの楽しみ"],
    "kitchen":   ["こういうキッチングッズ好き", "料理のモチベ上がりそう", "毎日の料理が楽しくなりそう",
                  "こういうのあると助かりそう", "使ってみたくなる"],
    "living":    ["こういう雑貨好き", "こういうの探してた", "暮らしが整いそう",
                  "あると地味に便利そう", "つい保存したくなる"],
    "fashion":   ["こういうの探してた", "シンプルで使いやすそう", "コーデの幅が広がりそう",
                  "使ってみたくなる", "手元にあると便利そう"],
    "book":      ["こういう本、つい手に取りたくなる", "積読になりそうだけど気になる",
                  "休日にゆっくり読みたい", "手元に置いておきたい1冊"],
    "pet":       ["うちの子にも使いたい", "ペットとの暮らしが楽しくなりそう", "こういうの探してた"],
    "kids":      ["こういうの探してた", "子どもが喜びそう", "ママ友にも教えたい"],
    "food":      ["こういうの好き", "リピートしそう", "おうちカフェにぴったり",
                  "届いたら食べるの楽しみ"],
    "general":   ["こういうの探してた", "気になる", "使ってみたくなる",
                  "あると地味に便利そう"],
}


# ============================================================
# 語尾バリエーション辞書 + 重複防止（改善1）
# ============================================================
SCENE_ENDINGS = [
    "にも良さそう！",
    "にもぴったりそう！",
    "にも活躍しそう！",
    "にも取り入れやすそう！",
    "にも使いやすそう！",
    "にも助かりそう！",
    "にも重宝しそう！",
]

# book専用の語尾（「〜に」ではなく「〜の」系が自然）
BOOK_SCENE_ENDINGS = [
    "に読みたくなる",
    "に良さそう。",
    "にちょうど良さそう",
    "にぴったり",
    "に手に取りたくなる",
]

_recent_endings: list[str] = []
_MAX_RECENT_ENDINGS = 3


def _pick_scene_ending(genre: str = "general") -> str:
    """語尾をランダムに選ぶ（直近3件と被らない）"""
    pool = BOOK_SCENE_ENDINGS if genre == "book" else SCENE_ENDINGS
    # 直近3件と被らないものを優先
    available = [e for e in pool if e not in _recent_endings]
    if not available:
        available = pool
    ending = random.choice(available)
    _recent_endings.append(ending)
    if len(_recent_endings) > _MAX_RECENT_ENDINGS:
        _recent_endings.pop(0)
    return ending


# ============================================================
# 季節感（改善4B）
# ============================================================
SEASON_PHRASES = {
    "spring": ["新生活に", "春の準備に", "軽やかに", "整えたいこの時期に"],
    "summer": ["暑い日に", "夏のおうち時間に", "涼しく過ごしたいこの時期に", "持ち歩きにも"],
    "autumn": ["おうち時間に", "秋の夜長に", "あたたかみのある"],
    "winter": ["乾燥対策に", "冬のおうち時間に", "ぬくもりが欲しいこの時期に", "防寒にも"],
}


def _get_season() -> str:
    """現在月から季節を推定"""
    month = datetime.now().month
    if month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    elif month in (9, 10, 11):
        return "autumn"
    else:
        return "winter"


def _get_seasonal_phrase() -> str | None:
    """季節フレーズをランダムに返す（50%の確率でNone）"""
    if random.random() < 0.5:
        return None
    season = _get_season()
    phrases = SEASON_PHRASES.get(season, [])
    return random.choice(phrases) if phrases else None


# ============================================================
# ROOMらしい言い回し（改善4C）
# ============================================================
ROOM_FLAVORS = [
    "myROOMに載せたい",
    "コレ！したくなる",
    "こういうのROOMで見つけると嬉しい",
    "つい保存したくなる",
]


def _get_room_flavor() -> str | None:
    """ROOMらしい一言を返す（30%の確率）"""
    if random.random() < 0.3:
        return random.choice(ROOM_FLAVORS)
    return None


# ============================================================
# pickupトーン（臨時投稿 room plus post 用）
# ============================================================
PICKUP_OPENERS = [
    "今日見つけた",
    "これ気になる",
    "ふと見つけた",
    "偶然見つけた",
    "ROOM巡りで発見",
    "これ良さそう",
    "なんか気になる",
    "見つけちゃった",
]

PICKUP_CLOSINGS = [
    "気になったので載せてみた",
    "とりあえず載せておく！",
    "メモがわりに載せておこう",
    "気になるものリスト入り",
    "あとでじっくり見よう",
    "また見返したいからメモ",
]


def _template_pickup(headline, reason, scene, closing, emoji1, emoji2, tags,
                     genre: str = "general"):
    """TYPE_PICKUP: 臨時投稿用の軽いトーン"""
    opener = random.choice(PICKUP_OPENERS)
    pickup_closing = random.choice(PICKUP_CLOSINGS)
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    lines = [
        f"{opener}！{headline}{emoji1}",
        "",
        f"{conn}、{scene}{ending}",
        f"{pickup_closing}{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


# ============================================================
# book専用テンプレート（改善2）
# ============================================================
BOOK_OPENERS = [
    "この本、気になってた",
    "読みたい1冊",
    "こういう本に弱い…",
    "今の自分に刺さりそうな1冊",
    "ずっと気になってた",
    "気になる1冊",
]

BOOK_BODIES = [
    "空き時間に少しずつ読みたい",
    "自分時間に読みたくなる",
    "気分転換にも良さそう",
    "こういうテーマ気になる",
    "読みやすそうで気になる",
    "サクッと読めそうなのも嬉しい",
]


def _template_book(headline, reason, scene, closing, emoji1, emoji2, tags):
    """book専用テンプレート"""
    opener = random.choice(BOOK_OPENERS)
    body = random.choice(BOOK_BODIES)
    ending = _pick_scene_ending("book")
    lines = [
        f"{headline}、{opener}{emoji1}",
        "",
        f"{body}し、{scene}{ending}",
        f"{closing}{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


# ============================================================
# 5テンプレート（語尾バリエーション対応）
# ============================================================

def _connect_shi(reason: str) -> str:
    """reason を「〜し、」に自然に繋げる。"""
    if reason.endswith(("い", "る")):
        return f"{reason}し"
    if reason.endswith("だ"):
        return f"{reason}し"
    return f"{reason}だし"


def _template_a(headline, reason, scene, closing, emoji1, emoji2, tags,
                genre: str = "general"):
    """TYPE_A: 欲しい系"""
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    lines = [
        f"{headline}、これ欲しい{emoji1}",
        "",
        f"{conn}、{scene}{ending}",
        f"{closing}…！{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


def _template_b(headline, reason, scene, merit, emoji1, emoji2, tags,
                genre: str = "general"):
    """TYPE_B: 便利系"""
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    lines = [
        f"{headline}、これ便利そう{emoji1}",
        "",
        f"{conn}、{scene}{ending}",
        f"{merit}{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


def _template_c(headline, reason, scene, closing, emoji1, emoji2, tags,
                genre: str = "general"):
    """TYPE_C: おしゃれ/好み系"""
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    # bookはbook専用テンプレートに行くのでここには来ないが念のため
    opener = f"{headline}、デザインが好み{emoji1}"
    lines = [
        opener,
        "",
        f"{conn}、{scene}{ending}",
        f"{closing}…！{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


def _template_d(headline, reason, scene, merit, emoji1, emoji2, tags,
                genre: str = "general"):
    """TYPE_D: 共感系"""
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    lines = [
        f"{headline}、ずっと気になってた{emoji1}",
        "",
        f"{conn}、{scene}{ending}",
        f"{merit}{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


def _template_e(headline, reason, scene, merit, emoji1, emoji2, tags,
                genre: str = "general"):
    """TYPE_E: お得系"""
    conn = _connect_shi(reason)
    ending = _pick_scene_ending(genre)
    lines = [
        f"{headline}、コスパ良さそう{emoji1}",
        "",
        f"{conn}、{scene}{ending}",
        f"{merit}{emoji2}",
        "",
    ]
    lines.extend(f"#{t}" for t in tags)
    return "\n".join(lines)


TEMPLATES = [
    (_template_a, "TYPE_A (欲しい)"),
    (_template_b, "TYPE_B (便利)"),
    (_template_c, "TYPE_C (おしゃれ)"),
    (_template_d, "TYPE_D (共感)"),
    (_template_e, "TYPE_E (お得)"),
]

# book専用
BOOK_TEMPLATE = (_template_book, "TYPE_BOOK (読書)")


# ============================================================
# 表現重複防止
# ============================================================
_recent_openings: list[str] = []
_MAX_RECENT = 5


def _is_opening_duplicate(opening: str) -> bool:
    """直近5件と同じ冒頭（最初の10文字）が被るか"""
    key = opening[:10]
    return any(key == o[:10] for o in _recent_openings)


def _record_opening(opening: str):
    """冒頭を記録"""
    global _recent_openings
    _recent_openings.append(opening[:10])
    if len(_recent_openings) > _MAX_RECENT:
        _recent_openings = _recent_openings[-_MAX_RECENT:]


# 前回使ったテンプレートを記録（連続回避）
_last_template_index = -1


# ============================================================
# ジャンル判定
# ============================================================

def detect_genre(title: str, url: str = "", comment: str = "") -> str:
    """商品タイトル・URL・コメントからジャンルを推定する"""
    text = f"{title} {url} {comment}".lower()
    scores = {}
    for genre, keywords in GENRE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > 0:
            scores[genre] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


# ============================================================
# ハッシュタグ生成
# ============================================================

LIFESTYLE_TAGS = [
    "楽天ROOM", "楽天ROOMに載せてます", "おすすめ", "暮らし",
    "買ってよかった", "楽天お買い物マラソン", "楽天スーパーセール",
    "QOL爆上がり", "暮らしを整える",
]


def generate_tags(title: str, genre: str, count: int = 4) -> list[str]:
    """ハッシュタグを4カテゴリで生成する"""
    vocab = GENRE_VOCABULARY.get(genre, DEFAULT_VOCABULARY)
    tags = []

    # 1. 商品名系: カタカナ語を拾う（4文字以上、12文字以内、先にフル抽出してから切る）
    katakana_words_raw = re.findall(r"[ァ-ヶー]{4,12}", title)
    skip_words = {
        "サイズ", "タイプ", "カラー", "セット", "ホワイト", "ブラック",
        "ピンク", "グリーン", "ブルー", "レッド", "グレー", "イエロー",
        "オレンジ", "ベージュ", "シルバー", "ゴールド",
        "ポイント", "ランキング", "レビュー", "メーカー",
        "コーティン", "ストレートヘアア",  # 切断語を除外
    }
    katakana_words = [w for w in katakana_words_raw
                      if w not in skip_words and len(w) <= 8]
    if katakana_words:
        tags.append(random.choice(katakana_words))

    # 2. カテゴリ系
    genre_tags = list(vocab["tags_pool"])
    random.shuffle(genre_tags)
    if genre_tags:
        tags.append(genre_tags[0])

    # 3. 用途系（シーンから）
    scenes = list(vocab.get("scenes", []))
    if scenes:
        tags.append(random.choice(scenes))

    # 4. ライフスタイル系
    lifestyle = list(LIFESTYLE_TAGS)
    random.shuffle(lifestyle)
    if lifestyle:
        tags.append(lifestyle[0])

    # 重複排除
    seen = set()
    unique_tags = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)
    filler = genre_tags[1:] + lifestyle[1:]
    for t in filler:
        if len(unique_tags) >= count:
            break
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags[:count]


# ============================================================
# ヘッドライン生成（改善3: ブランド名+カテゴリ名）
# ============================================================

# ブランド名として認識するワード（タイトル先頭に来がちなもの）
KNOWN_BRANDS = {
    "siroca", "iwaki", "tower", "bruno", "balmuda", "braun", "sharp",
    "panasonic", "sony", "anker", "dyson", "salonia", "thermos",
    "zojirushi", "象印", "タイガー", "山崎実業", "無印良品", "ニトリ",
    "ティファール", "t-fal", "アイリスオーヤマ", "iris", "デロンギ",
    "レコルト", "recolte", "ツインバード", "twinbird",
}

# タイトルからカテゴリ名を抽出するためのマッピング
CATEGORY_KEYWORDS_MAP = {
    "コーヒーメーカー": ["コーヒーメーカー", "コーヒーマシン"],
    "ヘアアイロン": ["ヘアアイロン"],
    "ストレートアイロン": ["ストレートアイロン"],
    "カールアイロン": ["カールアイロン", "カールヘアアイロン"],
    "保存容器": ["保存容器", "コンテナ"],
    "ペーパーホルダー": ["ペーパーホルダー", "キッチンペーパー"],
    "ドライヤー": ["ドライヤー"],
    "掃除機": ["掃除機"],
    "加湿器": ["加湿器"],
    "フライパン": ["フライパン", "フライパンセット"],
    "ケトル": ["ケトル", "電気ケトル"],
    "トースター": ["トースター"],
    "炊飯器": ["炊飯器", "炊飯"],
    "水筒": ["水筒", "ボトル"],
    "バッグ": ["バッグ", "トートバッグ", "リュック"],
    "ピアス": ["ピアス"],
    "ネックレス": ["ネックレス"],
    "収納": ["収納ケース", "収納ボックス"],
    "タオル": ["タオル", "バスタオル"],
}


def _extract_brand(title: str) -> str | None:
    """タイトルからブランド名を抽出する"""
    # 先頭のワードをチェック
    parts = re.split(r"[\s　/／]+", title)
    if parts:
        first = parts[0].lower()
        if first in KNOWN_BRANDS:
            return parts[0]
        # 全角英字も
        for brand in KNOWN_BRANDS:
            if brand.lower() == first:
                return parts[0]
    return None


def _extract_category(title: str) -> str | None:
    """タイトルから商品カテゴリ名を抽出する"""
    for category, keywords in CATEGORY_KEYWORDS_MAP.items():
        for kw in keywords:
            if kw in title:
                return category
    return None


def _make_headline(title: str, genre: str = "general") -> str:
    """商品タイトルから1行目用の短いフレーズを作る

    優先順位:
      1. ブランド名 + カテゴリ名 → 「sirocaのコーヒーメーカー」
      2. ブランド名のみ → ブランド名
      3. タイトルから短縮
    """
    cleaned = re.sub(r"【.*?】", "", title)
    cleaned = re.sub(r"［.*?］", "", cleaned)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = re.sub(
        r"送料無料|ポイント\d+倍|あす楽|即日発送|ランキング\d+位|レビュー.*?件|★.*",
        "", cleaned
    )
    cleaned = cleaned.strip()

    brand = _extract_brand(cleaned)
    category = _extract_category(cleaned)

    # ブランド名 + カテゴリ名
    if brand and category:
        headline = f"{brand}の{category}"
        if len(headline) <= 20:
            return headline

    # bookの場合: 書名だけ抽出（著者・出版社を除く）
    if genre == "book":
        # 「著」「出版」「編」以降を削除
        book_title = re.split(r"\s+(著|出版|編|監修|訳)\s*", cleaned)[0].strip()
        # 著者名（漢字2-4文字 + 空白）を除去
        book_title = re.sub(r"\s+[一-龥ぁ-ん]{2,4}\s*$", "", book_title).strip()
        if len(book_title) > 20:
            book_title = book_title[:20]
        if book_title:
            return book_title

    # タイトルから短縮
    if len(cleaned) > 25:
        parts = re.split(r"[　\s/／]", cleaned)
        shortened = parts[0]
        # ブランド名だけだと短すぎる場合、2ワード目を追加
        if brand and len(parts) > 1 and shortened.lower() == brand.lower():
            candidate = f"{parts[0]} {parts[1]}"
            if len(candidate) <= 25:
                shortened = candidate
        if len(shortened) > 25:
            shortened = shortened[:25]
        return shortened

    return cleaned if cleaned else title[:25]


# ============================================================
# コメント生成
# ============================================================

def _pick_vocab(vocab: dict) -> dict:
    """語彙辞書からランダムに1セット選ぶ"""
    reason = random.choice(vocab["reasons"])
    point = random.choice(vocab["points"])
    scene = random.choice(vocab["scenes"])
    merit = random.choice(vocab["merits"])
    emoji1 = random.choice(vocab["emojis"])
    emoji2_pool = [e for e in vocab["emojis"] if e != emoji1]
    emoji2 = random.choice(emoji2_pool) if emoji2_pool else emoji1
    return {
        "reason": reason, "point": point, "scene": scene,
        "merit": merit, "emoji1": emoji1, "emoji2": emoji2,
    }


def _get_closing(genre: str) -> str:
    """ジャンルに合ったクロージングラインを返す"""
    lines = CLOSING_LINES.get(genre, CLOSING_LINES["general"])
    return random.choice(lines)


def _build_comment(template_fn, template_name, headline, vocab, genre, tags, closing):
    """テンプレートに応じてコメントを生成する"""
    v = _pick_vocab(vocab)

    # bookジャンルで汎用テンプレートが当たった場合 → book専用に差し替え
    if genre == "book" and template_fn not in (_template_book,):
        # B(便利), E(お得) はbookに不適切 → book専用に差し替え
        if template_fn in (_template_b, _template_e, _template_c):
            comment = _template_book(headline, v["reason"], v["scene"], closing,
                                     v["emoji1"], v["emoji2"], tags)
            return comment, "TYPE_BOOK (読書)"

    # book専用テンプレート
    if template_fn == _template_book:
        comment = template_fn(headline, v["reason"], v["scene"], closing,
                              v["emoji1"], v["emoji2"], tags)
        return comment, template_name

    # pickupテンプレート
    if template_fn == _template_pickup:
        comment = template_fn(headline, v["reason"], v["scene"], closing,
                              v["emoji1"], v["emoji2"], tags, genre=genre)
        return comment, template_name

    # 通常テンプレート（クロージング使用型 vs メリット使用型）
    if template_fn in (_template_a, _template_c):
        comment = template_fn(headline, v["reason"], v["scene"], closing,
                              v["emoji1"], v["emoji2"], tags, genre=genre)
    else:
        comment = template_fn(headline, v["reason"], v["scene"], v["merit"],
                              v["emoji1"], v["emoji2"], tags, genre=genre)
    return comment, template_name


def generate_comment(title: str, url: str = "", genre: str = None,
                     tone: str = "normal") -> str:
    """1つのコメントをランダムに生成する（重複回避付き）

    Args:
        title: 商品タイトル
        url: 商品URL
        genre: ジャンル（Noneで自動判定）
        tone: "normal" | "pickup"（臨時投稿用の軽いトーン）
    """
    global _last_template_index

    if genre is None:
        genre = detect_genre(title, url)

    vocab = GENRE_VOCABULARY.get(genre, DEFAULT_VOCABULARY)
    headline = _make_headline(title, genre)
    tags = generate_tags(title, genre, count=4)
    closing = _get_closing(genre)

    # テンプレート選択
    if tone == "pickup":
        # pickupトーン: pickupテンプレートを高確率で使用
        available = [
            (_template_pickup, "TYPE_PICKUP (発見)"),
            (_template_pickup, "TYPE_PICKUP (発見)"),  # 2倍の確率
            (TEMPLATES[0][0], TEMPLATES[0][1]),  # TYPE_A
            (TEMPLATES[3][0], TEMPLATES[3][1]),  # TYPE_D
        ]
    elif genre == "book":
        # bookは専用テンプレート + A(欲しい), D(共感) のみ許可
        available = [
            (BOOK_TEMPLATE[0], BOOK_TEMPLATE[1]),
            (TEMPLATES[0][0], TEMPLATES[0][1]),  # TYPE_A
            (TEMPLATES[3][0], TEMPLATES[3][1]),  # TYPE_D
        ]
    else:
        available = list(TEMPLATES)

    indices = list(range(len(available)))
    if _last_template_index in indices and len(indices) > 1:
        indices.remove(_last_template_index)

    # 最大3回試行して冒頭重複を回避
    for _attempt in range(3):
        idx = random.choice(indices)
        template_fn, template_name = available[idx]

        comment, actual_name = _build_comment(
            template_fn, template_name, headline, vocab, genre, tags, closing
        )

        first_line = comment.split("\n")[0]
        if not _is_opening_duplicate(first_line):
            break

    _last_template_index = idx
    _record_opening(first_line)
    return comment


def generate_comment_candidates(title: str, url: str = "", genre: str = None,
                                 count: int = 3) -> list[dict]:
    """テンプレートごとにコメント候補を生成して返す"""
    if genre is None:
        genre = detect_genre(title, url)

    vocab = GENRE_VOCABULARY.get(genre, DEFAULT_VOCABULARY)
    headline = _make_headline(title, genre)
    candidates = []

    if genre == "book":
        available = [
            BOOK_TEMPLATE,
            TEMPLATES[0],  # TYPE_A
            TEMPLATES[3],  # TYPE_D
        ]
    else:
        available = list(TEMPLATES)
        random.shuffle(available)

    for template_fn, template_name in available[:count]:
        tags = generate_tags(title, genre, count=4)
        closing = _get_closing(genre)

        comment, actual_name = _build_comment(
            template_fn, template_name, headline, vocab, genre, tags, closing
        )

        candidates.append({
            "pattern": actual_name,
            "comment": comment,
            "genre": genre,
            "tags": tags,
            "headline": headline,
            "char_count": len(comment.replace("\n", "")),
        })

    return candidates


# ============================================================
# 投稿重複防止
# ============================================================

class DuplicateChecker:
    """過去投稿URLと24時間以内のカテゴリを管理する"""

    def __init__(self, history_path: str | Path):
        self.history_path = Path(history_path)
        self._history: list[dict] = []
        self._load()

    def _load(self):
        if self.history_path.exists():
            try:
                self._history = json.loads(
                    self.history_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, Exception):
                self._history = []

    def _save(self):
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            json.dumps(self._history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_duplicate_url(self, url: str) -> bool:
        return any(h.get("url") == url for h in self._history)

    def is_genre_over_limit(self, genre: str, limit: int = 3) -> bool:
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recent = [
            h for h in self._history
            if h.get("genre") == genre and h.get("posted_at", "") >= cutoff
        ]
        return len(recent) >= limit

    def get_recent_openings(self, count: int = 5) -> list[str]:
        recent = sorted(
            self._history,
            key=lambda h: h.get("posted_at", ""),
            reverse=True,
        )[:count]
        return [h.get("opening", "") for h in recent if h.get("opening")]

    def record(self, url: str, genre: str, title: str = "",
               comment: str = "", post_type: str = "",
               score: int = 0, tags: list[str] = None):
        """投稿記録を追加（強化版）"""
        opening = comment.split("\n")[0] if comment else ""
        self._history.append({
            "url": url,
            "genre": genre,
            "title": title,
            "opening": opening,
            "comment": comment[:200],
            "post_type": post_type,
            "score": score,
            "tags": tags or [],
            "posted_at": datetime.now().isoformat(),
        })
        self._save()

    def check(self, url: str, genre: str) -> str | None:
        if self.is_duplicate_url(url):
            return "同じURLの投稿が既にあります"
        if self.is_genre_over_limit(genre):
            return f"24時間以内に同ジャンル({genre})の投稿が3件以上あります"
        return None
