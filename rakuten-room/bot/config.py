"""ROOM BOT v5.0 - 完全自動運用仕様"""

import json
import os
import random
from datetime import datetime
from pathlib import Path

# .env があれば読み込む（python-dotenv不要のシンプル実装）
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
SCREENSHOT_DIR = DATA_DIR / "screenshots"

# 楽天API
RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "")
RAKUTEN_ACCESS_KEY = os.environ.get("RAKUTEN_ACCESS_KEY", "")

# Slack Webhook
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# 会社フォルダルート
COMPANY_ROOT = PROJECT_ROOT.parent.parent
POST_LOG_PATH = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "POST_LOG.json"
FOLLOW_LOG_PATH = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "FOLLOW_LOG.json"
DAILY_LOG_DIR = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "daily"

# 月間スケジュールファイルパス（daily_schedule.py から参照）
# monthly_planner.py と同じパスに統一（DATA_DIR / "monthly_schedule.json"）
MONTHLY_SCHEDULE_PATH = DATA_DIR / "monthly_schedule.json"

# Playwright設定
BROWSER_HEADLESS = False
BROWSER_SLOW_MO = 100
CHROME_EXECUTABLE_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Persistent context用のユーザーデータ保存先（通常Chromeからcookieをコピーして使用）
# 2026-05-05 Phase A-2: Chrome profile を機能別に分離
#   POST / LIKE / FOLLOWBACK が同一 profile を共有していた結果、Playwright が
#   "Target page, context or browser has been closed" 衝突を起こしていた
#   （実例: 2026-03-25 scheduler.log で 52082件の同種エラー、
#    2026-05-05 09:00 Post Batch1 が runner_fail rc=1 で失敗）
# 解決: 機能別 profile + 同一 cookie を3つに複製で並列実行可能化
CHROME_USER_DATA_DIR_POST       = DATA_DIR / "chrome_profile_post"
CHROME_USER_DATA_DIR_LIKE       = DATA_DIR / "chrome_profile_like"
CHROME_USER_DATA_DIR_FOLLOWBACK = DATA_DIR / "chrome_profile_followback"
CHROME_USER_DATA_DIR_FOLLOW     = DATA_DIR / "chrome_profile_follow"  # HOST follow_executor 用 fallback

# action 名 → profile path のマッピング（BrowserManager で使用）
CHROME_PROFILE_BY_ACTION = {
    "post":       CHROME_USER_DATA_DIR_POST,
    "like":       CHROME_USER_DATA_DIR_LIKE,
    "followback": CHROME_USER_DATA_DIR_FOLLOWBACK,
    "follow":     CHROME_USER_DATA_DIR_FOLLOW,
}

# Backward compat alias - 既存コード（diagnose系・debug系）が直接参照しているため残す
# 新規コードでは CHROME_PROFILE_BY_ACTION[action] を使うこと
CHROME_USER_DATA_DIR = CHROME_USER_DATA_DIR_POST


def get_chrome_profile(action: str = "post") -> Path:
    """機能別 Chrome profile path を返す。

    2026-05-05 Phase A-2 migration safety:
        新規 profile (chrome_profile_post/like/followback/follow) が作られるまでの
        移行期間中、profile dir が無ければ legacy chrome_profile を fallback として返す。
        Bookmarks / Cookies の存在で「真の profile」かを判定する。

    Args:
        action: "post", "like", "followback", "follow" のいずれか
    Returns:
        対応する Chrome profile の Path
    """
    target = CHROME_PROFILE_BY_ACTION.get(action, CHROME_USER_DATA_DIR_POST)
    # Migration safety: 新 profile が未作成なら legacy chrome_profile にfallback
    # 「真の profile」判定: Bookmarks / Cookies / Network/Cookies (modern Chrome) のいずれかが存在
    legacy = DATA_DIR / "chrome_profile"
    has_real_profile = (
        (target / "Default" / "Bookmarks").exists() or
        (target / "Default" / "Cookies").exists() or
        (target / "Default" / "Network" / "Cookies").exists()
    )
    legacy_has_profile = (
        legacy.exists() and (
            (legacy / "Default" / "Bookmarks").exists() or
            (legacy / "Default" / "Network" / "Cookies").exists()
        )
    )
    if not has_real_profile and legacy_has_profile:
        # Migration 未完了: legacy profile を使う（fallback）
        return legacy
    return target

# セッション
SESSION_STATE_PATH = DATA_DIR / "state" / "storage_state.json"

# 楽天ROOM
ROOM_BASE_URL = "https://room.rakuten.co.jp"
RAKUTEN_BASE_URL = "https://www.rakuten.co.jp"
RECOMMEND_USERS_URL = "https://room.rakuten.co.jp/discover/recommendUsers"
FOLLOWERS_URL = "https://room.rakuten.co.jp/discover/followers"  # フォロワー返し用（2026-04-02 追加）

# アカウント
ACCOUNT_NAME = "カピバラ癒し"
ROOM_ID = "room_e05d4d1c1e"

# タイムアウト（ms）
PAGE_LOAD_TIMEOUT = 30000
ELEMENT_TIMEOUT = 10000
POST_SUBMIT_TIMEOUT = 15000

# 人間的な遅延（秒）
HUMAN_DELAY_MIN = 1.0
HUMAN_DELAY_MAX = 3.0
TYPE_DELAY_MIN = 0.03
TYPE_DELAY_MAX = 0.12

# ============================================================
# 投稿BOT設定
# ============================================================

# 日次投稿数（BOT対策: 日によって変動させる）
# 2026-04-24 Marketing 08:42 指示: POST cap=100/day 厳格適用
# 2026-04-25 Marketing 指示: 暫定cap200へ変更（楽天実上限観測のため）
POST_DAILY_MIN = 90
POST_DAILY_MAX = 200
# ハード上限: queue_executor が daily_cap_reached で停止する絶対値
# 旧: cap100 (〜2026-04-25 13:10) / 新: cap200 (2026-04-25 13:10〜)
POST_DAILY_CAP = 200

# バッチ分割
POST_BATCH_1_COUNT = 50              # バッチ1: 固定50件
# バッチ2: 残り（日次合計 - 50件 = 40〜50件）

# バッチ開始時刻（範囲内でランダムに決定。固定にしない＝BOT対策）
POST_BATCH_1_START_HOUR = 0          # バッチ1: 0:00〜0:30の間でランダム開始
POST_BATCH_1_START_MINUTE_MAX = 30   # バッチ1の開始分の上限
POST_BATCH_2_START_HOUR_MIN = 7      # バッチ2: 7:00〜12:00の間でランダム開始
POST_BATCH_2_START_HOUR_MAX = 12     # バッチ2の開始時の上限

# 投稿導線の比率（合計100にする）
POST_ROUTE_RATIO = {
    "direct": 70,       # 商品ページから直接投稿
    "influencer": 30,   # インフルエンサー投稿から派生
}

# 投稿間隔（ランダム化。固定間隔にしない）
POST_INTERVAL_MIN = 8                # 最小間隔（秒）
POST_INTERVAL_MAX = 15               # 最大間隔（秒）

# 制限
POST_MAX_SAME_GENRE = 3              # 同ジャンル連続投稿の上限

# バッチ設定（daily_schedule.py から参照）
DAILY_JITTER_MIN = 0    # 日次ジッター最小（分）
DAILY_JITTER_MAX = 30   # 日次ジッター最大（分）
POST_BATCHES = {
    "night": {
        "start_hour": POST_BATCH_1_START_HOUR,
        "start_minute_min": 35,                   # 00:35〜 (フォロー00:00とずらす)
        "start_minute_max": 50,
        "count_min": POST_BATCH_1_COUNT,
        "count_max": POST_BATCH_1_COUNT,
        "interval_min": POST_INTERVAL_MIN,
        "interval_max": POST_INTERVAL_MAX,
    },
    "lunch": {
        "start_hour": 12,
        "start_minute_min": 35,                   # 12:35〜 (フォロー12:00とずらす)
        "start_minute_max": 50,
        "count_min": 20,
        "count_max": 30,
        "interval_min": POST_INTERVAL_MIN,
        "interval_max": POST_INTERVAL_MAX,
    },
    "evening": {
        "start_hour": 19,
        "start_minute_min": 35,                   # 19:35〜 (フォロー19:00とずらす)
        "start_minute_max": 50,
        "count_min": None,  # 残り全件
        "count_max": None,
        "interval_min": POST_INTERVAL_MIN,
        "interval_max": POST_INTERVAL_MAX,
    },
}

# ============================================================
# フォローBOT設定
# ============================================================

# 日次フォロー数（BOT対策: 日によって変動させる）
FOLLOW_DAILY_MIN = 450
FOLLOW_DAILY_MAX = 500

# ============================================================
# いいねBOT設定
# ============================================================

# 日次いいね数
# 2026-04-09 Phase 1-4 修正: 実運用レンジ (350-450/日) に config を合わせる
# 旧値 150/250 は monthly_schedule.json (504/日) に上書きされていたが、整合性のため正規化
LIKE_DAILY_MIN = 450
LIKE_DAILY_MAX = 500

# フォローバック 日次目標（CEO指示 2026-05-01: 固定30件）
FOLLOWBACK_DAILY_TARGET = 30

# いいね履歴ファイル
LIKE_HISTORY_PATH = DATA_DIR / "like_history.json"

# いいねするフィードURL（/items がメイン。いいねボタン実在確認済み 2026-04-02）
LIKE_FEED_URLS = [
    "https://room.rakuten.co.jp/items",
    "https://room.rakuten.co.jp/timeline/followings",
]

# 連続失敗で停止する閾値
LIKE_MAX_CONSECUTIVE_FAILURES = 5

# セッション分割
FOLLOW_SESSION_MAX = 50              # 1セッション = 最大50件
FOLLOW_SESSIONS_PER_DAY = 4          # 1日4セッション

# フォロー間隔（ランダム化。固定間隔にしない）
FOLLOW_INTERVAL_MIN = 0.3            # 最小間隔（秒・1分1秒逃さず原則・5/8 短縮）
FOLLOW_INTERVAL_MAX = 0.8            # 最大間隔（秒・5/8 短縮）

# セッション内休憩（一定件数ごとにランダム休憩を入れる）
FOLLOW_REST_EVERY_MIN = 10           # 休憩を入れる間隔の最小値（件）
FOLLOW_REST_EVERY_MAX = 15           # 休憩を入れる間隔の最大値（件）
FOLLOW_REST_DURATION_MIN = 10        # 休憩時間の最小値（秒）
FOLLOW_REST_DURATION_MAX = 30        # 休憩時間の最大値（秒）

# セッション間休憩 (2026-05-06 CEO「1分1秒逃さず」指示で撤廃。0秒に変更)
# rate_limit 検知時の自動 cooldown (FOLLOW_RL_COOLDOWN_MIN=69) のみ残す
FOLLOW_SESSION_REST_MIN = 0          # セッション間休憩なし
FOLLOW_SESSION_REST_MAX = 0          # セッション間休憩なし

# ============================================================
# ヘルパー関数
# ============================================================

def get_daily_post_target():
    """今日の投稿目標件数をランダムに決定する（90〜100）"""
    return random.randint(POST_DAILY_MIN, POST_DAILY_MAX)

def get_day_type_targets(day_type: str) -> dict:
    """day_type に応じた目標件数を返す（daily_schedule.py から参照）"""
    if day_type == "off":
        return {"post": 0, "like": 0, "follow": 0}
    elif day_type == "light":
        return {
            "post": random.randint(POST_DAILY_MIN // 2, POST_DAILY_MAX // 2),
            "like": random.randint(FOLLOW_DAILY_MIN // 2, FOLLOW_DAILY_MAX // 2),
            "follow": 0,
        }
    else:  # normal
        return {
            "post": random.randint(POST_DAILY_MIN, POST_DAILY_MAX),
            "like": random.randint(FOLLOW_DAILY_MIN, FOLLOW_DAILY_MAX),
            "follow": 0,
        }

def get_daily_follow_target():
    """今日のフォロー目標件数をランダムに決定する（150〜250）"""
    return random.randint(FOLLOW_DAILY_MIN, FOLLOW_DAILY_MAX)

def get_daily_like_target():
    """今日のいいね目標件数をランダムに決定する（150〜250）"""
    return random.randint(LIKE_DAILY_MIN, LIKE_DAILY_MAX)

def get_batch1_start_minute():
    """バッチ1の開始分をランダムに決定する（0〜30分）"""
    return random.randint(0, POST_BATCH_1_START_MINUTE_MAX)

def get_batch2_start_hour():
    """バッチ2の開始時をランダムに決定する（7〜12時）"""
    return random.randint(POST_BATCH_2_START_HOUR_MIN, POST_BATCH_2_START_HOUR_MAX)

def get_post_route():
    """投稿導線をランダムに選択する（比率に基づく）"""
    r = random.randint(1, 100)
    if r <= POST_ROUTE_RATIO["direct"]:
        return "direct"
    return "influencer"

def get_random_post_interval():
    """投稿間隔をランダムに決定する（秒）"""
    return random.uniform(POST_INTERVAL_MIN, POST_INTERVAL_MAX)

def get_random_follow_interval():
    """フォロー間隔をランダムに決定する（秒）"""
    return random.uniform(FOLLOW_INTERVAL_MIN, FOLLOW_INTERVAL_MAX)

def get_follow_rest_interval():
    """フォロー中の休憩を入れる件数をランダムに決定する"""
    return random.randint(FOLLOW_REST_EVERY_MIN, FOLLOW_REST_EVERY_MAX)

def get_follow_rest_duration():
    """フォロー中の休憩時間をランダムに決定する（秒）"""
    return random.uniform(FOLLOW_REST_DURATION_MIN, FOLLOW_REST_DURATION_MAX)

def get_session_rest_duration():
    """セッション間休憩時間をランダムに決定する（秒）"""
    return random.uniform(FOLLOW_SESSION_REST_MIN, FOLLOW_SESSION_REST_MAX)

# いいね間隔設定
LIKE_INTERVAL_MIN = 2.0
LIKE_INTERVAL_MAX = 6.0
LIKE_REST_EVERY_MIN = 15
LIKE_REST_EVERY_MAX = 25
LIKE_REST_DURATION_MIN = 10.0
LIKE_REST_DURATION_MAX = 30.0

def get_like_interval():
    """いいね間隔をランダムに決定する（秒）"""
    return random.uniform(LIKE_INTERVAL_MIN, LIKE_INTERVAL_MAX)

def get_like_rest_interval():
    """いいね中の休憩を入れる件数をランダムに決定する"""
    return random.randint(LIKE_REST_EVERY_MIN, LIKE_REST_EVERY_MAX)

def get_like_rest_duration():
    """いいね中の休憩時間をランダムに決定する（秒）"""
    return random.uniform(LIKE_REST_DURATION_MIN, LIKE_REST_DURATION_MAX)

# ============================================================
# 段階テスト設定（テスト時はこちらの値で上書きする）
# ============================================================
# Test A: POST_DAILY_MIN=20, POST_DAILY_MAX=20, FOLLOW_DAILY_MIN=50, FOLLOW_DAILY_MAX=50
# Test B: POST_DAILY_MIN=50, POST_DAILY_MAX=50, FOLLOW_DAILY_MIN=100, FOLLOW_DAILY_MAX=100
# Test C: POST_DAILY_MIN=90, POST_DAILY_MAX=100, FOLLOW_DAILY_MIN=150, FOLLOW_DAILY_MAX=250

# ============================================================
# 商品プール設定
# ============================================================

# 初期は高品質重視で小規模運用（安定後に拡張）
POOL_MIN = 700                    # 最低維持件数（700件下回ったら補充）
POOL_MAX = 1000                   # 最大件数（超過時はscore低い順に削除）
POOL_REPLENISH_BUFFER = 50        # 補充時のバッファ

# 監査設定
AUDIT_DIR = DATA_DIR / "audit"
AUDIT_RESULTS_PATH = AUDIT_DIR / "audit_results.json"

# レポート
REPORT_DIR = DATA_DIR / "reports"

# 運用モード
OPERATION_MODE_FILE = DATA_DIR / "operation_mode.json"

# ============================================================
# パトロール / 監視設定 (Phase 2 追加)
# ============================================================

# 裏パトロール (background_patrol.py) の実行間隔（分）
BACKGROUND_PATROL_INTERVAL_MIN = 60

# 表パトロール (auto_monitor.py) の実施時刻
PATROL_TIMES = ['09:00', '15:00', '21:00']

# 補填キュー（remediation_queue.json）処理閾値
REMEDIATION_POST_GAP_THRESHOLD = 5      # post 遅れがこの件数を超えたら補填
REMEDIATION_FOLLOW_GAP_THRESHOLD = 10   # follow 遅れがこの件数を超えたら補填
REMEDIATION_LIKE_GAP_THRESHOLD = 10     # like 遅れがこの件数を超えたら補填

# 投稿停滞アラート（連続停止検知）
POST_STALL_ALERT_HOURS = 2              # posted=0 がこの時間続いたら CRITICAL
PATROL_HALFWAY_HOUR = 15                # この時刻に達成率<50% なら自動再実行
PATROL_HALFWAY_RATIO = 0.5

# ============================================================
# アカウント ペルソナ設定 (CEO 2026-05-16 指示)
# ============================================================
# 「31歳・0歳の新米ママ。その子が発信するような投稿を心掛けてください」
# → 介護/シニア/医療業務用 等は ペルソナ不一致で reject
# → ベビー/ママ向け/若い女性 向け は priority boost
#
# 過去事例 (5/10 posted): 介護エプロン → アカウント信頼性低下リスク
#
# 運用:
#   item_auditor.py で audit_persona() check で NG = fail
#   product_fetcher.py で BOOST genre を優先取得

PERSONA = {
    "age": 31,
    "gender": "female",
    "life_stage": "new_mom",         # 0歳児の新米ママ
    "child_age_months": 0,
    "interests": ["ベビー用品", "育児", "コスメ", "ファッション", "キッチン", "インテリア", "スイーツ"],
}

# タイトル/コメントに含まれる場合 fail (ペルソナ不一致)
PERSONA_NG_KEYWORDS = [
    # 介護・シニア系 (CEO 5/16 確定)
    "介護", "シニア", "高齢者", "老人", "高齢用", "介護用",
    "介助", "認知症", "床ずれ", "誤嚥",
    # 医療業務用 (CEO 5/16 確定)
    "業務用", "プロ用", "病院専用", "施設用", "業者向け",
    # 男性向け専用 (CEO 5/16 確定)
    "メンズ専用", "紳士", "おじさん",
    # 介護関連商品名
    "杖", "歩行器", "ポータブルトイレ", "大人用おむつ", "大人 おむつ",
    "車椅子", "ステッキ", "シルバーカー",
    # 喪服・葬儀 (CEO 5/16 chosen: フォーマル/喪服 OFF)
    "喪服", "葬儀", "仏壇", "数珠", "ブラックフォーマル", "礼服",
    # 男性専用 ホビー/趣味 (CEO 5/16 確定)
    "釣り具", "ゴルフクラブ", "電動工具", "インパクトドライバー",
    "プラモデル", "ガンプラ", "トレカ", "トレーディングカード", "ミニ四駆",
    "フィギュア", "プロレス", "野球グッズ", "麻雀",
    # システム複雑 (CEO 5/16 5000円超 + 誠実性 NG)
    "ふるさと納税", "返礼品",
    # 雑誌付録 (CEO 5/16 チェックなし = NG)
    "雑誌付録", "ムック本付録",
    # ペット用品 (CEO 5/16 完全 NG 確定)
    # Codex 12回目 #2: 「リード」「ハムスター」「ウサギ」単独だと誤爆 (リードディフューザー/ウサギ柄子供服等)
    # → ペット文脈に限定された具体的語彙のみ
    "犬用", "猫用", "ペット用", "ペット用品",
    "ドッグフード", "キャットフード", "ペットフード", "ペットシーツ",
    "猫砂", "ペットケージ", "犬の首輪", "ペットリード", "犬用リード", "猫用首輪",
    "ハムスター用", "うさぎ用", "ハリネズミ用",
    # 車・バイク用品 (チャイルドシート除く) (CEO 5/16 確定)
    "車載", "ドライブレコーダー", "シートカバー", "サンシェード",
    "タイヤ", "車内収納", "車用芳香剤", "スマホホルダー 車",
    "原付", "スクーター", "バイクヘルメット", "バイク用",
    # 大型家具 (キッチン家電は OK・家具のみ NG) (CEO 5/16 確定)
    "ダイニングテーブル", "ダイニングセット", "ベッドフレーム", "システムベッド",
    "3人掛けソファ", "ソファセット", "テレビ台", "テレビボード",
    "本棚 大型", "学習机",
]

# タイトル/コメントに含まれる場合 boost (ペルソナ一致・優先) (CEO 5/16 拡張)
PERSONA_BOOST_KEYWORDS = [
    # ベビー・子供 (0-6歳 OK, CEO 5/16 拡張・Codex 12回目 #3「6歳」追加)
    "ベビー", "赤ちゃん", "新生児", "0歳", "1歳", "2歳", "3歳", "4歳", "5歳", "6歳",
    "幼児", "乳児", "キッズ", "入園", "入学",
    # ベビー寝具 (CEO 5/16 追加)
    "ベビーベッド", "おくるみ", "スリーピングサック", "ベビー布団",
    # ママ自分
    "ママ", "新米ママ", "プレママ", "育児", "授乳", "離乳食",
    "マタニティ", "授乳ブラ", "授乳服",
    # Codex 12回目 #4 typo 修正: 「マスケア」削除 (意図不明 → ママケア が正)
    "産後ケア", "骨盤ベルト", "腹帯", "産後", "ママケア", "バストケア",
    # ベビー必需
    "おむつ", "おしりふき", "抱っこ紐", "ベビーカー", "ベビー服",
    "ベビー食器", "離乳食皿", "フィーディング", "チャイルドシート",
    # 知育・絵本
    "知育玩具", "知育", "絵本", "木のおもちゃ",
    # コスメ・自分時間
    "コスメ", "スキンケア", "ヘアケア", "美容液", "リップケア",
    "プチプラ", "韓国コスメ", "韓国",
    # ファッション
    "ワンピース", "カーディガン", "ナイトウェア", "ルームウェア",
    # キッチン (時短)
    "食洗機対応", "時短", "ワンオペ", "便利グッズ",
    # インテリア・雑貨
    "おしゃれ", "かわいい", "北欧", "ナチュラル", "木製",
    # Codex 12回目 #4 typo 修正: 「うだちライト」→ 「間接照明」
    "ペンダントライト", "間接照明",
    # マザーズバッグ・アクセサリー (CEO 5/16 追加)
    "マザーズバッグ", "リュック", "トートバッグ",
    "ピアス", "ネックレス", "アクセサリー",
    # 軽運動・ジム (CEO 5/16 OK)
    "ヨガマット", "ダンベル", "ストレッチポール", "ランニングシューズ",
    # ガジェット (ママも使う) (CEO 5/16 OK)
    "スマホケース", "iPhoneケース", "ワイヤレス充電",
    # 食品 (CEO 5/16 「手軽に買いやすい」全面推奨)
    "お取り寄せ", "スイーツ", "無添加", "ベビー食材",
    # 「セット」は広すぎて誤爆 (例:鉛筆 12本セット) → 削除. 「詰め合わせ」のみ保持
    "お土産", "銘菓", "ご当地", "名産", "詰め合わせ",
    "福袋", "訳あり", "お試し", "アウトレット",
    "コンビニ限定",
    # 美容 シートマスク 系 (CEO 5/16 OK)
    "シートマスク", "フェイスマスク", "シートパック", "マスクパック",
    # ヒーリング系 (CEO 5/16 アロマディフューザー ✓ - 具体カテゴリのみ)
    "アロマディフューザー", "アロマキャンドル",
    # Codex 13回目 #1 反映:
    # 汎用 promo 語 (プレミアム/高級/贅沢/厳選/送料無料/ポイント10倍/ポイント5倍/限定品/メール便/サンプル/リラックス)
    # は boost 過剰発火で 5000円ライン緩和 → CEO 「手軽に買いやすい」意図と乖離 → 全部削除.
    # 「プレミアム醤油」等の高め食品は「醤油」等 具体ジャンル keyword でカバーすべき.
]

# ============================================================
# 価格 cap (CEO 5/16: 「手軽に買いやすい」5000円 line)
# ============================================================
# 5000円超 でも persona boost 一致 (ベビー必需 抱っこ紐 等) は許容上限まで OK
# 15000円超 はどの item でも fail (大型家具/家電/高級ブランド)
PERSONA_PRICE_CAP_NORMAL = 5000           # 通常品 上限
PERSONA_PRICE_CAP_BOOSTED = 15000         # boost 一致品 上限 (ベビーカー/抱っこ紐 等)
PERSONA_PRICE_CAP_HARD = 30000            # どんな商品でも超えたら fail (大型家電除外)
# 例: 抱っこ紐 12000円 (boost & < 15000) → OK
#     シャインマスカット 5000円 (神経 & =5000) → 通常 OK
#     高級フルーツ 8000円 (神経 & > 5000) → fail
#     ロボット掃除機 40000円 → fail

# NG check 例外 keyword (Codex 12回目 #5: チャイルドシート例外 logic)
# これらが含まれる item は NG check を skip して boost 優先扱い
PERSONA_NG_OVERRIDE_KEYWORDS = [
    "チャイルドシート",  # 車関連 NG だが boost 優先
]

# product_fetcher で 優先的に取得する genre (PERSONA に合致)
PERSONA_PREFERRED_GENRES = ["baby", "kids", "beauty", "kitchen", "fashion",
                             "living", "interior", "food", "stationery", "seasonal"]
# product_fetcher で 取得頻度を下げる genre
# CEO 5/16: pet 完全 NG, car は genre 内 child seat 除外で deprioritize
PERSONA_DEPRIORITIZED_GENRES = ["car", "outdoor", "pet", "garden"]
# CEO 5/16: sports は産後ケア/軽運動/ジム まで boost なので preferred 寄り (除外)


# ============================================================
# ジャンル別検索キーワード（楽天API商品取得用）
# ============================================================

GENRE_SEARCH_KEYWORDS = {
    "kitchen":   ["フライパン セット", "保存容器 耐熱", "水切りラック", "キッチンツール セット", "包丁 ステンレス",
                  "まな板 抗菌", "鍋 IH対応", "計量カップ", "弁当箱 保温", "エプロン おしゃれ"],
    "beauty":    ["スキンケア セット", "ヘアオイル 人気", "日焼け止め", "コスメ ポーチ", "ハンドクリーム",
                  "リップバーム", "洗顔 泡", "化粧水 保湿", "アイシャドウ パレット", "ボディクリーム"],
    "living":    ["今治タオル セット", "収納ボックス おしゃれ", "マグカップ 北欧", "クッション カバー", "ルームウェア",
                  "アロマ ディフューザー", "スリッパ 洗える", "時計 壁掛け", "ブランケット 夏", "除湿剤"],
    "fashion":   ["バッグ レディース 軽量", "スニーカー 白", "ストール UV", "帽子 レディース", "ワンピース 春",
                  "サンダル レディース", "Tシャツ メンズ", "財布 コンパクト", "日傘 折りたたみ", "ベルト 本革"],
    "appliance": ["モバイルバッテリー 軽量", "ワイヤレスイヤホン", "加湿器 卓上", "LEDライト デスク", "充電器 USB",
                  "扇風機 首掛け", "電動歯ブラシ", "体重計 スマホ連携", "ドライヤー 速乾", "延長コード タワー"],
    "food":      ["コーヒー ドリップ", "ナッツ 素焼き", "お菓子 詰め合わせ", "紅茶 ギフト", "スイーツ お取り寄せ",
                  "プロテイン おすすめ", "グラノーラ", "はちみつ 国産", "ドライフルーツ", "お茶 ティーバッグ"],
    "kids":      ["知育玩具", "水筒 キッズ", "お弁当箱 子供", "入学準備", "絵本 人気",
                  "レゴ ブロック", "色鉛筆 セット", "リュック 子供", "プール バッグ", "夏休み 工作"],
    "book":      ["自己啓発 ベストセラー", "レシピ本", "ビジネス書 ランキング", "絵本 セット", "参考書",
                  "小説 2026", "マンガ 全巻", "英語 学習", "資格 テキスト", "図鑑 子供"],
    "pet":       ["ペットベッド", "猫 おもちゃ", "犬 おやつ 無添加", "ペット 食器", "猫 爪とぎ",
                  "犬 ハーネス", "猫 トイレ", "ペット キャリー", "犬 シャンプー", "猫 おやつ"],
    "health":    ["サプリメント マルチビタミン", "プロテイン ホエイ", "マッサージガン", "青汁 国産",
                  "体温計 非接触", "血圧計 手首", "アイマスク ホット", "ストレッチポール", "入浴剤 ギフト", "湿布 温感"],
    "outdoor":   ["キャンプ チェア", "レジャーシート 防水", "クーラーボックス 小型", "焚き火台 ソロ",
                  "ランタン LED", "テント ワンタッチ", "アウトドア テーブル", "虫除け スプレー", "登山 リュック", "釣り ルアー"],
    "interior":  ["カーテン 遮光", "ラグ 洗える", "照明 ペンダント", "観葉植物 フェイク",
                  "壁掛け フック", "ミラー 全身", "キャンドル アロマ", "ティッシュケース おしゃれ", "トイレマット", "玄関マット"],
    "stationery":["手帳 2026", "ペンケース 大容量", "付箋 かわいい", "ボールペン 高級",
                  "ノート A5", "ファイル クリア", "電卓 おしゃれ", "マーカー 蛍光", "シール デコ", "名刺入れ 革"],
    "baby":      ["ベビー服 新生児", "おむつ パンパース", "抱っこ紐 軽量", "哺乳瓶 ガラス",
                  "ベビーカー コンパクト", "離乳食 食器", "おしりふき", "ベビーモニター", "チャイルドシート", "ガーゼ タオル"],
    "sports":    ["ヨガマット 厚手", "ランニングシューズ メンズ", "ダンベル 可変式", "スポーツタオル",
                  "水着 レディース", "プロテインシェイカー", "サイクリング グローブ", "テニスラケット", "ゴルフボール", "トレーニングウェア"],
    "car":       ["車載充電器 急速", "ドライブレコーダー", "車用芳香剤", "シートカバー",
                  "スマホホルダー 車", "サンシェード 車", "洗車 スポンジ", "タイヤ空気入れ", "車内収納", "LEDバルブ 車"],
    "garden":    ["プランター おしゃれ", "園芸 土", "ガーデニング 手袋", "じょうろ ステンレス",
                  "種 野菜", "ハーブ 苗", "人工芝 ベランダ", "物干し台 ステンレス", "防草シート", "ソーラーライト 庭"],
    "travel":    ["スーツケース 機内持込", "トラベルポーチ セット", "ネックピロー 低反発", "パスポートケース",
                  "圧縮袋 衣類", "変換プラグ 海外", "折りたたみ ボストンバッグ", "アイマスク 旅行", "携帯スリッパ", "セキュリティポーチ"],
    "seasonal":  ["扇風機 ハンディ", "冷感タオル", "日焼け止め スプレー", "制汗剤",
                  "アイスリング", "保冷バッグ ランチ", "麦茶 パック", "蚊取り線香", "かき氷機 家庭用", "ビニールプール"],
}

# ============================================================
# 運用モード管理
# ============================================================

def get_operation_mode() -> dict:
    """運用モードをファイルから読み込む。デフォルトはSAFE 20件"""
    if OPERATION_MODE_FILE.exists():
        try:
            with open(OPERATION_MODE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            pass
    return {"mode": "SAFE", "safe_limit": 20, "updated_at": "", "notes": "初期設定"}


def set_operation_mode(mode: str, safe_limit: int = 20, notes: str = "") -> dict:
    """運用モードをファイルに保存する"""
    if mode not in ("AUTO", "SAFE", "STOP"):
        raise ValueError(f"無効なモード: {mode}（AUTO/SAFE/STOPのみ）")
    data = {
        "mode": mode,
        "safe_limit": safe_limit,
        "updated_at": datetime.now().isoformat(),
        "notes": notes,
    }
    OPERATION_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OPERATION_MODE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


# ディレクトリ作成
for d in [LOG_DIR, SCREENSHOT_DIR, DATA_DIR / "state", DAILY_LOG_DIR,
          AUDIT_DIR, REPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ======================================================
# レート制限ルール (実データ分析確定 2026-05-01)
# ratelimit_micro_analyzer.py が自動導出
# 2026-05-05 Plan v4 P3: SSOT スプシ連動化 (CEO がスプシで上限値を更新可能)
# ======================================================
FOLLOW_RL_COOLDOWN_MIN   = 69   # RL後の最小待機時間(分) ← 確定・bot適用済


def get_follow_rate_limits() -> dict:
    """SSOT (スプシ) からフォロー rate limit 上限を取得。
    cache 6h、失敗時は config の hard-code fallback。

    SSOT 列: スプシ「楽天ROOM_検証管理 / 楽天ROOM_デイリーログ」(gid=1447646534)
    現状はスプシに専用列がないので、当面は hard-code を使う。
    将来 CEO がスプシに「FOLLOW_SAFE_24H_MAX」「FOLLOW_SAFE_HOURLY_MAX」列を追加すれば
    自動で反映される。
    """
    # default fallback (実観測 833 / 安全 708 / 1h 80)
    fallback = {
        "safe_hourly_max": 80,    # 1h 上限 (仮説値・実観測 ?)
        "safe_24h_max":    99999, # 2026-05-08 CEO 指示「FOLLOW_SAFE_24H_MAX=833 ルール削除」 → 実質撤廃
        "rl_cooldown_min": 69,    # 確定値
    }
    cache = DATA_DIR / "state" / "rate_limits_ssot.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            from datetime import datetime as _dt
            cache_age = (_dt.now() - _dt.fromisoformat(data["fetched_at"])).total_seconds()
            if cache_age < 21600:  # 6h
                return data.get("limits", fallback)
        except Exception:
            pass
    # SSOT スプシから取得試行 (列が無ければ fallback 使用)
    try:
        # 将来拡張用 placeholder。現状は fallback をそのまま使う。
        limits = fallback
        cache.write_text(json.dumps({
            "date": today,
            "fetched_at": datetime.now().isoformat(),
            "limits": limits,
            "source": "fallback (Plan v4 P3 placeholder)",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return fallback
    return limits


# Plan v4 P3 (2026-05-05): hard-code を撤廃して SSOT 経由参照
# 旧運用との互換のため module 変数も残すが、実装側は get_follow_rate_limits() を使うこと
_rl = get_follow_rate_limits()
FOLLOW_SAFE_HOURLY_MAX = _rl["safe_hourly_max"]
FOLLOW_SAFE_24H_MAX    = _rl["safe_24h_max"]   # 2026-05-08 CEO 指示で 833 ルール削除 → 99999 (実質撤廃・rate_limit 自動 cooldown のみ残す)
# 日次ハードキャップ: 実観測 max=833 を採用
# (根拠: VM実データ分析: 回復中央値=60分, 24h最大観測=833件)
