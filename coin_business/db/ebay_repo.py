"""
coin_business/db/ebay_repo.py
================================
eBay listing の保存・取得リポジトリ層。

責務:
  - upsert_listing_raw()   : ebay_listings_raw に upsert (dedup key = ebay_item_id)
  - insert_snapshot()      : ebay_listing_snapshots に追記 (再取得履歴)
  - get_raw_by_item_id()   : ebay_item_id で 1 件取得
  - load_ready_seeds()     : READY 状態の yahoo_coin_seeds を取得
  - mark_seed_scanned()    : seed の scan_count / hit_count / next_scan_at を更新
  - record_ingest_run()    : job_ebay_ingest_daily にジョブ記録

設計原則:
  - raw テーブルは ebay_item_id で UPSERT (同一 item の重複防止)
  - snapshot テーブルは常に INSERT (時系列追跡)
  - API エラー時は例外を外に出さず False / None / [] を返す
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from constants import Table, SeedStatus

logger = logging.getLogger(__name__)

# ================================================================
# テーブル名
# ================================================================

RAW_TABLE       = Table.EBAY_LISTINGS_RAW       # "ebay_listings_raw"
SNAP_TABLE      = Table.EBAY_LISTING_SNAPSHOTS  # "ebay_listing_snapshots"
SEEDS_TABLE     = Table.YAHOO_COIN_SEEDS        # "yahoo_coin_seeds"
HITS_TABLE      = Table.EBAY_SEED_HITS          # "ebay_seed_hits"
JOB_TABLE       = Table.JOB_EBAY_INGEST         # "job_ebay_ingest_daily"
JOB_SCANNER     = Table.JOB_EBAY_SCANNER        # "job_ebay_scanner_daily"

# ================================================================
# スナップショット設定
# ================================================================

DEFAULT_COOLDOWN_HOURS = 24   # seed スキャン後のクールダウン時間

# ================================================================
# 公開 API
# ================================================================

def upsert_listing_raw(client, item: dict) -> Optional[str]:
    """
    ebay_listings_raw に listing を upsert する。

    Args:
        client: Supabase クライアント
        item:   EbayBrowseClient._normalize_item() が返す dict

    Returns:
        保存された UUID 文字列、失敗時は None
    """
    ebay_item_id = item.get("ebay_item_id")
    if not ebay_item_id:
        logger.warning("ebay_item_id が空のためスキップ")
        return None

    # DB に送るレコードを構築 (生 payload は文字列のまま送る)
    rec: dict = {}
    safe_fields = [
        "ebay_item_id", "title", "listing_url",
        "listing_type", "current_price_usd", "currency",
        "bid_count", "end_time", "start_time",
        "seller_id", "seller_username", "seller_feedback_score",
        "shipping_from_country", "image_url", "thumbnail_url",
        "condition", "is_active", "is_sold",
    ]
    for f in safe_fields:
        v = item.get(f)
        if v is not None:
            rec[f] = v

    # raw_payload は JSON 文字列として保存
    if "raw_payload" in item:
        rec["raw_payload"] = item["raw_payload"]

    # last_fetched_at を現在時刻で更新
    rec["last_fetched_at"] = datetime.now(timezone.utc).isoformat()

    try:
        resp = client.table(RAW_TABLE).upsert(
            rec,
            on_conflict="ebay_item_id",
        ).execute()
        if resp.data:
            return resp.data[0].get("id")
        return None
    except Exception as exc:
        logger.error("ebay_listings_raw upsert 失敗 item_id=%s: %s", ebay_item_id, exc)
        return None


def get_raw_by_item_id(client, ebay_item_id: str) -> Optional[dict]:
    """
    ebay_listings_raw から ebay_item_id で 1 件取得する。
    スナップショット差分計算に使う。
    """
    try:
        resp = (
            client.table(RAW_TABLE)
            .select("id, ebay_item_id, current_price_usd, bid_count, is_active, is_sold")
            .eq("ebay_item_id", ebay_item_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        logger.error("get_raw_by_item_id 失敗 item_id=%s: %s", ebay_item_id, exc)
        return None


def insert_snapshot(
    client,
    listing_id:   str,
    ebay_item_id: str,
    item:         dict,
    prev:         Optional[dict] = None,
) -> bool:
    """
    ebay_listing_snapshots に 1 行を INSERT する。

    Args:
        client:       Supabase クライアント
        listing_id:   ebay_listings_raw.id (UUID)
        ebay_item_id: eBay の item ID
        item:         新しいデータ (normalize_item の出力)
        prev:         前回 raw データ (差分計算用、省略可)

    Returns:
        True = 成功、False = 失敗
    """
    snap: dict = {
        "listing_id":   listing_id,
        "ebay_item_id": ebay_item_id,
        "is_active":    item.get("is_active", True),
        "is_sold":      item.get("is_sold",   False),
        "bid_count":    item.get("bid_count",  0),
    }

    price = item.get("current_price_usd")
    if price is not None:
        snap["price_usd"] = price

    # 差分計算 (前回データがある場合)
    if prev:
        prev_price = prev.get("current_price_usd")
        prev_bids  = prev.get("bid_count", 0)
        if price is not None and prev_price is not None:
            try:
                snap["price_delta_usd"] = round(float(price) - float(prev_price), 2)
            except (TypeError, ValueError):
                pass
        try:
            snap["bid_delta"] = int(item.get("bid_count", 0)) - int(prev_bids or 0)
        except (TypeError, ValueError):
            pass

    # 残時間 (end_time から計算)
    end_time = item.get("end_time")
    if end_time:
        try:
            end_dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            now    = datetime.now(timezone.utc)
            delta  = (end_dt - now).total_seconds()
            snap["time_left_seconds"] = max(0, int(delta))
        except (ValueError, TypeError):
            pass

    try:
        client.table(SNAP_TABLE).insert(snap).execute()
        return True
    except Exception as exc:
        logger.error("ebay_listing_snapshots insert 失敗 item_id=%s: %s",
                     ebay_item_id, exc)
        return False


# ================================================================
# seed 管理
# ================================================================

def load_ready_seeds(
    client,
    limit:      int = 50,
    seed_types: list[str] | None = None,
) -> list[dict]:
    """
    READY 状態 (next_scan_at が NULL または現在時刻以前) の seed を取得する。
    priority_score 降順で返す。

    Args:
        client:      Supabase クライアント
        limit:       最大取得件数
        seed_types:  seed_type フィルタ (None = 全種別)

    Returns:
        list of yahoo_coin_seeds レコード
    """
    try:
        q = (
            client.table(SEEDS_TABLE)
            .select(
                "id, yahoo_lot_id, seed_type, search_query, "
                "cert_company, cert_number, year_min, year_max, "
                "denomination, grade_min, grader, "
                "ref_price_jpy, ref_sold_date, "
                "priority_score, seed_status, next_scan_at, "
                "scan_count, hit_count"
            )
            .eq("seed_status", SeedStatus.READY)
            .order("priority_score", desc=True)
            .limit(limit)
        )
        if seed_types:
            q = q.in_("seed_type", seed_types)

        resp = q.execute()
        return resp.data or []
    except Exception as exc:
        logger.error("load_ready_seeds 失敗: %s", exc)
        return []


def mark_seed_scanning(client, seed_id: str) -> bool:
    """seed_status を SCANNING に更新する。"""
    try:
        client.table(SEEDS_TABLE).update(
            {"seed_status": SeedStatus.SCANNING}
        ).eq("id", seed_id).execute()
        return True
    except Exception as exc:
        logger.error("mark_seed_scanning 失敗 id=%s: %s", seed_id, exc)
        return False


def mark_seed_scanned(
    client,
    seed_id:           str,
    hit_count_delta:   int = 0,
    cooldown_hours:    float = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    """
    seed スキャン完了後に状態を更新する。

    処理内容:
      - seed_status: COOLDOWN
      - scan_count:  +1
      - hit_count:   +hit_count_delta
      - last_scanned_at: NOW()
      - next_scan_at: NOW() + cooldown_hours

    Args:
        client:          Supabase クライアント
        seed_id:         yahoo_coin_seeds.id
        hit_count_delta: このスキャンで新たに見つかった listing 数
        cooldown_hours:  クールダウン時間 (時間)

    Returns:
        True = 成功
    """
    now          = datetime.now(timezone.utc)
    next_scan_at = (now + timedelta(hours=cooldown_hours)).isoformat()

    try:
        # scan_count / hit_count は RPC がないと原子的に増やせないため
        # 現在値を取得してから更新する
        resp = (
            client.table(SEEDS_TABLE)
            .select("scan_count, hit_count")
            .eq("id", seed_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            logger.warning("seed が見つからない id=%s", seed_id)
            return False

        current = resp.data[0]
        new_scan_count = int(current.get("scan_count") or 0) + 1
        new_hit_count  = int(current.get("hit_count")  or 0) + hit_count_delta

        client.table(SEEDS_TABLE).update({
            "seed_status":    SeedStatus.COOLDOWN,
            "scan_count":     new_scan_count,
            "hit_count":      new_hit_count,
            "last_scanned_at": now.isoformat(),
            "next_scan_at":   next_scan_at,
        }).eq("id", seed_id).execute()
        return True

    except Exception as exc:
        logger.error("mark_seed_scanned 失敗 id=%s: %s", seed_id, exc)
        return False


def requeue_cooled_seeds(client) -> int:
    """
    next_scan_at が過去の COOLDOWN seed を READY に戻す。
    スケジューラから定期的に呼ぶ。

    Returns:
        READY に戻した件数
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        resp = (
            client.table(SEEDS_TABLE)
            .update({"seed_status": SeedStatus.READY})
            .eq("seed_status", SeedStatus.COOLDOWN)
            .lte("next_scan_at", now)
            .execute()
        )
        count = len(resp.data) if resp.data else 0
        if count:
            logger.info("COOLDOWN → READY に移行: %d 件", count)
        return count
    except Exception as exc:
        logger.error("requeue_cooled_seeds 失敗: %s", exc)
        return 0


# ================================================================
# seed hit 管理
# ================================================================

def upsert_seed_hit(
    client,
    seed_id:       str,
    listing_id:    str,
    ebay_item_id:  str,
    match_score:   float,
    match_type:    str,
    matched_query: str,
    hit_rank:      int,
    hit_reason:    str,
    match_details: dict | None = None,
) -> Optional[str]:
    """
    ebay_seed_hits に hit レコードを UPSERT する。

    同一 (seed_id, listing_id) は重複しない (UNIQUE 制約)。
    既存 hit はスコア・ランク等を更新する。

    Returns:
        保存された UUID 文字列、失敗時は None
    """
    import json as _json
    rec: dict = {
        "seed_id":       seed_id,
        "listing_id":    listing_id,
        "ebay_item_id":  ebay_item_id,
        "match_score":   round(match_score, 3),
        "match_type":    match_type,
        "matched_query": matched_query[:500] if matched_query else None,
        "hit_rank":      hit_rank,
        "hit_reason":    hit_reason,
    }
    if match_details:
        rec["match_details"] = _json.dumps(match_details, ensure_ascii=False)

    try:
        resp = client.table(HITS_TABLE).upsert(
            rec,
            on_conflict="seed_id,listing_id",
        ).execute()
        if resp.data:
            return resp.data[0].get("id")
        return None
    except Exception as exc:
        logger.error(
            "ebay_seed_hits upsert 失敗 seed=%s item=%s: %s",
            seed_id, ebay_item_id, exc,
        )
        return None


def get_existing_hit_listing_ids(client, seed_id: str) -> set[str]:
    """
    seed_id に対して既に登録済みの listing_id セットを返す。
    重複 hit 抑止に使う。

    Returns:
        既存 hit の listing_id set (空なら空 set)
    """
    try:
        resp = (
            client.table(HITS_TABLE)
            .select("listing_id")
            .eq("seed_id", seed_id)
            .execute()
        )
        return {row["listing_id"] for row in (resp.data or [])}
    except Exception as exc:
        logger.error("get_existing_hit_listing_ids 失敗 seed=%s: %s", seed_id, exc)
        return set()


# ================================================================
# ジョブ記録
# ================================================================

def record_ingest_run(
    client,
    run_date:         str,
    status:           str,
    seeds_scanned:    int = 0,
    listings_fetched: int = 0,
    listings_saved:   int = 0,
    snapshots_saved:  int = 0,
    error_count:      int = 0,
    error_message:    Optional[str] = None,
) -> bool:
    """
    job_ebay_ingest_daily にジョブ実行記録を insert する。

    Args:
        run_date:  "YYYY-MM-DD"
        status:    "ok" | "partial" | "error"
    """
    try:
        record: dict = {
            "run_date":         run_date,
            "status":           status,
            "seeds_scanned":    seeds_scanned,
            "listings_fetched": listings_fetched,
            "listings_saved":   listings_saved,
            "snapshots_saved":  snapshots_saved,
            "error_count":      error_count,
        }
        if error_message:
            record["error_message"] = error_message[:2000]
        client.table(JOB_TABLE).insert(record).execute()
        return True
    except Exception as exc:
        logger.error("ingest ジョブ記録失敗: %s", exc)
        return False


def record_scanner_run(
    client,
    run_date:      str,
    status:        str,
    seeds_scanned: int = 0,
    hits_found:    int = 0,
    hits_saved:    int = 0,
    error_count:   int = 0,
    error_message: Optional[str] = None,
) -> bool:
    """
    job_ebay_scanner_daily にスキャナー実行記録を insert する。

    Args:
        run_date:  "YYYY-MM-DD"
        status:    "ok" | "partial" | "error"
    """
    try:
        record: dict = {
            "run_date":      run_date,
            "status":        status,
            "seeds_scanned": seeds_scanned,
            "hits_found":    hits_found,
            "hits_saved":    hits_saved,
            "error_count":   error_count,
        }
        if error_message:
            record["error_message"] = error_message[:2000]
        client.table(JOB_SCANNER).insert(record).execute()
        return True
    except Exception as exc:
        logger.error("scanner ジョブ記録失敗: %s", exc)
        return False
