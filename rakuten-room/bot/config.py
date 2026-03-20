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

# Playwright設定
BROWSER_HEADLESS = False
BROWSER_SLOW_MO = 100
CHROME_EXECUTABLE_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Persistent context用のユーザーデータ保存先（通常Chromeからcookieをコピーして使用）
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
# 投稿BOT設定
# ============================================================

# 日次投稿数（BOT対策: 日によって変動させる）
POST_DAILY_MIN = 90
POST_DAILY_MAX = 100

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
# ヘルパー関数
# ============================================================

def get_daily_post_target():
    """今日の投稿目標件数をランダムに決定する（90〜100）"""
    return random.randint(POST_DAILY_MIN, POST_DAILY_MAX)

def get_daily_follow_target():
    """今日のフォロー目標件数をランダムに決定する（150〜250）"""
    return random.randint(FOLLOW_DAILY_MIN, FOLLOW_DAILY_MAX)

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
POOL_MIN = 300                    # 最低維持件数
POOL_MAX = 600                    # 最大件数（超過時はscore低い順に削除）
POOL_REPLENISH_BUFFER = 50        # 補充時のバッファ

# 監査設定
AUDIT_DIR = DATA_DIR / "audit"
AUDIT_RESULTS_PATH = AUDIT_DIR / "audit_results.json"

# レポート
REPORT_DIR = DATA_DIR / "reports"

# 運用モード
OPERATION_MODE_FILE = DATA_DIR / "operation_mode.json"

# ============================================================
# ジャンル別検索キーワード（楽天API商品取得用）
# ============================================================

GENRE_SEARCH_KEYWORDS = {
    "kitchen":   ["フライパン セット", "保存容器 耐熱", "水切りラック", "キッチンツール セット", "包丁 ステンレス"],
    "beauty":    ["スキンケア セット", "ヘアオイル 人気", "日焼け止め", "コスメ ポーチ", "ハンドクリーム"],
    "living":    ["今治タオル セット", "収納ボックス おしゃれ", "マグカップ 北欧", "クッション カバー", "ルームウェア"],
    "fashion":   ["バッグ レディース 軽量", "スニーカー 白", "ストール UV", "帽子 レディース", "ワンピース 春"],
    "appliance": ["モバイルバッテリー 軽量", "ワイヤレスイヤホン", "加湿器 卓上", "LEDライト デスク", "充電器 USB"],
    "food":      ["コーヒー ドリップ", "ナッツ 素焼き", "お菓子 詰め合わせ", "紅茶 ギフト", "スイーツ お取り寄せ"],
    "kids":      ["知育玩具", "水筒 キッズ", "お弁当箱 子供", "入学準備", "絵本 人気"],
    "book":      ["自己啓発 ベストセラー", "レシピ本", "ビジネス書 ランキング", "絵本 セット", "参考書"],
    "pet":       ["ペットベッド", "猫 おもちゃ", "犬 おやつ 無添加", "ペット 食器", "猫 爪とぎ"],
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
