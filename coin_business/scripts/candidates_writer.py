"""
candidates_writer.py

overseas_lot スキーマのレコードを daily_candidates テーブルへ upsert し、
action_notifier (Layer 4) を呼び出すパイプライン。

フロー:
  [overseas_lot dict] → 判定 (action_notifier) → DB upsert → Slack通知

使い方:
  from scripts.candidates_writer import write_candidates

  written = write_candidates(lots, dry_run=False)
  # → {"ok": 3, "review": 2, "ceo": 1, "ng": 5, "error": 0}
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


# ── dedup_key 生成 ─────────────────────────────────────────────────

def make_lot_dedup_key(source: str, auction_id: str, lot_number: str,
                       lot_title: str, current_price: float) -> str:
    """
    海外オークションロット用の dedup_key を生成。
    同一ロットが複数回取得されても重複しない。
    """
    raw = f"{source}|{auction_id}|{lot_number}|{lot_title[:40]}|{int(current_price or 0)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:32]


# ── overseas_lot → daily_candidates 変換 ─────────────────────────

def _to_daily_candidate(lot: dict) -> dict:
    """
    overseas_lot スキーマ (dict) を daily_candidates テーブル形式に変換。

    既存カラム (DDL 001_initial_schema.sql):
      report_date, rank_position, tier, source, current_price,
      estimated_buy_price, estimated_sell_price, expected_profit,
      profit_rate, gross_rank, decision_factors, ai_comment, ceo_decision

    追加カラム (migrations/overseas_auction_fields):
      management_no, auction_house, auction_id, lot_title, lot_number,
      lot_url, currency, fx_rate, lot_end_time, coin_match_status,
      match_score, judgment, judgment_reason, buy_limit_jpy,
      estimated_cost_jpy, estimated_margin_pct, is_active_auction,
      priority, dedup_key, notified, status
    """
    today = date.today().isoformat()

    # dedup_key (未設定の場合は生成)
    dedup_key = lot.get("dedup_key") or make_lot_dedup_key(
        source       = lot.get("source", "unknown"),
        auction_id   = lot.get("auction_id", ""),
        lot_number   = lot.get("lot_number", ""),
        lot_title    = lot.get("lot_title", ""),
        current_price = float(lot.get("current_price") or 0),
    )

    # 利益計算
    cost_jpy    = float(lot.get("estimated_cost_jpy") or 0)
    buy_limit   = float(lot.get("buy_limit_jpy") or 0)
    expected_profit = int(buy_limit - cost_jpy) if (buy_limit > 0 and cost_jpy > 0) else None
    profit_rate = float(lot.get("estimated_margin_pct") or 0) or None

    # decision_factors JSONB にメタ情報を格納
    decision_factors = {
        "source":         lot.get("source"),
        "auction_house":  lot.get("auction_house"),
        "match_score":    lot.get("match_score"),
        "yahoo_3m_count": lot.get("yahoo_3m_count"),
        "currency":       lot.get("currency"),
        "current_price":  lot.get("current_price"),
        "fx_rate":        lot.get("fx_rate"),
        "is_cac":         lot.get("is_cac"),
    }

    return {
        # ── 既存カラム ─────────────────────────────────────
        "report_date":          today,
        "rank_position":        lot.get("rank_position") or 999,
        "tier":                 lot.get("tier") or "overseas",
        "source":               lot.get("source") or "unknown",
        "current_price":        int(lot.get("price_jpy") or lot.get("estimated_cost_jpy") or 0) or None,
        "estimated_buy_price":  int(cost_jpy) if cost_jpy > 0 else None,
        "estimated_sell_price": int(buy_limit) if buy_limit > 0 else None,
        "expected_profit":      expected_profit,
        "profit_rate":          profit_rate,
        "gross_rank":           lot.get("judgment") or "pending",
        "decision_factors":     decision_factors,
        "ai_comment":           lot.get("judgment_reason"),
        "ceo_decision":         "pending",
        # ── 拡張カラム ─────────────────────────────────────
        "management_no":        lot.get("management_no"),
        "auction_house":        lot.get("auction_house"),
        "auction_id":           lot.get("auction_id"),
        "lot_title":            lot.get("lot_title"),
        "lot_number":           lot.get("lot_number"),
        "lot_url":              lot.get("lot_url"),
        "currency":             lot.get("currency") or "USD",
        "fx_rate":              float(lot.get("fx_rate") or 0) or None,
        "lot_end_time":         lot.get("lot_end_time"),
        "coin_match_status":    lot.get("coin_match_status") or "unmatched",
        "match_score":          float(lot.get("match_score") or 0) or None,
        "judgment":             lot.get("judgment") or "pending",
        "judgment_reason":      lot.get("judgment_reason"),
        "buy_limit_jpy":        int(buy_limit) if buy_limit > 0 else None,
        "estimated_cost_jpy":   int(cost_jpy) if cost_jpy > 0 else None,
        "estimated_margin_pct": profit_rate,
        "is_active_auction":    lot.get("is_active_auction", True),
        "priority":             int(lot.get("priority") or 1),
        "dedup_key":            dedup_key,
        "notified":             False,
        "status":               lot.get("status") or "pending",
    }


# ── メイン書き込み関数 ────────────────────────────────────────────

def write_candidates(
    lots: list[dict],
    dry_run: bool = False,
    skip_notify: bool = False,
) -> dict:
    """
    overseas_lot リストを daily_candidates へ書き込み、Layer 4 通知を行う。

    Args:
        lots         : overseas_lot スキーマの dict リスト
        dry_run      : True の場合は DB 書き込み・Slack通知をスキップ
        skip_notify  : True の場合は判定後のSlack通知のみスキップ

    Returns:
        dict: {"ok": int, "review": int, "ceo": int, "ng": int,
               "error": int, "total": int, "notified": dict}
    """
    from scripts.supabase_client import get_client
    from scripts.action_notifier import decide_judgment, notify_batch

    counts = {"ok": 0, "review": 0, "ceo": 0, "ng": 0, "error": 0, "total": len(lots)}

    if not lots:
        logger.info("  [candidates_writer] 候補なし — スキップ")
        return counts

    # ── Step 1: 判定 ─────────────────────────────────────────────
    ok_list    = []
    review_list = []
    ceo_list   = []
    ng_list    = []
    records    = []

    for lot in lots:
        yahoo_3m = int(lot.get("yahoo_3m_count") or 0)
        judgment, reason = decide_judgment({**lot, "yahoo_3m_count": yahoo_3m})
        lot["judgment"]        = judgment
        lot["judgment_reason"] = reason

        if judgment == "OK":
            ok_list.append(lot)
            counts["ok"] += 1
        elif judgment == "REVIEW":
            review_list.append(lot)
            counts["review"] += 1
        elif judgment == "CEO判断":
            ceo_list.append(lot)
            counts["ceo"] += 1
        else:
            ng_list.append(lot)
            counts["ng"] += 1

        records.append(_to_daily_candidate(lot))

    logger.info(
        f"  [candidates_writer] 判定完了: "
        f"OK={counts['ok']} / REVIEW={counts['review']} / "
        f"CEO判断={counts['ceo']} / NG={counts['ng']}"
    )

    # ── Step 2: DB upsert ────────────────────────────────────────
    if dry_run:
        logger.info(f"  [DRY-RUN] {len(records)}件 — daily_candidates upsert スキップ")
        for r in records[:3]:
            logger.info(
                f"    {r.get('judgment'):8} | {r.get('lot_title', '')[:40]} "
                f"| ¥{r.get('estimated_cost_jpy') or 0:,.0f}"
            )
    else:
        client = get_client()
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i: i + BATCH_SIZE]
            try:
                resp = client.table("daily_candidates").upsert(
                    batch, on_conflict="dedup_key"
                ).execute()
                logger.info(f"  [candidates_writer] upsert {len(resp.data)}件 (batch {i // BATCH_SIZE + 1})")
            except Exception as e:
                logger.warning(f"  [candidates_writer] upsert エラー: {e}")
                counts["error"] += len(batch)

    # ── Step 3: Layer 4 通知 ─────────────────────────────────────
    if skip_notify:
        logger.info("  [candidates_writer] Slack通知スキップ (skip_notify=True)")
    else:
        notified = notify_batch(ok_list, review_list, ceo_list, dry_run=dry_run)
        counts["notified"] = notified
        logger.info(
            f"  [candidates_writer] 通知完了: "
            f"OK={notified.get('ok_sent',0)} / "
            f"REVIEW={notified.get('review_sent',0)} / "
            f"CEO={notified.get('ceo_sent',0)}"
        )

    return counts


# ── daily_candidates 確認用 ──────────────────────────────────────

def get_pending_candidates(judgment: Optional[str] = None, limit: int = 20) -> list[dict]:
    """
    daily_candidates から status=pending の候補を取得。
    judgment 指定で絞り込み可能 (例: "OK" / "CEO判断")。
    """
    from scripts.supabase_client import get_client
    client = get_client()
    q = (client.table("daily_candidates")
         .select("management_no,lot_title,judgment,judgment_reason,"
                 "buy_limit_jpy,estimated_cost_jpy,estimated_margin_pct,"
                 "auction_house,lot_url,lot_end_time,priority,status")
         .eq("status", "pending")
         .order("priority", desc=True)
         .order("lot_end_time", desc=False)
         .limit(limit))
    if judgment:
        q = q.eq("judgment", judgment)
    try:
        return q.execute().data or []
    except Exception as e:
        logger.warning(f"  [candidates_writer] get_pending error: {e}")
        return []


def get_ceo_list(limit: int = 50) -> list[dict]:
    """CEO判断リストを取得（終了時刻順）。"""
    return get_pending_candidates(judgment="CEO判断", limit=limit)


def print_candidate_summary() -> None:
    """現在の daily_candidates 状況をコンソール出力。"""
    from scripts.supabase_client import get_client
    client = get_client()
    try:
        r = client.table("daily_candidates").select("judgment, status").execute()
        from collections import Counter
        j_counts = Counter(row.get("judgment") for row in r.data)
        s_counts = Counter(row.get("status") for row in r.data)
        total = len(r.data)
        print(f"\n=== daily_candidates ({total}件) ===")
        print("判定別:")
        for j in ["OK", "REVIEW", "CEO判断", "NG", "pending"]:
            if j_counts.get(j):
                print(f"  {j:8}: {j_counts[j]}件")
        print("ステータス別:")
        for s, cnt in s_counts.most_common():
            print(f"  {s or 'None':12}: {cnt}件")
    except Exception as e:
        logger.warning(f"print_candidate_summary error: {e}")
