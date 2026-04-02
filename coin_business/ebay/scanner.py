"""
coin_business/ebay/scanner.py
================================
Yahoo seed 起点の eBay seed スキャナー。

責務:
  - yahoo_coin_seeds の READY seed を読み込む
  - 各 seed に対して EbayBrowseClient.search_by_seed() を実行する
  - 取得した listing を ebay_listings_raw に upsert する
  - seed × listing のマッチを ebay_seed_hits に記録する (重複 skip)
  - seed status を SCANNING → COOLDOWN に遷移させる
  - priority に応じた cadence (next_scan_at) を設定する

Flow:
  SeedScanner.run()
    → requeue_cooled_seeds()            (COOLDOWN → READY 戻し)
    → load_ready_seeds()                (READY seed 取得)
    → for each seed:
        scan_seed(seed)
          → mark_seed_scanning()
          → get_existing_hit_listing_ids()  (dedup)
          → ebay_client.search_by_seed()
          → upsert_listing_raw()
          → upsert_seed_hit()           (UNIQUE skip)
          → mark_seed_scanned(cooldown = ScannerCadence.cooldown_hours(seed_type))
    → record_scanner_run()

設計原則:
  - scanner は seed status 遷移に責任を持つ
  - client は eBay との通信のみ
  - repo は DB 操作のみ
  - hit_reason は seed_type から決定論的に生成する
  - 同一 seed/item の重複は UNIQUE 制約 + 事前チェックの 2 層で防ぐ
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from constants import SeedType, ScannerCadence
from ebay.client import EbayBrowseClient
from db.ebay_repo import (
    load_ready_seeds,
    mark_seed_scanning,
    mark_seed_scanned,
    upsert_listing_raw,
    get_existing_hit_listing_ids,
    upsert_seed_hit,
    requeue_cooled_seeds,
    record_scanner_run,
)

logger = logging.getLogger(__name__)


# ================================================================
# 定数
# ================================================================

# seed_type → hit_reason マッピング
_HIT_REASON: dict[str, str] = {
    SeedType.CERT_EXACT:       "cert_number_match",
    SeedType.CERT_TITLE:       "cert_title_match",
    SeedType.TITLE_NORMALIZED: "title_normalized",
    SeedType.YEAR_DENOM_GRADE: "year_denom_grade",
}

# seed_type → match_type マッピング (ebay_seed_hits.match_type)
_MATCH_TYPE: dict[str, str] = {
    SeedType.CERT_EXACT:       "cert_exact",
    SeedType.CERT_TITLE:       "cert_title",
    SeedType.TITLE_NORMALIZED: "title_fuzzy",
    SeedType.YEAR_DENOM_GRADE: "year_grade",
}


# ================================================================
# 結果データクラス
# ================================================================

@dataclass
class SeedScanResult:
    """1 seed のスキャン結果。"""
    seed_id:   str
    seed_type: str
    query:     str
    fetched:   int = 0   # API から取得した listing 数
    saved:     int = 0   # ebay_listings_raw に保存した数
    hit_new:   int = 0   # ebay_seed_hits に新規追加した数
    hit_skip:  int = 0   # 重複 skip した数
    error:     bool = False
    error_msg: str  = ""


@dataclass
class ScanRunResult:
    """run() 全体の結果。"""
    seeds_scanned: int = 0
    hits_found:    int = 0   # API 取得総件数
    hits_saved:    int = 0   # seed_hits 新規保存数
    error_count:   int = 0
    errors:        list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def status_str(self) -> str:
        if self.error_count == 0:
            return "ok"
        if self.hits_saved > 0:
            return "partial"
        return "error"


# ================================================================
# SeedScanner
# ================================================================

class SeedScanner:
    """
    Yahoo seed 起点の eBay スキャナー。

    Usage:
        scanner = SeedScanner(supabase_client)
        result = scanner.run(limit=50)

    Args:
        client:      Supabase クライアント
        ebay_client: EbayBrowseClient インスタンス (省略時は自動生成)
    """

    def __init__(self, client, ebay_client: EbayBrowseClient | None = None):
        self._db   = client
        self._ebay = ebay_client or EbayBrowseClient()

    # ----------------------------------------------------------------
    # 公開 API
    # ----------------------------------------------------------------

    def run(
        self,
        limit:      int = 50,
        dry_run:    bool = False,
        seed_types: list[str] | None = None,
        seed_limit: int = 50,
    ) -> ScanRunResult:
        """
        READY seed を順番にスキャンする。

        Args:
            limit:      処理する seed の最大数
            dry_run:    True = DB 書き込みなし
            seed_types: 絞り込む seed_type リスト (None = 全種別)
            seed_limit: 1 seed あたりの listing 取得件数

        Returns:
            ScanRunResult
        """
        result = ScanRunResult()

        if not self._ebay.is_configured:
            msg = "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が未設定 — スキャンをスキップ"
            logger.warning(msg)
            result.errors.append(msg)
            result.error_count += 1
            return result

        # COOLDOWN 終了 seed を READY に戻す
        if not dry_run:
            requeued = requeue_cooled_seeds(self._db)
            if requeued:
                logger.info("COOLDOWN → READY: %d 件", requeued)

        # READY seed を取得
        seeds = load_ready_seeds(self._db, limit=limit, seed_types=seed_types)
        if not seeds:
            logger.info("READY 状態の seed がありません — 終了")
            return result

        logger.info("対象 seed: %d 件 (dry_run=%s)", len(seeds), dry_run)

        for seed in seeds:
            sr = self.scan_seed(seed, dry_run=dry_run, seed_limit=seed_limit)
            result.seeds_scanned += 1
            result.hits_found    += sr.fetched
            result.hits_saved    += sr.hit_new
            if sr.error:
                result.error_count += 1
                result.errors.append(sr.error_msg)

        return result

    def scan_seed(
        self,
        seed:       dict,
        dry_run:    bool = False,
        seed_limit: int  = 50,
    ) -> SeedScanResult:
        """
        1 seed をスキャンして hit を保存する。

        Args:
            seed:       yahoo_coin_seeds のレコード
            dry_run:    True = DB 書き込みなし
            seed_limit: listing 取得件数上限

        Returns:
            SeedScanResult
        """
        seed_id   = seed.get("id", "?")
        seed_type = seed.get("seed_type", SeedType.YEAR_DENOM_GRADE)
        query     = seed.get("search_query", "")

        sr = SeedScanResult(seed_id=seed_id, seed_type=seed_type, query=query)

        if not query:
            logger.warning("seed [%s] search_query が空 — スキップ", seed_id)
            sr.error     = True
            sr.error_msg = f"seed={seed_id} search_query が空"
            return sr

        # SCANNING に更新
        if not dry_run:
            mark_seed_scanning(self._db, seed_id)

        # 既存 hit の listing_id セットを取得 (dedup)
        existing_listing_ids: set[str] = set()
        if not dry_run:
            existing_listing_ids = get_existing_hit_listing_ids(self._db, seed_id)

        # eBay 検索
        try:
            items = self._ebay.search_by_seed(seed, limit=seed_limit)
        except Exception as exc:
            msg = f"seed={seed_id} 検索例外: {exc}"
            logger.error(msg)
            sr.error     = True
            sr.error_msg = msg
            if not dry_run:
                mark_seed_scanned(self._db, seed_id, hit_count_delta=0,
                                  cooldown_hours=ScannerCadence.cooldown_hours(seed_type))
            return sr

        sr.fetched = len(items)
        logger.info("Seed [%s] %s: fetched=%d", seed_id, seed_type, sr.fetched)

        match_type = _MATCH_TYPE.get(seed_type, "title_fuzzy")
        hit_reason = _HIT_REASON.get(seed_type, "year_denom_grade")
        match_score = float(
            SeedType.PRIORITY.get(seed_type, 0.3)
        )

        for rank, item in enumerate(items, start=1):
            ebay_item_id = item.get("ebay_item_id", "")

            if dry_run:
                logger.debug("  [DRY-RUN] rank=%d %s %s",
                             rank, ebay_item_id, item.get("title", "")[:50])
                sr.saved  += 1
                sr.hit_new += 1
                continue

            # ebay_listings_raw に upsert
            listing_id = upsert_listing_raw(self._db, item)
            if not listing_id:
                logger.warning("  upsert_listing_raw 失敗 item_id=%s", ebay_item_id)
                continue
            sr.saved += 1

            # 重複 skip (事前チェック)
            if listing_id in existing_listing_ids:
                sr.hit_skip += 1
                logger.debug("  [SKIP] 既存 hit listing_id=%s", listing_id)
                continue

            # ebay_seed_hits に upsert
            hit_id = upsert_seed_hit(
                client        = self._db,
                seed_id       = seed_id,
                listing_id    = listing_id,
                ebay_item_id  = ebay_item_id,
                match_score   = match_score,
                match_type    = match_type,
                matched_query = query,
                hit_rank      = rank,
                hit_reason    = hit_reason,
                match_details = {
                    "query":      query,
                    "seed_type":  seed_type,
                    "hit_rank":   rank,
                    "item_title": item.get("title", ""),
                },
            )
            if hit_id:
                sr.hit_new += 1
                existing_listing_ids.add(listing_id)  # 同一 run での重複防止
            else:
                sr.hit_skip += 1  # UNIQUE 制約 conflict (DB 側)

        # COOLDOWN に更新
        if not dry_run:
            cooldown = ScannerCadence.cooldown_hours(seed_type)
            mark_seed_scanned(
                self._db,
                seed_id,
                hit_count_delta = sr.hit_new,
                cooldown_hours  = cooldown,
            )

        logger.info(
            "  完了: fetched=%d saved=%d hit_new=%d hit_skip=%d cooldown=%dh",
            sr.fetched, sr.saved, sr.hit_new, sr.hit_skip,
            ScannerCadence.cooldown_hours(seed_type),
        )
        return sr
