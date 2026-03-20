"""コインリサーチ事業 - 設定ファイル v1.0

仮想通貨マーケット情報の収集・分析・レポート生成の設定。
判断支援が目的。自動売買は行わない。
"""

import os
from pathlib import Path

# ================================================================
# パス定義（絶対パス・環境統制ルール準拠）
# ================================================================

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LOG_DIR = PROJECT_ROOT / "logs"
DOCS_DIR = PROJECT_ROOT / "docs"

# マイグレーション
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# データサブディレクトリ
PRICES_DIR = DATA_DIR / "prices"
VOLUME_DIR = DATA_DIR / "volume"
NEWS_DIR = DATA_DIR / "news"
SNS_DIR = DATA_DIR / "sns"
ONCHAIN_DIR = DATA_DIR / "onchain"

# アウトプットサブディレクトリ
DAILY_OUTPUT_DIR = OUTPUTS_DIR / "daily"
WEEKLY_OUTPUT_DIR = OUTPUTS_DIR / "weekly"

# ================================================================
# 対象通貨
# ================================================================

TARGET_COINS = [
    "bitcoin",
    "ethereum",
    "solana",
    "xrp",
    "cardano",
]

# CoinGecko ID → 表示名マッピング
COIN_DISPLAY_NAMES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
    "cardano": "ADA",
}

# ================================================================
# API設定
# ================================================================

# CoinGecko（価格・出来高）
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Binance（オプション: リアルタイム価格）
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_BASE_URL = "https://api.binance.com/api/v3"

# CryptoPanic（ニュース）
CRYPTOPANIC_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")
CRYPTOPANIC_BASE_URL = "https://cryptopanic.com/api/v1"

# Etherscan（オンチェーン）
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
ETHERSCAN_BASE_URL = "https://api.etherscan.io/api"

# ================================================================
# 異常検知閾値
# ================================================================

ALERT_THRESHOLDS = {
    "price_surge_1h_pct": 5.0,      # 1時間で+5%以上 → 急騰通知
    "price_drop_1h_pct": -5.0,      # 1時間で-5%以上 → 急落通知
    "volume_spike_ratio": 3.0,      # 24h平均出来高の3倍 → 異常通知
    "sentiment_extreme_high": 0.8,  # センチメント高極値
    "sentiment_extreme_low": -0.8,  # センチメント低極値
}

# ================================================================
# レポート設定
# ================================================================

# 日次レポートに含める内容
DAILY_REPORT_SECTIONS = [
    "market_summary",       # 市場全体サマリー
    "price_changes",        # 主要通貨の価格変動
    "volume_analysis",      # 出来高分析
    "news_highlights",      # 主要ニュース
    "alerts",               # 異常検知アラート
]

# 週次レポートに追加される内容
WEEKLY_REPORT_EXTRA_SECTIONS = [
    "trend_analysis",       # 週間トレンド
    "correlation_matrix",   # 相関分析
    "outlook",              # 今後の注目点
]

# ================================================================
# Supabase設定
# ================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role_key（サーバー処理専用）

# ================================================================
# Airtable設定（レガシー: 段階的にSupabaseへ移行）
# ================================================================

AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")

# ================================================================
# Slack通知設定
# ================================================================

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
SLACK_COIN_CHANNEL = os.environ.get("SLACK_COIN_CHANNEL", "")

# ================================================================
# Scheduler設定
# ================================================================

SCHEDULE = [
    {"time": "06:00", "action": "collect_all",    "desc": "全データ取得"},
    {"time": "06:30", "action": "analyze_all",    "desc": "全分析実行"},
    {"time": "07:00", "action": "daily_report",   "desc": "日次レポート生成＋Slack通知"},
    {"time": "12:00", "action": "collect_prices",  "desc": "価格データ中間取得"},
    {"time": "18:00", "action": "collect_all",    "desc": "全データ取得（夕方）"},
    {"time": "18:30", "action": "analyze_all",    "desc": "全分析実行（夕方）"},
    {"time": "19:00", "action": "evening_report", "desc": "夕方サマリー＋Slack通知"},
]

# ================================================================
# ログ設定
# ================================================================

LOG_LEVEL = os.environ.get("COIN_RESEARCH_LOG_LEVEL", "INFO")
LOG_RETENTION_DAYS = 90  # 長期蓄積のため90日保持

# ================================================================
# ディレクトリ初期化
# ================================================================

for d in [DATA_DIR, PRICES_DIR, VOLUME_DIR, NEWS_DIR, SNS_DIR,
          ONCHAIN_DIR, OUTPUTS_DIR, DAILY_OUTPUT_DIR, WEEKLY_OUTPUT_DIR,
          LOG_DIR, DOCS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
