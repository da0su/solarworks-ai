"""ROOM BOT v6.0 - CEOコマンド運用 + 休みロジック + 臨時業務"""

import os
import random
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

# 会社フォルダルート
COMPANY_ROOT = PROJECT_ROOT.parent.parent
POST_LOG_PATH = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "POST_LOG.json"
FOLLOW_LOG_PATH = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "FOLLOW_LOG.json"
DAILY_LOG_DIR = COMPANY_ROOT / "05_CONTENT" / "rakuten_room" / "history" / "daily"

# Playwright設定
BROWSER_HEADLESS = False
BROWSER_SLOW_MO = 100
CHROME_EXECUTABLE_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

# ============================================================
# CDP接続モード（推奨）
# ============================================================
# True: CEOの通常Chrome（ログイン済み）に外部接続する
#   → 楽天ROOMのログインセッションをそのまま利用
#   → Chrome起動時に --remote-debugging-port=9222 が必要
# False: BOT専用プロファイルで launch_persistent_context（従来方式）
USE_CDP_MODE = True
CDP_URL = "http://localhost:9222"

# Persistent context用のユーザーデータ保存先（USE_CDP_MODE=False時のみ使用）
CHROME_USER_DATA_DIR = DATA_DIR / "chrome_profile"

# セッション
SESSION_STATE_PATH = DATA_DIR / "state" / "storage_state.json"

# 楽天ROOM
ROOM_BASE_URL = "https://room.rakuten.co.jp"
RAKUTEN_BASE_URL = "https://www.rakuten.co.jp"
RECOMMEND_USERS_URL = "https://room.rakuten.co.jp/discover/recommendUsers"

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
# 投稿BOT設定（v5.0 人間らしい揺らぎスケジュール）
# ============================================================

# 日次投稿数（BOT対策: 日によって変動させる）
POST_DAILY_MIN = 98
POST_DAILY_MAX = 102

# --- 3バッチ構成 ---
# 各バッチの設定: 開始基準時刻、ジッター範囲、件数範囲、投稿間隔
POST_BATCHES = {
    "night": {
        "start_hour": 0,              # 基準: 0:00
        "start_minute_min": 0,        # 開始分の下限
        "start_minute_max": 30,       # 開始分の上限 → 0:00-0:30
        "count_min": 30,              # 最小件数
        "count_max": 40,              # 最大件数
        "interval_min": 0,            # 投稿間隔（秒）最小 ※人間的ランダム
        "interval_max": 20,           # 投稿間隔（秒）最大
    },
    "lunch": {
        "start_hour": 11,             # 基準: 11時台
        "start_minute_min": 30,       # 11:30
        "start_minute_max": 210,      # +210分 = 最大14:30  (11:30 + 180min)
        "count_min": 25,              # 最小件数
        "count_max": 35,              # 最大件数
        "interval_min": 0,            # 投稿間隔（秒）最小 ※人間的ランダム
        "interval_max": 20,           # 投稿間隔（秒）最大
    },
    "evening": {
        "start_hour": 19,             # 基準: 19時台
        "start_minute_min": 0,        # 19:00
        "start_minute_max": 210,      # +210分 = 最大22:30  (19:00 + 210min) ※余裕を持たせる
        "count_min": None,            # 残り全件（自動計算）
        "count_max": None,            # 残り全件（自動計算）
        "interval_min": 0,            # 投稿間隔（秒）最小 ※人間的ランダム
        "interval_max": 20,           # 投稿間隔（秒）最大
    },
}

# 日次ジッター（各バッチ開始時刻に追加される揺らぎ）
DAILY_JITTER_MIN = 1                 # 最小ジッター（分）
DAILY_JITTER_MAX = 15                # 最大ジッター（分）

# 投稿導線の比率（合計100にする）
POST_ROUTE_RATIO = {
    "direct": 70,       # 商品ページから直接投稿
    "influencer": 30,   # インフルエンサー投稿から派生
}

# 投稿間隔デフォルト（人間らしいランダム間隔）
POST_INTERVAL_MIN = 0                # 最小間隔（秒）
POST_INTERVAL_MAX = 20               # 最大間隔（秒）

# 臨時投稿(room plus post)用の高速間隔
ROOM_PLUS_POST_INTERVAL_MIN = 0      # 最小間隔（秒）
ROOM_PLUS_POST_INTERVAL_MAX = 5      # 最大間隔（秒）

# 安全休憩（N投稿ごとに休憩を挟む）
POST_REST_EVERY = 20                  # 20投稿ごとに休憩
POST_REST_DURATION_MIN = 5            # 休憩時間の最小値（秒）
POST_REST_DURATION_MAX = 15           # 休憩時間の最大値（秒）

# 制限
POST_MAX_SAME_GENRE = 3              # 同ジャンル連続投稿の上限

# 日次プラン生成時刻（scheduler用）
PLAN_GENERATION_HOUR = 23
PLAN_GENERATION_MINUTE = 50          # 23:50 に翌日プラン生成

# ============================================================
# フォローBOT設定
# ============================================================

# 日次フォロー数（BOT対策: 日によって変動させる）
FOLLOW_DAILY_MIN = 150
FOLLOW_DAILY_MAX = 250

# セッション分割
FOLLOW_SESSION_MAX = 50              # 1セッション = 最大50件
FOLLOW_SESSIONS_PER_DAY = 4          # 1日4セッション

# フォロー間隔（ランダム化。固定間隔にしない）
FOLLOW_INTERVAL_MIN = 1.0            # 最小間隔（秒）
FOLLOW_INTERVAL_MAX = 3.0            # 最大間隔（秒）

# セッション内休憩（一定件数ごとにランダム休憩を入れる）
FOLLOW_REST_EVERY_MIN = 10           # 休憩を入れる間隔の最小値（件）
FOLLOW_REST_EVERY_MAX = 15           # 休憩を入れる間隔の最大値（件）
FOLLOW_REST_DURATION_MIN = 10        # 休憩時間の最小値（秒）
FOLLOW_REST_DURATION_MAX = 30        # 休憩時間の最大値（秒）

# セッション間休憩
FOLLOW_SESSION_REST_MIN = 300        # セッション間休憩の最小値（秒）= 5分
FOLLOW_SESSION_REST_MAX = 900        # セッション間休憩の最大値（秒）= 15分

# ============================================================
# いいねBOT設定
# ============================================================

# 日次いいね数（初期運用: 控えめ / 安定後: 拡大）
LIKE_DAILY_MIN = 30                  # 初期: 30件/日
LIKE_DAILY_MAX = 50                  # 初期: 50件/日
# 安定後: LIKE_DAILY_MIN=80, LIKE_DAILY_MAX=150

# セッション分割
LIKE_SESSION_MIN = 10                # 1セッション最小件数
LIKE_SESSION_MAX = 30                # 1セッション最大件数

# いいね間隔（人間らしい揺らぎ）
LIKE_INTERVAL_MIN = 2.0              # 通常間隔 最小（秒）
LIKE_INTERVAL_MAX = 8.0              # 通常間隔 最大（秒）

# ランダム休憩（一定件数ごと）
LIKE_REST_EVERY_MIN = 5              # 休憩を入れる間隔（件）最小
LIKE_REST_EVERY_MAX = 12             # 休憩を入れる間隔（件）最大
LIKE_REST_DURATION_MIN = 10          # 休憩時間（秒）最小
LIKE_REST_DURATION_MAX = 30          # 休憩時間（秒）最大

# 連続失敗で停止
LIKE_MAX_CONSECUTIVE_FAILURES = 5

# いいね対象フィードURL（診断結果 2026-03-16 に基づく）
# /all/feed, /all/ranking は 404。正しいフィードは / → /items にリダイレクト
LIKE_FEED_URLS = [
    "https://room.rakuten.co.jp/",              # メインフィード（→ /items）
    "https://room.rakuten.co.jp/my/feed",       # MYフィード（フォロー中ユーザーの投稿）
]

# いいね履歴（重複防止）
LIKE_HISTORY_PATH = DATA_DIR / "like_history.json"

# ============================================================
# CEOコマンド / 運用状態管理
# ============================================================

# 運用状態ファイル
ROOM_STATE_PATH = DATA_DIR / "state" / "room_state.json"

# 月間スケジュール
MONTHLY_SCHEDULE_PATH = DATA_DIR / "monthly_schedule.json"

# 臨時業務ログ
ROOM_PLUS_LOG_PATH = DATA_DIR / "room_plus_log.json"

# ============================================================
# 日タイプ別パラメータ（normal / light / off）
# ============================================================

DAY_TYPE_PARAMS = {
    "normal": {
        "post_min": 98, "post_max": 102,
        "like_min": 30, "like_max": 50,
        "follow_min": 190, "follow_max": 205,
    },
    "light": {
        "post_min": 20, "post_max": 40,
        "like_min": 10, "like_max": 20,
        "follow_min": 30, "follow_max": 80,
    },
    "off": {
        "post_min": 0, "post_max": 0,
        "like_min": 0, "like_max": 0,
        "follow_min": 0, "follow_max": 0,
    },
}

# 月間休みルール
MONTHLY_OFF_DAYS_MIN = 3                 # 月間off日の最小数
MONTHLY_OFF_DAYS_MAX = 5                 # 月間off日の最大数
MONTHLY_LIGHT_DAYS_MIN = 2               # 月間light日の最小数
MONTHLY_LIGHT_DAYS_MAX = 3               # 月間light日の最大数
MAX_CONSECUTIVE_OFF = 2                  # 連続offの上限

# ============================================================
# room plus 安全上限
# ============================================================

ROOM_PLUS_MAX_POST = 50                  # 1回の臨時投稿 最大50件
ROOM_PLUS_MAX_LIKE = 30                  # 1回の臨時いいね 最大30件
ROOM_PLUS_MAX_FOLLOW = 30               # 1回の臨時フォロー 最大30件

# ============================================================
# ヘルパー関数
# ============================================================

def get_daily_post_target():
    """今日の投稿目標件数をランダムに決定する（98〜102）"""
    return random.randint(POST_DAILY_MIN, POST_DAILY_MAX)

def get_daily_follow_target():
    """今日のフォロー目標件数をランダムに決定する（190〜205）"""
    return random.randint(FOLLOW_DAILY_MIN, FOLLOW_DAILY_MAX)

def get_post_route():
    """投稿導線をランダムに選択する（比率に基づく）"""
    r = random.randint(1, 100)
    if r <= POST_ROUTE_RATIO["direct"]:
        return "direct"
    return "influencer"

def get_random_post_interval():
    """投稿間隔をランダムに決定する（秒）- フォールバック用"""
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

def get_daily_like_target():
    """今日のいいね目標件数をランダムに決定する"""
    return random.randint(LIKE_DAILY_MIN, LIKE_DAILY_MAX)

def get_like_interval():
    """いいね間隔をランダムに決定する（秒）"""
    return random.uniform(LIKE_INTERVAL_MIN, LIKE_INTERVAL_MAX)

def get_like_rest_interval():
    """いいね中の休憩を入れる件数をランダムに決定する"""
    return random.randint(LIKE_REST_EVERY_MIN, LIKE_REST_EVERY_MAX)

def get_like_rest_duration():
    """いいね中の休憩時間をランダムに決定する（秒）"""
    return random.uniform(LIKE_REST_DURATION_MIN, LIKE_REST_DURATION_MAX)

def get_day_type_targets(day_type: str) -> dict:
    """day_typeに応じた目標件数を返す"""
    params = DAY_TYPE_PARAMS.get(day_type, DAY_TYPE_PARAMS["normal"])
    return {
        "post": random.randint(params["post_min"], params["post_max"]),
        "like": random.randint(params["like_min"], params["like_max"]),
        "follow": random.randint(params["follow_min"], params["follow_max"]),
    }

# ============================================================
# 段階テスト設定（テスト時はこちらの値で上書きする）
# ============================================================
# Test A: POST_DAILY_MIN=20, POST_DAILY_MAX=20
# Test B: POST_DAILY_MIN=50, POST_DAILY_MAX=50
# Test C: POST_DAILY_MIN=98, POST_DAILY_MAX=102 (本番)

# ディレクトリ作成
for d in [LOG_DIR, SCREENSHOT_DIR, DATA_DIR / "state", DAILY_LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
