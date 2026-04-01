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
import re
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


# ── 鑑定会社・鑑定番号 抽出 ──────────────────────────────────────────

def extract_cert_info(lot_title: str) -> tuple[str | None, str | None]:
    """
    lot_title から grading_company と cert_number を抽出する。

    Returns:
        (grading_company, cert_number)
        grading_company: 'NGC' | 'PCGS' | None
        cert_number    : 文字列 or None

    検出パターン (優先順):
      1. Cert# XXXXXXX / cert XXXXXXX
      2. # XXXXXXX
      3. NGC/PCGS GRADE XXXXXXX (グレードの直後に来る数字)
      4. タイトル末尾の7〜10桁数字 (例: NGC MS63 4053419)
      5. 文字プレフィクス付き: L24401, P0001234 など (NGC/PCGS の旧フォーマット)
    """
    if not lot_title:
        return None, None

    title = lot_title.strip()

    # grading_company 検出
    ngc_m  = re.search(r'\bNGC\b',  title, re.IGNORECASE)
    pcgs_m = re.search(r'\bPCGS\b', title, re.IGNORECASE)
    if not ngc_m and not pcgs_m:
        return None, None

    grading_company = 'NGC' if ngc_m else 'PCGS'

    # cert_number 抽出パターン (優先順)
    patterns = [
        # 1. 明示的ラベル: cert# / cert / certification#
        r'\bcert(?:ification)?(?:\s*#\s*|\s+)(\d{6,10}(?:-\d{1,4})?)\b',
        # 2. # + 数字
        r'#\s*(\d{7,10}(?:-\d{1,4})?)\b',
        # 3. グレードの直後: NGC MS63 4053419 / PCGS MS70-0001 12345678
        r'\b(?:NGC|PCGS)\s+[A-Z]{1,3}[-+]?\s*\d{1,3}(?:/\d+)?\s+(\d{7,10}(?:-\d{1,4})?)\b',
        # 4. タイトル末尾7〜10桁
        r'\b(\d{7,10}(?:-\d{1,4})?)\s*$',
        # 5. 文字プレフィクス付き旧形式: L24401 / P000123456
        r'\b([A-Z]\d{5,9})\b',
    ]

    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if not m:
            continue
        cert = m.group(1).strip()
        # 除外: 4桁年号 (例: 1914, 2024)
        if re.fullmatch(r'\d{4}', cert):
            continue
        # 除外: 重量・サイズ系の短い数字
        if re.fullmatch(r'\d{1,3}', cert):
            continue
        return grading_company, cert

    # grading_company は検出できたが cert_number なし
    return grading_company, None


# ── dedup_key 生成 ─────────────────────────────────────────────────

def make_lot_dedup_key(source: str, auction_id: str, lot_number: str,
                       lot_title: str, current_price: float) -> str:
    """
    海外オークションロット用の dedup_key を生成。
    同一ロットが複数回取得されても重複しない。
    """
    raw = f"{source}|{auction_id}|{lot_number}|{lot_title[:40]}|{int(current_price or 0)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:32]


# ── coin_slab_data から 4カラム取得 ──────────────────────────────

def _fetch_ref_columns(management_nos: list[str]) -> dict:
    """
    coin_slab_data から ref1/ref2 の仕入上限4カラムを取得し、
    management_no → {ref1_buy_limit_20k_jpy, ...} のルックアップを返す。
    """
    if not management_nos:
        return {}
    try:
        from scripts.supabase_client import get_client
    except ModuleNotFoundError:
        from supabase_client import get_client
    client = get_client()
    mgmt_list = [m for m in management_nos if m]
    if not mgmt_list:
        return {}
    try:
        resp = (client.table("coin_slab_data")
                .select("management_no,"
                        "ref1_buy_limit_20k_jpy,ref1_buy_limit_15pct_jpy,"
                        "ref2_buy_limit_20k_jpy,ref2_buy_limit_15pct_jpy")
                .in_("management_no", mgmt_list)
                .execute())
        lookup: dict = {}
        for row in (resp.data or []):
            mgmt = row.get("management_no")
            if mgmt:
                lookup[mgmt] = {
                    "ref1_buy_limit_20k_jpy":   row.get("ref1_buy_limit_20k_jpy"),
                    "ref1_buy_limit_15pct_jpy": row.get("ref1_buy_limit_15pct_jpy"),
                    "ref2_buy_limit_20k_jpy":   row.get("ref2_buy_limit_20k_jpy"),
                    "ref2_buy_limit_15pct_jpy": row.get("ref2_buy_limit_15pct_jpy"),
                }
        logger.info(f"  [candidates_writer] ref_columns取得: {len(lookup)}件")
        return lookup
    except Exception as e:
        logger.warning(f"  [candidates_writer] ref_columns取得エラー: {e}")
        return {}


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

    # grading_company / cert_number 抽出 (lot_title から)
    lot_title_str = lot.get("lot_title") or ""
    _gc, _cert = extract_cert_info(lot_title_str)
    # lot 側に明示値があれば優先
    grading_company = lot.get("grading_company") or _gc
    cert_number     = lot.get("cert_number")     or _cert

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
        # ceo_decision: unmatched案件はCEO確認に上げない
        "ceo_decision":         lot.get("ceo_decision_override") or "pending",
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
        # ── 4カラム追加 (coin_slab_data から引用) ─────────────────
        "ref1_buy_limit_20k_jpy":   lot.get("ref1_buy_limit_20k_jpy"),
        "ref1_buy_limit_15pct_jpy": lot.get("ref1_buy_limit_15pct_jpy"),
        "ref2_buy_limit_20k_jpy":   lot.get("ref2_buy_limit_20k_jpy"),
        "ref2_buy_limit_15pct_jpy": lot.get("ref2_buy_limit_15pct_jpy"),
        # ── 鑑定番号 (migration 011_cert_columns.sql 適用後に有効) ──
        "grading_company":          grading_company,
        "cert_number":              cert_number,
    }


# ── メイン書き込み関数 ────────────────────────────────────────────

def _audit_lots(lots: list[dict], stage: str) -> dict:
    """
    ロットリストの品質を監査し、混入チェック結果をログ出力する。

    Returns:
        dict: 監査カウント {"banknote": int, "no_mgmt": int, "no_ref1": int, "total": int}
    """
    try:
        from scripts.fetch_noble_noonans_spink import is_non_coin_lot
    except ImportError:
        try:
            from fetch_noble_noonans_spink import is_non_coin_lot
        except ImportError:
            is_non_coin_lot = lambda t: (False, "")  # フォールバック

    banknote_count = 0
    no_mgmt_count  = 0
    no_ref1_count  = 0

    for lot in lots:
        title = lot.get("lot_title", "")
        is_non, _ = is_non_coin_lot(title)
        if is_non:
            banknote_count += 1
            logger.warning(f"  [AUDIT] 紙幣混入検出: {title[:60]}")
        if not lot.get("management_no"):
            no_mgmt_count += 1
        if lot.get("ref1_buy_limit_20k_jpy") is None:
            no_ref1_count += 1

    logger.info(
        f"  ╔══ 監査レポート [{stage}] ══════════════════════╗\n"
        f"  ║ 入力総数      : {len(lots):>4}件\n"
        f"  ║ 紙幣混入      : {banknote_count:>4}件  ← 要除外\n"
        f"  ║ 管理番号未登録: {no_mgmt_count:>4}件  ← CEO確認対象外\n"
        f"  ║ ref1未算出    : {no_ref1_count:>4}件  ← CEO確認対象外\n"
        f"  ╚════════════════════════════════════════════════╝"
    )
    return {
        "banknote":  banknote_count,
        "no_mgmt":   no_mgmt_count,
        "no_ref1":   no_ref1_count,
        "total":     len(lots),
    }


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
               "error": int, "total": int, "excluded": int,
               "audit": dict, "notified": dict}
    """
    from scripts.supabase_client import get_client
    from scripts.action_notifier import decide_judgment, notify_batch

    counts = {
        "ok": 0, "review": 0, "ceo": 0, "ng": 0,
        "error": 0, "total": len(lots), "excluded": 0,
    }

    if not lots:
        logger.info("  [candidates_writer] 候補なし — スキップ")
        return counts

    # ── Step 0-A: 取得段階フィルタ（紙幣・非硬貨を除外） ──────────────
    try:
        from scripts.fetch_noble_noonans_spink import is_non_coin_lot
    except ImportError:
        try:
            from fetch_noble_noonans_spink import is_non_coin_lot
        except ImportError:
            is_non_coin_lot = lambda t: (False, "")

    pre_total = len(lots)
    coin_lots = []
    banknote_lots = []
    for lot in lots:
        is_non, reason = is_non_coin_lot(lot.get("lot_title", ""))
        if is_non:
            banknote_lots.append(lot)
            logger.warning(f"  [除外] 紙幣/非硬貨: {lot.get('lot_title','')[:60]}")
        else:
            coin_lots.append(lot)

    counts["excluded"] += len(banknote_lots)
    if banknote_lots:
        logger.warning(
            f"  [candidates_writer] ⚠ 紙幣/非硬貨を除外: "
            f"{len(banknote_lots)}件 (入力{pre_total}件 → 残{len(coin_lots)}件)"
        )
    lots = coin_lots

    # ── Step 0-B: coin_slab_data から 4カラムをエンリッチ ─────────────
    mgmt_nos = [lot.get("management_no") for lot in lots]
    ref_lookup = _fetch_ref_columns([m for m in mgmt_nos if m])
    if ref_lookup:
        for lot in lots:
            mgmt = lot.get("management_no")
            if mgmt and mgmt in ref_lookup:
                lot.update(ref_lookup[mgmt])

    # ── Step 0-C: 管理番号・ref1 なし案件を分離 ──────────────────────
    qualified = []   # CEO確認対象（management_no + ref1 あり）
    unmatched = []   # 管理番号なし or ref1なし（DB記録するが CEO確認に上げない）
    for lot in lots:
        has_mgmt = bool(lot.get("management_no"))
        has_ref1 = lot.get("ref1_buy_limit_20k_jpy") is not None
        if has_mgmt and has_ref1:
            qualified.append(lot)
        else:
            lot["coin_match_status"] = lot.get("coin_match_status") or "unmatched"
            lot["ceo_skip"] = True   # CEO確認スキップフラグ
            unmatched.append(lot)

    logger.info(
        f"  [candidates_writer] 品質フィルタ: "
        f"CEO確認対象={len(qualified)}件 / 除外(unmatched)={len(unmatched)}件"
    )

    # ── Step 0-D: 監査ログ出力 ──────────────────────────────────────
    audit = _audit_lots(lots, stage="write_candidates後")
    counts["audit"] = audit
    counts["total"] = len(lots)

    # ── Step 1: 判定 ─────────────────────────────────────────────
    ok_list     = []
    review_list = []
    ceo_list    = []
    ng_list     = []
    records     = []

    for lot in lots:
        yahoo_3m = int(lot.get("yahoo_3m_count") or 0)
        judgment, reason = decide_judgment({**lot, "yahoo_3m_count": yahoo_3m})
        lot["judgment"]        = judgment
        lot["judgment_reason"] = reason

        # unmatched は ceo_decision を "unmatched" に設定してCEO確認に上げない
        if lot.get("ceo_skip"):
            lot["ceo_decision_override"] = "unmatched"

        if judgment == "OK" and not lot.get("ceo_skip"):
            ok_list.append(lot)
            counts["ok"] += 1
        elif judgment == "REVIEW" and not lot.get("ceo_skip"):
            review_list.append(lot)
            counts["review"] += 1
        elif judgment == "CEO判断" and not lot.get("ceo_skip"):
            ceo_list.append(lot)
            counts["ceo"] += 1
        else:
            ng_list.append(lot)
            counts["ng"] += 1

        records.append(_to_daily_candidate(lot))

    logger.info(
        f"  [candidates_writer] 判定完了: "
        f"OK={counts['ok']} / REVIEW={counts['review']} / "
        f"CEO判断={counts['ceo']} / NG={counts['ng']} / "
        f"除外済み={counts['excluded']}"
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


def update_ceo_decision(
    dedup_key: str,
    decision: str,
    ng_reason: str = None,
    comment: str = None,
) -> bool:
    """
    CEOの承認/NG判断を daily_candidates に保存する。

    Args:
        dedup_key : daily_candidates の dedup_key（一意識別子）
        decision  : 'approved' または 'rejected'
        ng_reason : NG理由テキスト（decision='rejected' 時）
        comment   : コメント（任意）

    Returns:
        bool: 成功時 True
    """
    if decision not in ("approved", "rejected"):
        logger.warning(f"update_ceo_decision: 無効な decision={decision!r}")
        return False
    try:
        from scripts.supabase_client import get_client
    except ModuleNotFoundError:
        from supabase_client import get_client

    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict = {
        "ceo_decision":    decision,
        "ceo_decided_at":  now_iso,
    }
    if ng_reason is not None:
        payload["ceo_ng_reason"] = ng_reason
    if comment is not None:
        payload["ceo_comment"] = comment

    try:
        client = get_client()
        resp = (client.table("daily_candidates")
                .update(payload)
                .eq("dedup_key", dedup_key)
                .execute())
        updated = len(resp.data or [])
        if updated == 0:
            logger.warning(f"update_ceo_decision: 対象レコードなし dedup_key={dedup_key[:8]}")
            return False
        logger.info(f"update_ceo_decision: {decision} → dedup_key={dedup_key[:8]}")
        return True
    except Exception as e:
        logger.error(f"update_ceo_decision error: {e}")
        return False


# ── bid_history CRUD ─────────────────────────────────────────

def load_bid_history(limit: int = 100) -> list[dict]:
    """bid_history テーブルから入札実績を取得（新しい順）。"""
    try:
        from scripts.supabase_client import get_client
    except ModuleNotFoundError:
        from supabase_client import get_client
    try:
        client = get_client()
        r = (client.table("bid_history")
             .select("*")
             .order("bid_date", desc=True)
             .order("created_at", desc=True)
             .limit(limit)
             .execute())
        return r.data or []
    except Exception as e:
        logger.warning(f"load_bid_history error: {e}")
        return []


def save_bid_entry(data: dict) -> Optional[str]:
    """
    入札実績を bid_history へ保存する。

    Args:
        data: bid_history カラムに対応する dict
              必須: lot_title
              任意: auction_house, lot_url, lot_number, management_no,
                    bid_date, auction_end_at, our_bid_usd, our_bid_jpy,
                    result, final_price_usd, final_price_jpy,
                    actual_cost_jpy, resell_price_jpy, actual_profit_jpy,
                    screenshot_path, notes, recommended_by

    Returns:
        str: 生成された UUID (id) または None（失敗時）
    """
    try:
        from scripts.supabase_client import get_client
    except ModuleNotFoundError:
        from supabase_client import get_client

    payload = {k: v for k, v in data.items() if v is not None and v != ""}
    payload.setdefault("result", "scheduled")
    payload.setdefault("recommended_by", "cap")

    try:
        client = get_client()
        r = client.table("bid_history").insert(payload).execute()
        if r.data:
            new_id = r.data[0].get("id")
            logger.info(f"save_bid_entry: 保存完了 id={new_id} title={data.get('lot_title','')[:30]}")
            return new_id
        return None
    except Exception as e:
        logger.error(f"save_bid_entry error: {e}")
        return None


def update_bid_entry(bid_id: str, data: dict) -> bool:
    """bid_history の既存レコードを更新する（結果入力・価格更新等）。"""
    try:
        from scripts.supabase_client import get_client
    except ModuleNotFoundError:
        from supabase_client import get_client

    payload = {k: v for k, v in data.items() if v is not None}
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client = get_client()
        r = client.table("bid_history").update(payload).eq("id", bid_id).execute()
        updated = len(r.data or [])
        logger.info(f"update_bid_entry: {updated}件更新 id={bid_id[:8]}")
        return updated > 0
    except Exception as e:
        logger.error(f"update_bid_entry error: {e}")
        return False


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
