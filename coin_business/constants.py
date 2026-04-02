"""
coin_business/constants.py
==========================
コイン仕入れリサーチ事業 — 全定数の唯一の定義場所

このファイルを constants の source of truth とする。
Python コード内でステータス名・level名・cadence 値を
ベタ書き (例: "AUDIT_PASS", "watching") することを禁止する。
変更が必要なときはこのファイルのみを修正する。

Day 1 固定 (基準コミット: 8e228c2, be978e8)
"""

from __future__ import annotations

# ================================================================
# ソース種別 (source field の値)
# ================================================================

class Source:
    EBAY      = "ebay"
    HERITAGE  = "heritage"
    SPINK     = "spink"
    STACKS    = "stacks_bowers"
    NOBLE     = "noble"
    BITNOW    = "bitnow"         # 本流では使わない。negotiate_later のみ。
    YAHOO     = "yahoo"

    ALL_AUCTION_HOUSES = (HERITAGE, SPINK, STACKS, NOBLE)
    ALL_SOURCES = (EBAY, HERITAGE, SPINK, STACKS, NOBLE, YAHOO)
    BITNOW_POLICY = "NEGOTIATE_LATER"  # BitNow は本流に入れない


# ================================================================
# Yahoo! staging ステータス
# ================================================================

class YahooStagingStatus:
    PENDING_CEO      = "PENDING_CEO"       # CEO確認待ち（最初の10日間は必ずここ）
    APPROVED_TO_MAIN = "APPROVED_TO_MAIN"  # CEO/CAP 承認済み。昇格処理待ち
    PROMOTED         = "PROMOTED"          # yahoo_sold_lots へ昇格完了
    REJECTED         = "REJECTED"          # 却下
    HELD             = "HELD"              # 保留中（再確認予定）

    ALL = (PENDING_CEO, APPROVED_TO_MAIN, PROMOTED, REJECTED, HELD)
    PENDING = (PENDING_CEO, HELD)
    TERMINAL = (PROMOTED, REJECTED)

    # CEO 承認が必須の期間
    CEO_APPROVAL_DAYS = 10


# ================================================================
# Yahoo! seed ステータス (yahoo_coin_seeds.seed_status)
# ================================================================

class SeedStatus:
    READY    = "READY"     # 次回スキャン待ち（初期値）
    SCANNING = "SCANNING"  # スキャン中
    COOLDOWN = "COOLDOWN"  # スキャン完了・次回まで待機 (next_scan_at 参照)
    DISABLED = "DISABLED"  # 無効化 (hit_count 過多 / 手動停止)

    ALL    = (READY, SCANNING, COOLDOWN, DISABLED)
    ACTIVE = (READY, SCANNING)


# ================================================================
# Yahoo! seed 種別 (yahoo_coin_seeds.seed_type)
# ================================================================

class SeedType:
    CERT_EXACT        = "CERT_EXACT"        # cert_company + cert_number 完全一致
    CERT_TITLE        = "CERT_TITLE"        # cert_company + title_normalized
    TITLE_NORMALIZED  = "TITLE_NORMALIZED"  # title_normalized のみ
    YEAR_DENOM_GRADE  = "YEAR_DENOM_GRADE"  # year + denomination + grade_text

    ALL = (CERT_EXACT, CERT_TITLE, TITLE_NORMALIZED, YEAR_DENOM_GRADE)

    # 優先度スコア（高いほど先にスキャン）
    PRIORITY: dict[str, float] = {
        CERT_EXACT:       1.0,
        CERT_TITLE:       0.7,
        TITLE_NORMALIZED: 0.5,
        YEAR_DENOM_GRADE: 0.3,
    }


# ================================================================
# eBay seed scanner クールダウン時間 (seed_type 別)
# CERT_EXACT が最高頻度、YEAR_DENOM_GRADE が最低頻度
# ================================================================

class ScannerCadence:
    """seed_type ごとの COOLDOWN 時間（時間）。
    priority が高い seed ほど短い間隔で再スキャンする。
    """
    CERT_EXACT_HOURS        = 1    # CERT_EXACT → 1 時間
    CERT_TITLE_HOURS        = 2    # CERT_TITLE → 2 時間
    TITLE_NORMALIZED_HOURS  = 4    # TITLE_NORMALIZED → 4 時間
    YEAR_DENOM_GRADE_HOURS  = 6    # YEAR_DENOM_GRADE → 6 時間

    _MAP: dict[str, int] = {
        "CERT_EXACT":       CERT_EXACT_HOURS,
        "CERT_TITLE":       CERT_TITLE_HOURS,
        "TITLE_NORMALIZED": TITLE_NORMALIZED_HOURS,
        "YEAR_DENOM_GRADE": YEAR_DENOM_GRADE_HOURS,
    }

    @classmethod
    def cooldown_hours(cls, seed_type: str) -> int:
        """seed_type に対応するクールダウン時間（時間）を返す。
        未知の seed_type は最大値 YEAR_DENOM_GRADE_HOURS を返す。
        """
        return cls._MAP.get(seed_type, cls.YEAR_DENOM_GRADE_HOURS)


# ================================================================
# 候補レベル（Level A のみ仕入れ対象）
# ================================================================

class CandidateLevel:
    A = "A"  # 仕入れ対象。以下の3条件のいずれかを満たす:
    #   1) cert_company + cert_number 完全一致
    #   2) Yahoo!基準より高グレードで利益条件を満たす
    #   3) 年代差 ±5年 以内で利益条件を満たす

    B = "B"  # 価格参考・相場補助のみ。候補化しない。
    C = "C"  # 完全無関係。除外。

    ALL = (A, B, C)
    ELIGIBLE = (A,)   # daily_candidates に昇格できるレベル

    # Level A の年代差許容範囲
    YEAR_DELTA_MAX = 5       # ±5年以内

    # Level A の利益条件（min）
    MIN_PROFIT_JPY  = 0      # 利益 > 0 円
    MIN_ROI         = 0.0    # ROI > 0%


# ================================================================
# マッチング種別
# ================================================================

class MatchType:
    CERT_EXACT   = "cert_exact"    # cert_company + cert_number 完全一致
    HIGH_GRADE   = "high_grade"    # Yahoo基準より高グレード + 利益条件
    YEAR_DELTA   = "year_delta"    # 年代差 ±5年 + 利益条件
    TITLE_FUZZY  = "title_fuzzy"   # タイトル類似（補助）

    ALL = (CERT_EXACT, HIGH_GRADE, YEAR_DELTA, TITLE_FUZZY)
    LEVEL_A_TYPES = (CERT_EXACT, HIGH_GRADE, YEAR_DELTA)


# ================================================================
# CAP 監査ステータス
# ================================================================

class AuditStatus:
    AUDIT_PASS = "AUDIT_PASS"   # 全チェック通過 → daily_candidates 昇格
    AUDIT_HOLD = "AUDIT_HOLD"   # 一部条件未達 → 人間確認待ち
    AUDIT_FAIL = "AUDIT_FAIL"   # 除外条件あり → 候補化しない

    ALL = (AUDIT_PASS, AUDIT_HOLD, AUDIT_FAIL)
    PROMOTE_ELIGIBLE = (AUDIT_PASS,)  # これ以外は daily_candidates に昇格しない


# ================================================================
# CAP 監査チェック項目
# ================================================================

class AuditCheck:
    CERT_VALIDITY      = "cert_validity"       # cert 妥当性
    TITLE_CONSISTENCY  = "title_consistency"   # タイトル整合
    GRADE_DELTA        = "grade_delta"         # グレード差
    YEAR_DELTA         = "year_delta"          # 年数差
    PROFIT_CONDITION   = "profit_condition"    # 利益条件
    SHIPPING_VALID     = "shipping_valid"      # shipping 条件（eBay: US/UK のみ）
    LOT_SIZE_SINGLE    = "lot_size_single"     # lot size = 単品のみ
    NOT_STALE          = "not_stale"           # stale でない（6h以内に確認済み）
    NOT_SOLD           = "not_sold"            # sold でない
    NOT_ENDED          = "not_ended"           # 終了していない

    ALL = (
        CERT_VALIDITY, TITLE_CONSISTENCY, GRADE_DELTA, YEAR_DELTA,
        PROFIT_CONDITION, SHIPPING_VALID, LOT_SIZE_SINGLE,
        NOT_STALE, NOT_SOLD, NOT_ENDED,
    )
    CHECK_RESULT_PASS = "pass"
    CHECK_RESULT_FAIL = "fail"
    CHECK_RESULT_WARN = "warn"
    CHECK_RESULT_SKIP = "skip"


# ================================================================
# 自動評価ティア（daily_candidates.auto_tier）
# ================================================================

class AutoTier:
    AUTO_PASS   = "AUTO_PASS"    # 完全自動承認可能
    AUTO_REVIEW = "AUTO_REVIEW"  # CEO レビュー推奨
    AUTO_REJECT = "AUTO_REJECT"  # 自動除外

    ALL = (AUTO_PASS, AUTO_REVIEW, AUTO_REJECT)


# ================================================================
# CEO 判断ステータス（daily_candidates.ceo_decision）
# ================================================================

class CeoDecision:
    PENDING  = "pending"   # 未判断
    APPROVED = "approved"  # 承認
    REJECTED = "rejected"  # 却下
    HELD     = "held"      # 保留

    # ※ 旧DB互換: "ng" は "rejected" と同等扱い（normalize_ceo_decision() で変換）
    LEGACY_NG = "ng"

    ALL = (PENDING, APPROVED, REJECTED, HELD)
    ACTIONABLE = (APPROVED,)   # bid queue に送れる状態


# ================================================================
# KEEP 監視ステータス（candidate_watchlist.status）
# ================================================================

class WatchStatus:
    WATCHING     = "watching"      # 監視中
    PRICE_OK     = "price_ok"      # 価格が目標以内
    PRICE_HIGH   = "price_too_high"  # 価格が上限超過
    ENDING_SOON  = "ending_soon"   # 終了間近 (1時間以内)
    BID_READY    = "bid_ready"     # 入札実行可能状態
    BID_QUEUED   = "bid_queued"    # 入札キュー登録済み
    ENDED        = "ended"         # 終了（落札・流れ問わず）
    CANCELLED    = "cancelled"     # 監視キャンセル

    ALL = (WATCHING, PRICE_OK, PRICE_HIGH, ENDING_SOON, BID_READY,
           BID_QUEUED, ENDED, CANCELLED)
    ACTIVE = (WATCHING, PRICE_OK, ENDING_SOON, BID_READY)
    TERMINAL = (ENDED, CANCELLED)


# ================================================================
# KEEP 監視頻度（秒）
# 残時間に応じた refresh cadence
# ================================================================

class WatchCadence:
    NORMAL_SECONDS     = 3 * 3600   # 通常: 3時間ごと
    WITHIN_24H_SECONDS = 1 * 3600   # 24時間以内: 1時間ごと
    WITHIN_6H_SECONDS  = 30 * 60    # 6時間以内: 30分ごと
    WITHIN_1H_SECONDS  = 10 * 60    # 1時間以内: 10分ごと

    # 残時間の閾値（秒）
    THRESHOLD_24H = 24 * 3600
    THRESHOLD_6H  =  6 * 3600
    THRESHOLD_1H  =  1 * 3600

    @staticmethod
    def for_time_left(time_left_seconds: int | None) -> int:
        """残時間に応じた refresh interval（秒）を返す"""
        if time_left_seconds is None:
            return WatchCadence.NORMAL_SECONDS
        if time_left_seconds <= WatchCadence.THRESHOLD_1H:
            return WatchCadence.WITHIN_1H_SECONDS
        if time_left_seconds <= WatchCadence.THRESHOLD_6H:
            return WatchCadence.WITHIN_6H_SECONDS
        if time_left_seconds <= WatchCadence.THRESHOLD_24H:
            return WatchCadence.WITHIN_24H_SECONDS
        return WatchCadence.NORMAL_SECONDS


# ================================================================
# T-minus 世界オークション監視ステージ（日数）
# ================================================================

class TMinusStage:
    T21 = 21   # 開催3週間前: lot 初期収集
    T7  = 7    # 開催1週間前: 全 lot 更新
    T3  = 3    # 開催3日前: 注目 lot 優先監視
    T1  = 1    # 開催前日: 最終確認・アラート

    ALL = (T21, T7, T3, T1)


# ================================================================
# 入札記録ステータス（bidding_records.status）
# ================================================================

class BidStatus:
    QUEUED     = "queued"      # キュー登録済み
    SUBMITTED  = "submitted"   # 送信済み
    WON        = "won"         # 落札
    LOST       = "lost"        # 落選
    CANCELLED  = "cancelled"   # キャンセル
    ERROR      = "error"       # エラー

    ALL = (QUEUED, SUBMITTED, WON, LOST, CANCELLED, ERROR)
    TERMINAL = (WON, LOST, CANCELLED, ERROR)


# ================================================================
# グレーダー
# ================================================================

class Grader:
    NGC  = "NGC"
    PCGS = "PCGS"
    RAW  = "RAW"

    CERTIFIED = (NGC, PCGS)

    # eBay: USD のみ / US または UK 発送のみ
    EBAY_VALID_CURRENCY    = "USD"
    EBAY_VALID_SHIP_FROM   = ("US", "GB")

    # stale 判定: 6時間以上更新なし
    STALE_THRESHOLD_HOURS = 6


# ================================================================
# 通知種別
# ================================================================

class NotificationType:
    MORNING_BRIEF    = "morning_brief"
    LEVEL_A_NEW      = "level_a_new"
    KEEP_PRICE_ALERT = "keep_price_alert"
    ENDING_SOON      = "ending_soon"
    BID_READY        = "bid_ready"
    GLOBAL_LOT_ALERT = "global_lot_alert"
    BID_RESULT       = "bid_result"
    NIGHTLY_SUMMARY  = "nightly_summary"

    ALL = (
        MORNING_BRIEF, LEVEL_A_NEW, KEEP_PRICE_ALERT, ENDING_SOON,
        BID_READY, GLOBAL_LOT_ALERT, BID_RESULT, NIGHTLY_SUMMARY,
    )


# ================================================================
# 通知チャネル
# ================================================================

class NotificationChannel:
    SLACK     = "slack"
    NOTION    = "notion"
    DASHBOARD = "dashboard"

    ALL = (SLACK, NOTION, DASHBOARD)


# ================================================================
# DBテーブル名（ベタ書き禁止。ここで一元管理）
# ================================================================

class Table:
    # 既存テーブル
    MARKET_TRANSACTIONS      = "market_transactions"
    COIN_SLAB_DATA           = "coin_slab_data"
    DAILY_CANDIDATES         = "daily_candidates"
    CANDIDATE_DECISIONS      = "candidate_decisions"
    CANDIDATE_EVIDENCE       = "candidate_evidence"
    PRICING_SNAPSHOTS        = "pricing_snapshots"
    STATUS_CHECKS            = "status_checks"
    BIDDING_RECORDS          = "bidding_records"
    SHADOW_RUN_LOG           = "shadow_run_log"

    # Phase 2
    YAHOO_SOLD_LOTS_STAGING  = "yahoo_sold_lots_staging"
    YAHOO_SOLD_LOT_REVIEWS   = "yahoo_sold_lot_reviews"
    JOB_YAHOO_SOLD_SYNC      = "job_yahoo_sold_sync_daily"

    # Phase 4
    YAHOO_SOLD_LOTS          = "yahoo_sold_lots"          # 昇格済み本DB
    YAHOO_COIN_SEEDS         = "yahoo_coin_seeds"
    JOB_YAHOO_PROMOTER       = "job_yahoo_promoter_daily"
    JOB_SEED_GENERATOR       = "job_seed_generator_daily"

    # Phase 5
    EBAY_LISTINGS_RAW        = "ebay_listings_raw"
    EBAY_LISTING_SNAPSHOTS   = "ebay_listing_snapshots"
    EBAY_SEED_HITS           = "ebay_seed_hits"
    JOB_EBAY_INGEST          = "job_ebay_ingest_daily"
    JOB_EBAY_SCANNER         = "job_ebay_scanner_daily"

    # Phase 6
    GLOBAL_AUCTION_EVENTS    = "global_auction_events"
    GLOBAL_AUCTION_LOTS      = "global_auction_lots"
    GLOBAL_LOT_SNAPSHOTS     = "global_lot_price_snapshots"

    # Phase 7/8
    CANDIDATE_MATCH_RESULTS  = "candidate_match_results"
    CANDIDATE_WATCHLIST      = "candidate_watchlist"
    WATCHLIST_SNAPSHOTS      = "watchlist_snapshots"

    # Phase 9
    NOTIFICATION_LOG         = "notification_log"
    NEGOTIATE_LATER          = "negotiate_later"


# ================================================================
# Migration 適用順（Supabase SQL Editor で順番通りに実行）
# ================================================================

MIGRATION_ORDER = [
    "012_yahoo_staging.sql",
    "013_yahoo_seeds.sql",
    "014_ebay_listing_tables.sql",
    "015_global_auction_tables.sql",
    "016_match_audit_watch.sql",
    "017_notifications_negotiate.sql",
]


# ================================================================
# 利益計算定数（CEO確定）
# ※ coin_business/docs/project_coin_profit_formula.md に依拠
# ================================================================

class ProfitCalc:
    # 粗利率下限
    MIN_GROSS_MARGIN      = 0.15   # 15%

    # コスト内訳
    CUSTOMS_DUTY_RATE     = 1.10   # 関税 × 1.1
    US_FORWARDING_JPY     = 2000   # 米国転送費
    DOMESTIC_SHIPPING_JPY = 750    # 国内送料
    YAHOO_AUCTION_FEE     = 0.10   # ヤフオク手数料 10%

    # 為替レートのフォールバック（最新は daily_rates テーブルから取得）
    USD_TO_JPY_FALLBACK = 150


# ================================================================
# ジョブスケジュール（時刻は日本時間 JST）
# ================================================================

JOB_SCHEDULE = {
    "yahoo_sold_sync":     "06:00",  # Phase 2: Yahoo!staging 同期
    "yahoo_promoter":      "06:30",  # Phase 4: 承認済み Yahoo!昇格
    "seed_generator":      "07:00",  # Phase 4: seed 生成
    "global_auction_sync": "07:30",  # Phase 6: 世界オークション event 同期
    "ebay_seed_scanner":   "08:00",  # Phase 5/6: eBay seed スキャン (1回目)
    "global_lot_ingest":   "08:30",  # Phase 6: lot 収集
    "match_engine":        "09:00",  # Phase 7: マッチング
    "cap_audit_runner":    "09:30",  # Phase 7: CAP 監査
    "morning_brief_slack": "08:00",  # Phase 9: 朝ブリーフ
    "ebay_seed_scanner_2": "14:00",  # Phase 5/6: eBay seed スキャン (2回目)
    "ebay_seed_scanner_3": "20:00",  # Phase 5/6: eBay seed スキャン (3回目)
    "keep_watch_refresh":  "*/10",   # Phase 8: KEEP watchlist (10分ごと)
    "nightly_ops":         "03:00",  # 既存: nightly ops
}
