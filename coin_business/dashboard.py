# dashboard.py  -- Day14 CEO確認ダッシュボード完成版
"""
起動:
    streamlit run coin_business/dashboard.py
    python -m streamlit run coin_business/dashboard.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from scripts.supabase_client import get_supabase_client
from constants import YahooStagingStatus
from scripts.decision_logger import save_ceo_decision
from scripts.evidence_builder import (
    get_candidate_evidence,
    group_candidate_evidence,
    evidence_summary,
    build_candidate_evidence_bundle,
)
from scripts.pricing_engine import get_latest_pricing_snapshot
from scripts.eligibility_rules import evaluate_candidate_eligibility
from scripts.status_refresher import (
    get_latest_status_check,
    is_stale,
    refresh_candidate_status,
)
from scripts.review_queue import compose_ceo_review_payload, get_review_queue
from scripts.bid_recorder import (
    queue_candidate_for_bid,
    update_bid_record_status,
    list_bid_records,
)

st.set_page_config(
    page_title="Coin Business Dashboard",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# Constants
# =========================================================

APP_TITLE = "🪙 Coin Business CEO Dashboard"
REVIEW_NG_LABEL = "旧NGだが再確認候補"

AUTO_TIER_COLOR = {
    "AUTO_PASS":   "#16a34a",
    "AUTO_REVIEW": "#d97706",
    "AUTO_REJECT": "#dc2626",
    None:          "#6b7280",
}

EVIDENCE_TYPE_LABEL = {
    "source_listing":    "Source",
    "cert_verification": "Cert",
    "yahoo_comp":        "Yahoo",
    "heritage_comp":     "Heritage",
    "spink_comp":        "Spink",
    "numista_ref":       "Numista",
    "image":             "Image",
    "note":              "Note",
}

NG_REASON_CATEGORIES = [
    "価格オーバー",
    "グレード不足",
    "相場データ不足",
    "マッチ不一致",
    "発送元リスク",
    "競合激化予測",
    "スペック不明",
    "タイミング不適",
    "その他（自由入力）",
]

_NG_TO_CODE: Dict[str, str] = {
    "価格オーバー":       "roi_thin",
    "グレード不足":       "sellability_risk",
    "相場データ不足":     "evidence_insufficient",
    "マッチ不一致":       "different_coin",
    "発送元リスク":       "ship_from_invalid",
    "競合激化予測":       "roi_thin",
    "スペック不明":       "manual_hold",
    "タイミング不適":     "manual_hold",
    "その他（自由入力）": "manual_hold",
}

# =========================================================
# Utils
# =========================================================

def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default

def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

def fmt_dt(v: Any) -> str:
    dt = parse_dt(v)
    if not dt:
        return "-"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def fmt_jpy(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return f"¥{int(float(v)):,}"
    except Exception:
        return str(v)

def fmt_num(v: Any, digits: int = 0) -> str:
    if v in (None, ""):
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return str(v)

def fmt_pct(v: Any, digits: int = 1) -> str:
    if v in (None, ""):
        return "-"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except Exception:
        return str(v)

def badge_html(text: str, bg: str, fg: str = "white") -> str:
    return (
        f"<span style='display:inline-block;padding:4px 10px;"
        f"border-radius:999px;background:{bg};color:{fg};"
        f"font-size:12px;font-weight:600;margin-right:6px;margin-bottom:6px;'>"
        f"{text}</span>"
    )

def normalize_ceo_decision(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = str(v).strip().lower()
    if v == "ng":
        return "rejected"
    return v


# =========================================================
# Data access helpers (Supabase)
# =========================================================

@st.cache_data(ttl=60)
def get_all_candidates(limit: int = 1000) -> List[Dict[str, Any]]:
    db = get_supabase_client()
    res = (
        db.table("daily_candidates")
        .select("*")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


@st.cache_data(ttl=30)
def get_candidate_by_id(candidate_id: str) -> Optional[Dict[str, Any]]:
    db = get_supabase_client()
    res = (
        db.table("daily_candidates")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


@st.cache_data(ttl=30)
def get_latest_decision(candidate_id: str) -> Optional[Dict[str, Any]]:
    db = get_supabase_client()
    try:
        res = (
            db.table("candidate_decisions")
            .select("*")
            .eq("candidate_id", candidate_id)
            .order("decided_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        return None


@st.cache_data(ttl=30)
def get_bid_records_for(candidate_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    db = get_supabase_client()
    try:
        q = (
            db.table("bidding_records")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if candidate_id:
            q = q.eq("candidate_id", candidate_id)
        return q.execute().data or []
    except Exception:
        return []


@st.cache_data(ttl=30)
def get_all_bid_records(limit: int = 500) -> List[Dict[str, Any]]:
    return get_bid_records_for(candidate_id=None, limit=limit)


@st.cache_data(ttl=60)
def get_bid_summary() -> Dict[str, Any]:
    rows = get_all_bid_records(limit=500)
    summary: Dict[str, Any] = {
        "total": len(rows),
        "queued": 0, "submitted": 0, "won": 0,
        "lost": 0, "cancelled": 0, "error": 0,
    }
    for row in rows:
        status = (row.get("bid_status") or "").lower()
        if status in summary:
            summary[status] += 1
    return summary


def clear_caches():
    get_all_candidates.clear()
    get_candidate_by_id.clear()
    get_latest_decision.clear()
    get_bid_records_for.clear()
    get_all_bid_records.clear()
    get_bid_summary.clear()


# =========================================================
# Row enrichment / view model
# =========================================================

def build_row_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """1候補の評価結果をまとめた view dict を返す (UI表示用)。"""
    evaluation = evaluate_candidate_eligibility(row)
    latest_status = get_latest_status_check(str(row["id"]))

    # ステータスが最新なら上書き
    if latest_status:
        row = {**row, **{
            "is_active":              latest_status.get("is_active",              row.get("is_active")),
            "is_sold":                latest_status.get("is_sold",                row.get("is_sold")),
            "lot_size":               latest_status.get("lot_size",               row.get("lot_size")),
            "last_status_checked_at": latest_status.get("checked_at",             row.get("last_status_checked_at")),
            "shipping_from_country":  latest_status.get("ship_from",              row.get("shipping_from_country")),
            "source_currency":        latest_status.get("currency",               row.get("source_currency")),
        }}

    ceo_decision_norm = normalize_ceo_decision(
        row.get("decision_status") or row.get("ceo_decision")
    )

    is_review_ng = (
        evaluation.auto_tier == "AUTO_REVIEW"
        and ceo_decision_norm == "rejected"
    )

    return {
        **row,
        "auto_tier":          evaluation.auto_tier,
        "eligibility_status": evaluation.eligibility_status,
        "hard_fail_codes":    evaluation.hard_fail_codes,
        "warning_codes":      evaluation.warning_codes,
        "info_codes":         evaluation.info_codes,
        "approval_blocked":   evaluation.approval_blocked,
        "stale":              is_stale(row.get("last_status_checked_at")),
        "ceo_decision_norm":  ceo_decision_norm,
        "review_ng":          is_review_ng,
    }


def get_filtered_candidates() -> List[Dict[str, Any]]:
    """サイドバーフィルタを適用した候補リストを返す。"""
    all_rows = get_all_candidates(limit=1000)
    row_views = [build_row_view(r) for r in all_rows]

    source_filter   = st.session_state.get("flt_source", "ALL")
    tier_filter     = st.session_state.get("flt_tier", "ALL")
    active_only     = st.session_state.get("flt_active_only", False)
    exclude_stale   = st.session_state.get("flt_exclude_stale", False)
    min_evidence    = safe_int(st.session_state.get("flt_min_evidence", 0))
    min_profit      = safe_float(st.session_state.get("flt_min_profit", 0))
    decision_filter = st.session_state.get("flt_decision", "ALL")
    review_ng_only  = st.session_state.get("flt_review_ng_only", True)

    out = []
    for row in row_views:
        if source_filter and source_filter != "ALL" and row.get("source") != source_filter:
            continue
        if tier_filter and tier_filter != "ALL" and row.get("auto_tier") != tier_filter:
            continue
        if active_only and not row.get("is_active", False):
            continue
        if exclude_stale and row.get("stale"):
            continue
        if safe_int(row.get("evidence_count")) < min_evidence:
            continue
        if safe_float(row.get("projected_profit_jpy")) < min_profit:
            continue
        if decision_filter and decision_filter != "ALL" and row.get("ceo_decision_norm") != decision_filter:
            continue
        if review_ng_only and not row.get("review_ng"):
            continue
        out.append(row)

    # REVIEW_NG → AUTO_PASS → AUTO_REVIEW → AUTO_REJECT の順、次に profit 降順
    TIER_PRIO = {"AUTO_PASS": 0, "AUTO_REVIEW": 1, "AUTO_REJECT": 2}
    out.sort(key=lambda x: (
        not x.get("review_ng"),
        TIER_PRIO.get(x.get("auto_tier") or "", 9),
        -safe_float(x.get("projected_profit_jpy")),
        -safe_int(x.get("evidence_count")),
    ))
    return out


# =========================================================
# Sidebar / filters
# =========================================================

def render_sidebar():
    st.sidebar.title("フィルタ")

    raw_rows = get_all_candidates(limit=1000)
    sources = sorted({str(r.get("source") or "") for r in raw_rows if r.get("source")})
    decisions = ["ALL", "approved", "held", "rejected"]

    st.sidebar.selectbox("Source", ["ALL"] + sources, key="flt_source")
    st.sidebar.selectbox(
        "Auto Tier", ["ALL", "AUTO_PASS", "AUTO_REVIEW", "AUTO_REJECT"], key="flt_tier")
    st.sidebar.selectbox("Decision", decisions, key="flt_decision")
    st.sidebar.checkbox("Active only",    value=False, key="flt_active_only")
    st.sidebar.checkbox("Staleを除外",     value=False, key="flt_exclude_stale")
    st.sidebar.checkbox(
        REVIEW_NG_LABEL + " のみ", value=True, key="flt_review_ng_only")
    st.sidebar.number_input(
        "最低 evidence 件数", min_value=0, max_value=20, value=0, step=1,
        key="flt_min_evidence")
    st.sidebar.number_input(
        "最低 projected profit (JPY)", min_value=0, max_value=500000,
        value=0, step=5000, key="flt_min_profit")

    st.sidebar.divider()
    if st.sidebar.button("キャッシュ更新"):
        clear_caches()
        st.rerun()


# =========================================================
# KPI bar
# =========================================================

def render_top_metrics(rows: List[Dict[str, Any]]):
    total      = len(rows)
    auto_pass  = sum(1 for r in rows if r.get("auto_tier") == "AUTO_PASS")
    auto_rev   = sum(1 for r in rows if r.get("auto_tier") == "AUTO_REVIEW")
    auto_rej   = sum(1 for r in rows if r.get("auto_tier") == "AUTO_REJECT")
    review_ng  = sum(1 for r in rows if r.get("review_ng"))
    with_ev    = sum(1 for r in rows if safe_int(r.get("evidence_count")) > 0)
    with_price = sum(1 for r in rows if r.get("projected_profit_jpy") is not None)

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("候補",        total)
    c2.metric("AUTO_PASS",   auto_pass)
    c3.metric("AUTO_REVIEW", auto_rev)
    c4.metric("AUTO_REJECT", auto_rej)
    c5.metric("REVIEW_NG",   review_ng, help=REVIEW_NG_LABEL)
    c6.metric("Evidence",    f"{with_ev}/{total}")
    c7.metric("Pricing",     f"{with_price}/{total}")


# =========================================================
# Tab 1: 候補一覧
# =========================================================

def render_candidate_list_tab():
    st.subheader("候補一覧")

    rows = get_filtered_candidates()
    render_top_metrics(rows)

    if not rows:
        st.info("該当候補がありません。サイドバーのフィルタを調整してください。")
        return

    records = []
    for row in rows:
        records.append({
            "id":         str(row.get("id", ""))[:18],
            "source":     row.get("source"),
            "title":      (row.get("lot_title") or row.get("title") or "")[:55],
            "grader":     row.get("grader"),
            "cert":       row.get("cert_number"),
            "auto_tier":  row.get("auto_tier"),
            "review_ng":  "YES" if row.get("review_ng") else "",
            "decision":   row.get("ceo_decision_norm"),
            "active":     row.get("is_active"),
            "stale":      row.get("stale"),
            "evidence":   row.get("evidence_count"),
            "profit_jpy": fmt_jpy(row.get("projected_profit_jpy")),
            "roi":        fmt_pct(row.get("projected_roi")),
            "max_bid":    fmt_jpy(row.get("recommended_max_bid_jpy")),
            "ship_from":  row.get("shipping_from_country"),
            "currency":   row.get("source_currency"),
        })
    df = pd.DataFrame(records)

    st.dataframe(df, use_container_width=True, hide_index=True, height=500)

    st.divider()
    candidate_ids = [str(r.get("id", "")) for r in rows]
    sel = st.selectbox(
        "候補を選択 → CEO確認タブへ",
        options=candidate_ids,
        index=0,
        key="list_sel_id",
    )
    if sel and st.button("CEO確認タブで開く"):
        st.session_state["selected_candidate_id"] = str(sel)
        st.rerun()


# =========================================================
# Detail renderers (shared between tabs)
# =========================================================

def render_auto_tier_banner(auto_tier: Optional[str], is_review_ng: bool = False):
    color = AUTO_TIER_COLOR.get(auto_tier, "#6b7280")
    label = auto_tier or "UNKNOWN"
    sub   = f" — {REVIEW_NG_LABEL}" if is_review_ng else ""
    st.markdown(
        f'<div style="padding:12px 18px;border-radius:10px;background:{color};'
        f'color:white;font-size:20px;font-weight:700;margin-bottom:10px;">'
        f'{label}{sub}</div>',
        unsafe_allow_html=True,
    )


def render_reason_badges(title: str, codes: List[str], color: str):
    if not codes:
        return
    st.markdown(f"**{title}**")
    html = "".join([badge_html(code, color) for code in codes])
    st.markdown(html, unsafe_allow_html=True)


def render_candidate_overview(candidate: Dict[str, Any]):
    st.markdown("**概要**")
    lot_title = candidate.get("lot_title") or candidate.get("title") or "-"
    lot_url   = candidate.get("lot_url") or ""
    source    = candidate.get("source") or "-"
    grader    = candidate.get("grader") or "-"
    cert      = candidate.get("cert_number") or "-"
    coin_year = candidate.get("year") or candidate.get("coin_year") or "-"
    grade     = candidate.get("grade") or "-"
    auction_h = candidate.get("auction_house") or source

    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(f"**{lot_title}**")
        st.caption(f"Source: {source}  |  Auction: {auction_h}")
    with c2:
        if lot_url:
            st.link_button("🔗 見る", lot_url, use_container_width=True)

    oc1, oc2, oc3, oc4 = st.columns(4)
    oc1.metric("Grader",  grader)
    oc2.metric("Cert#",   cert[:20] if cert != "-" else "-")
    oc3.metric("Year",    coin_year)
    oc4.metric("Grade",   grade)

    # Cert verification links
    if cert and cert != "-" and grader and grader != "-":
        cert_clean = cert.replace("-", "").replace(" ", "")
        lc1, lc2, lc3 = st.columns(3)
        if grader.upper() == "NGC":
            lc1.link_button(
                "🔍 NGC Verify",
                f"https://www.ngccoin.com/certlookup/{cert_clean}/",
                use_container_width=True)
        elif grader.upper() == "PCGS":
            lc1.link_button(
                "🔍 PCGS Cert",
                f"https://www.pcgs.com/cert/{cert_clean}",
                use_container_width=True)
        lc2.link_button(
            "📚 Heritage Archive",
            "https://coins.ha.com/archives/price-results.zx",
            use_container_width=True)
        lc3.link_button(
            "💰 Spink Prices",
            "https://www.spink.com/results",
            use_container_width=True)


def render_status_panel(candidate: Dict[str, Any]):
    st.markdown("**Status**")
    latest_status = get_latest_status_check(str(candidate["id"]))
    row = latest_status or candidate

    cols = st.columns(5)
    cols[0].metric("Active",    "YES" if row.get("is_active") else "NO")
    cols[1].metric("Sold",      "YES" if row.get("is_sold")   else "NO")
    cols[2].metric("Lot Size",  fmt_num(row.get("lot_size")))
    cols[3].metric("Currency",  row.get("source_currency") or row.get("currency") or "-")
    cols[4].metric("Ship From", row.get("shipping_from_country") or row.get("ship_from") or "-")

    last_checked = row.get("last_status_checked_at") or row.get("checked_at")
    stale_flag   = is_stale(last_checked)
    st.caption(
        f"Last checked: {fmt_dt(last_checked)}"
        + ("  ⚠️ Stale" if stale_flag else "")
    )

    if st.button("最新 status を再取得", key=f"refresh_st_{candidate['id']}"):
        try:
            refresh_candidate_status(str(candidate["id"]))
            clear_caches()
            st.success("status refresh 完了")
            st.rerun()
        except Exception as e:
            st.error(f"status refresh 失敗: {e}")


def render_pricing_panel(candidate_id: str):
    st.markdown("**Pricing**")
    pricing = get_latest_pricing_snapshot(candidate_id)

    if not pricing:
        st.warning("pricing snapshot がありません。nightly_ops 実行後に反映されます。")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Expected Sale",   fmt_jpy(pricing.get("expected_sale_price_jpy")))
    c2.metric("Total Cost",      fmt_jpy(pricing.get("total_cost_jpy")))
    c3.metric("Projected Profit",fmt_jpy(pricing.get("projected_profit_jpy")))
    c4.metric("Projected ROI",   fmt_pct(pricing.get("projected_roi")))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("直近3m",  fmt_jpy(pricing.get("recent_3m_avg_jpy")))
    c6.metric("3-6m",    fmt_jpy(pricing.get("recent_3_6m_avg_jpy")))
    c7.metric("6-12m",   fmt_jpy(pricing.get("recent_6_12m_avg_jpy")))
    c8.metric("12m+",    fmt_jpy(pricing.get("older_12m_plus_avg_jpy")))

    c9, c10, c11 = st.columns(3)
    c9.metric("Max Bid",         fmt_jpy(pricing.get("recommended_max_bid_jpy")))
    c10.metric("Quality Score",  fmt_num(pricing.get("comparison_quality_score"), 2))
    c11.metric("Comp Count 3m",  fmt_num(pricing.get("comp_count_recent_3m")))

    st.caption(
        f"Strategy: {pricing.get('expected_sale_strategy') or '-'}  |  "
        f"市場データ: 24,961件 (直近3か月最重視)"
    )
    if pricing.get("pricing_note"):
        st.info(pricing["pricing_note"])

    with st.expander("cost breakdown"):
        cost_json = pricing.get("cost_formula_json") or pricing.get("cost_json")
        if cost_json:
            st.json(cost_json)
        else:
            st.caption("(なし)")


def render_evidence_panel(candidate_id: str, evidence_count: int = 0):
    label = f"🗂 Evidence ({evidence_count}件)" if evidence_count else "🗂 Evidence"
    with st.expander(label, expanded=(evidence_count > 0 and evidence_count <= 8)):
        ev_rows = get_candidate_evidence(candidate_id)

        if not ev_rows:
            st.warning("証拠がありません。")
            if st.button("evidence bundle 生成", key=f"build_ev_{candidate_id}"):
                try:
                    build_candidate_evidence_bundle(candidate_id, replace_generated=False)
                    clear_caches()
                    st.success("evidence bundle 生成完了")
                    st.rerun()
                except Exception as e:
                    st.error(f"生成失敗: {e}")
            return

        summ    = evidence_summary(ev_rows)
        grouped = group_candidate_evidence(ev_rows)

        cols = st.columns(max(1, min(6, len(summ))))
        for idx, (k, v) in enumerate(summ.items()):
            cols[idx % len(cols)].metric(EVIDENCE_TYPE_LABEL.get(k, k), v)

        OPEN_TYPES = {"source_listing", "cert_verification", "yahoo_comp"}
        for ev_type, items in grouped.items():
            with st.expander(
                f"{EVIDENCE_TYPE_LABEL.get(ev_type, ev_type)} ({len(items)})",
                expanded=(ev_type in OPEN_TYPES),
            ):
                for item in items:
                    st.markdown(f"**{item.get('title') or '-'}**")
                    if item.get("evidence_url"):
                        st.markdown(f"[リンクを開く]({item['evidence_url']})")
                    meta = item.get("meta_json")
                    if meta:
                        SHOW_KEYS = [
                            "source", "grader", "cert_number", "verified_status",
                            "sale_price_jpy", "sale_date", "bucket",
                            "year_exact_match", "current_price", "currency",
                            "shipping_from_country", "end_time",
                        ]
                        filtered = {k2: meta[k2] for k2 in SHOW_KEYS if k2 in meta}
                        st.json(filtered if filtered else meta)
                    st.markdown("")


def render_decision_panel(candidate: Dict[str, Any], approval_blocked: bool):
    """CEO意思決定ボタン群。"""
    cid      = str(candidate["id"])
    dedup    = candidate.get("dedup_key") or cid
    key_pfx  = cid[:12]

    latest = get_latest_decision(cid)
    if latest:
        dec_at = fmt_dt(latest.get("decided_at") or latest.get("created_at"))
        st.caption(
            f"前回: {latest.get('decision')} / "
            f"{latest.get('reason_code') or '-'} / {dec_at}"
        )
        if latest.get("decision_note"):
            st.info(latest["decision_note"])

    reason_code = st.selectbox(
        "Reason code",
        options=[
            "approved", "manual_review_ok", "profit_thin", "missing_cert",
            "ship_from_invalid", "different_coin", "manual_hold",
            "legacy_ng_recheck", "pricing_missing",
        ],
        key=f"reason_{key_pfx}",
    )
    decision_note = st.text_area(
        "Decision note", key=f"dnote_{key_pfx}", height=80)

    # AUTO_REJECT 例外承認チェックボックス
    can_approve = not approval_blocked
    if approval_blocked:
        can_approve = st.checkbox(
            "⚠️ 例外承認（Hard rule抵触を承知の上で承認）",
            key=f"exc_{key_pfx}",
        )

    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("✅ 承認", key=f"appr_{key_pfx}",
                     disabled=not can_approve,
                     use_container_width=True,
                     type="primary" if can_approve else "secondary"):
            _do_ceo_decision(cid, "approved", reason_code, decision_note)

    with c2:
        if st.button("⏸ 保留", key=f"hold_{key_pfx}", use_container_width=True):
            _do_ceo_decision(cid, "held", reason_code, decision_note)

    with c3:
        ng_key = f"ng_tog_{key_pfx}"
        if st.button("❌ NG", key=f"ng_btn_{key_pfx}", use_container_width=True):
            st.session_state[ng_key] = not st.session_state.get(ng_key, False)

    # NG 理由入力 (トグル)
    if st.session_state.get(f"ng_tog_{key_pfx}", False):
        ng_cat = st.selectbox(
            "NG理由", NG_REASON_CATEGORIES, key=f"ng_cat_{key_pfx}")
        ng_txt = st.text_input(
            "補足テキスト（任意）", key=f"ng_txt_{key_pfx}",
            placeholder="例: $950まで競り上がり → 予算超過")
        ng_comment = st.text_input("追加コメント", key=f"ng_cmt_{key_pfx}")
        ng_reason  = f"[{ng_cat}] {ng_txt}".rstrip() if ng_txt else ng_cat
        final_note = f"{ng_reason} | {ng_comment}" if ng_comment else ng_reason

        if st.button("💾 NGを保存", key=f"ng_save_{key_pfx}", type="primary"):
            _do_ceo_decision(
                cid, "rejected",
                reason_code=_NG_TO_CODE.get(ng_cat, "manual_hold"),
                decision_note=final_note,
            )


def _do_ceo_decision(
    candidate_id: str,
    decision: str,
    reason_code: str,
    decision_note: str,
) -> None:
    """decision を DB に保存して画面をリフレッシュ。"""
    try:
        save_ceo_decision(
            candidate_id=candidate_id,
            decision=decision,
            reason_code=reason_code,
            decision_note=decision_note or None,
            decided_by="ceo",
            source_screen="dashboard_v2",
        )
        clear_caches()
        st.session_state["selected_candidate_id"] = None
        st.success(f"{decision} を保存しました")
        st.rerun()
    except Exception as e:
        st.error(f"保存失敗: {e}")


def render_bid_queue_panel(candidate: Dict[str, Any]):
    """入札キュー追加セクション。"""
    cid     = str(candidate["id"])
    key_pfx = cid[:12]
    pricing = get_latest_pricing_snapshot(cid)

    existing = get_bid_records_for(candidate_id=cid, limit=20)
    if existing:
        st.caption("既存の bid records")
        bid_df = pd.DataFrame([{
            "status":       r.get("bid_status"),
            "bid_max_jpy":  fmt_jpy(r.get("bid_max_jpy")),
            "scheduled_at": fmt_dt(r.get("scheduled_at")),
            "external_ref": r.get("external_ref"),
            "created_at":   fmt_dt(r.get("created_at")),
        } for r in existing])
        st.dataframe(bid_df, use_container_width=True, hide_index=True, height=140)

    default_bid = safe_float(
        (pricing or {}).get("recommended_max_bid_jpy")
        or candidate.get("recommended_max_bid_jpy")
    )
    bid_max_jpy = st.number_input(
        "Bid max (JPY)", min_value=0, max_value=10_000_000,
        value=int(default_bid) if default_bid > 0 else 0,
        step=1000, key=f"bidmax_{key_pfx}",
    )
    bid_note = st.text_area(
        "Bid note", key=f"bid_note_{key_pfx}", height=70)

    q1, q2 = st.columns(2)
    with q1:
        if st.button("📥 キュー追加", key=f"queue_{key_pfx}"):
            try:
                queue_candidate_for_bid(
                    candidate_id=cid,
                    approved_by="ceo",
                    bid_max_jpy=float(bid_max_jpy),
                    note=bid_note or None,
                )
                clear_caches()
                st.success("bid queue に追加しました")
                st.rerun()
            except Exception as e:
                st.error(f"bid queue 追加失敗: {e}")

    with q2:
        if existing:
            new_status = st.selectbox(
                "Status update",
                options=["queued", "submitted", "won", "lost", "cancelled", "error"],
                key=f"bid_st_sel_{key_pfx}",
            )
            if st.button("🔄 最新 bid 更新", key=f"upd_bid_{key_pfx}"):
                try:
                    latest_bid_id = str(existing[0]["id"])
                    update_bid_record_status(
                        record_id=latest_bid_id,
                        bid_status=new_status,
                        note=bid_note or None,
                    )
                    clear_caches()
                    st.success("bid status 更新しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"更新失敗: {e}")


# =========================================================
# Tab 2: CEO確認 (2-panel 意思決定装置)
# =========================================================

def render_ceo_review_tab():
    st.subheader("CEO確認 — 意思決定装置")

    filtered_rows = get_filtered_candidates()

    if not filtered_rows:
        st.info(
            "表示対象がありません。\n\n"
            "サイドバーの「REVIEW_NG のみ」フィルタをオフにすると全候補が表示されます。"
        )
        return

    # 全候補からIDリストを構築
    all_candidates = get_all_candidates(limit=1000)
    options = [str(r.get("id", "")) for r in filtered_rows]

    selected_id = st.session_state.get("selected_candidate_id")
    if selected_id not in options:
        selected_id = options[0] if options else None

    if not selected_id:
        st.warning("候補がありません。")
        return

    # ─── 2パネルレイアウト ──────────────────────────────────────
    list_col, detail_col = st.columns([2, 3], gap="medium")

    # ── 左: 候補リスト ─────────────────────────────────────────
    with list_col:
        st.markdown("##### 候補リスト")
        st.caption(f"{len(filtered_rows)}件")

        for row in filtered_rows[:150]:
            cid       = str(row.get("id", ""))
            title     = (row.get("lot_title") or row.get("title") or "(no title)")[:48]
            source    = (row.get("source") or "?").upper()
            tier      = row.get("auto_tier") or "?"
            color     = AUTO_TIER_COLOR.get(tier, "#6b7280")
            is_rng    = row.get("review_ng", False)
            label     = "🔄 再確認候補" if is_rng else tier
            ev_cnt    = safe_int(row.get("evidence_count"))
            profit    = row.get("projected_profit_jpy")
            max_bid   = row.get("recommended_max_bid_jpy")
            grader    = row.get("grader") or ""
            cert      = row.get("cert_number") or ""
            is_sel    = (cid == selected_id)

            bg     = "#253560" if is_sel else "#1a2540"
            border = "border:2px solid #f5c518" if is_sel else "border:1px solid #2d3a5a"

            profit_str = fmt_jpy(profit) if profit else "価格未設定"
            max_bid_str= fmt_jpy(max_bid) if max_bid else ""
            cert_str   = f"{grader} {cert[:15]}".strip() if (grader or cert) else "(no cert)"

            st.markdown(
                f'<div style="background:{bg};{border};border-radius:8px;'
                f'padding:7px 10px;margin-bottom:4px">'
                f'<div style="color:{color};font-size:.72rem;font-weight:700;margin-bottom:2px">'
                f'{label} · {source}</div>'
                f'<div style="color:#f0f4ff;font-size:.82rem;font-weight:600;line-height:1.2">'
                f'{title}</div>'
                f'<div style="color:#94a3b8;font-size:.72rem;margin-top:2px">'
                f'{cert_str} | 証拠{ev_cnt} | {profit_str}'
                + (f' | 上限{max_bid_str}' if max_bid_str else '')
                + f'</div></div>',
                unsafe_allow_html=True,
            )
            if st.button("詳細", key=f"ceosel_{cid[:12]}", use_container_width=True):
                st.session_state["selected_candidate_id"] = cid
                st.rerun()

    # ── 右: 詳細パネル ─────────────────────────────────────────
    with detail_col:
        candidate = get_candidate_by_id(selected_id)
        if not candidate:
            st.warning("候補が見つかりません。再読込してください。")
            return

        ev = evaluate_candidate_eligibility(candidate)
        is_review_ng = (
            ev.auto_tier == "AUTO_REVIEW"
            and normalize_ceo_decision(candidate.get("ceo_decision")) == "rejected"
        )

        # Auto Tier バナー
        render_auto_tier_banner(ev.auto_tier, is_review_ng)

        # 概要
        render_candidate_overview(candidate)

        # Hard fail / Warning / Info バッジ
        render_reason_badges("Hard Fail", ev.hard_fail_codes, "#dc2626")
        render_reason_badges("Warnings",  ev.warning_codes,   "#d97706")
        render_reason_badges("Info",      ev.info_codes,      "#2563eb")

        # Status
        st.divider()
        render_status_panel(candidate)

        # Pricing
        st.divider()
        render_pricing_panel(selected_id)

        # Evidence
        st.divider()
        render_evidence_panel(selected_id, safe_int(candidate.get("evidence_count")))

        # CEO 意思決定
        st.divider()
        st.markdown("##### CEO 決定")
        render_decision_panel(candidate, ev.approval_blocked)

        # Bid Queue
        st.divider()
        st.markdown("##### Bid Queue")
        render_bid_queue_panel(candidate)

        # raw
        with st.expander("raw candidate row"):
            st.json(candidate)


# =========================================================
# Tab 3: 入札実績
# =========================================================

def render_bid_records_tab():
    st.subheader("入札実績")

    summary = get_bid_summary()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total",     summary.get("total",     0))
    c2.metric("Queued",    summary.get("queued",    0))
    c3.metric("Submitted", summary.get("submitted", 0))
    c4.metric("Won",       summary.get("won",       0))
    c5.metric("Lost",      summary.get("lost",      0))
    c6.metric("Error",     summary.get("error",     0))

    if st.button("🔄 再読込", key="bid_reload"):
        clear_caches()
        st.rerun()

    rows = get_all_bid_records(limit=500)
    if not rows:
        st.info("bid records がありません。CEO確認タブで承認 → キュー追加すると表示されます。")
        return

    df = pd.DataFrame([{
        "id":           str(r.get("id", ""))[:18],
        "candidate_id": str(r.get("candidate_id", ""))[:18],
        "status":       r.get("bid_status"),
        "bid_max_jpy":  fmt_jpy(r.get("bid_max_jpy")),
        "execution":    r.get("execution_mode"),
        "scheduled_at": fmt_dt(r.get("scheduled_at")),
        "external_ref": r.get("external_ref"),
        "note":         (r.get("note") or "")[:40],
        "created_at":   fmt_dt(r.get("created_at")),
    } for r in rows])

    st.dataframe(df, use_container_width=True, hide_index=True, height=480)


# =========================================================
# Tab 4: 運用サマリー
# =========================================================

def render_ops_tab():
    st.subheader("運用サマリー")

    rows = get_all_candidates(limit=1000)
    total    = len(rows)
    ev_cnt   = sum(1 for r in rows if safe_int(r.get("evidence_count")) > 0)
    priced   = sum(1 for r in rows if r.get("recommended_max_bid_jpy"))
    pass_n   = sum(1 for r in rows if r.get("auto_tier") == "AUTO_PASS")
    rev_n    = sum(1 for r in rows if r.get("auto_tier") == "AUTO_REVIEW")
    rej_n    = sum(1 for r in rows if r.get("auto_tier") == "AUTO_REJECT")

    # REVIEW_NG: auto_tier=AUTO_REVIEW && ceo_decision=ng
    rng_n    = sum(
        1 for r in rows
        if r.get("auto_tier") == "AUTO_REVIEW"
        and normalize_ceo_decision(r.get("ceo_decision")) == "rejected"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("総候補数", total)
        st.metric("Evidence完備", f"{ev_cnt}/{total}")
        st.metric("Pricing完備",  f"{priced}/{total}")
    with c2:
        st.metric("AUTO_PASS",   pass_n)
        st.metric("AUTO_REVIEW", rev_n)
        st.metric("AUTO_REJECT", rej_n)
    with c3:
        st.metric("REVIEW_NG（再確認候補）", rng_n,
                  help=REVIEW_NG_LABEL)
        st.metric("nightly_ops 最終実行", "自動実行中")

    st.divider()
    st.markdown("""
**Day14 本番運用フロー**

1. **毎朝**: ダッシュボードを開く → サイドバー「REVIEW_NG のみ」で133件を確認
2. **CEO確認タブ**: 候補を選択 → auto tier / cert / evidence / pricing を確認
3. **判断**: approved / held / rejected を選択して保存
4. **approved**: Bid Queue セクションで max bid 確認 → キュー追加
5. **入札実績タブ**: submitted → won/lost を更新

**Pricing未設定 150件の扱い**
- Heritage 25件 + Spink 125件 → 価格データなし（ソース側の問題）
- 原則 `held` に設定
- 手動価格入力後に再評価可能

**Shadow Precision: 74.1% / DISAGREE_FP: 0件**
システムの誤推薦はゼロ。conservative ですが Day14 時点では正しい挙動です。
    """)


# =========================================================
# Yahoo履歴 CEO確認待ちタブ (Day 3)
# =========================================================

@st.cache_data(ttl=60, show_spinner=False)
def _load_yahoo_pending(status_filter_key: str, sort_by: str,
                        cert_filter: str, min_conf: float) -> list[dict]:
    """staging レコード取得 (60秒キャッシュ)。"""
    from db.yahoo_review_repo import load_pending_review
    client = get_supabase_client()
    statuses = (
        ["PENDING_CEO", "HELD"] if status_filter_key == "PENDING+HELD"
        else ["PENDING_CEO"] if status_filter_key == "PENDING_CEO"
        else ["HELD"]
    )
    return load_pending_review(
        client,
        status_filter   = statuses,
        sort_by         = sort_by,
        limit           = 300,
        cert_filter     = cert_filter or None,
        min_confidence  = min_conf if min_conf > 0 else None,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _load_yahoo_counts() -> dict:
    from db.yahoo_review_repo import count_pending_review
    return count_pending_review(get_supabase_client())


def _yahoo_badge(status: str) -> str:
    colors = {
        "PENDING_CEO":      ("#f59e0b", "white"),
        "HELD":             ("#6366f1", "white"),
        "APPROVED_TO_MAIN": ("#16a34a", "white"),
        "REJECTED":         ("#dc2626", "white"),
        "PROMOTED":         ("#0ea5e9", "white"),
    }
    bg, fg = colors.get(status, ("#6b7280", "white"))
    return badge_html(status, bg, fg)


def _confidence_bar(conf: float | None) -> str:
    if conf is None:
        return "-"
    pct = int((conf or 0) * 100)
    color = "#16a34a" if pct >= 70 else "#f59e0b" if pct >= 40 else "#dc2626"
    return (
        f'<span style="color:{color};font-weight:bold">{pct}%</span>'
        f'<span style="color:#9ca3af;font-size:0.8em"> (conf)</span>'
    )


def render_yahoo_pending_tab():
    """Yahoo履歴 CEO確認待ち タブ本体。"""
    from db.yahoo_review_repo import (
        load_staging_record,
        get_review_history,
        save_review_decision,
        count_pending_review,
    )

    # ---- ヘッダー ----
    st.subheader("📋 Yahoo履歴 CEO確認待ち")

    # ---- KPI ----
    counts = _load_yahoo_counts()
    pending_n  = counts.get("PENDING_CEO", 0)
    held_n     = counts.get("HELD", 0)
    approved_n = counts.get("APPROVED_TO_MAIN", 0)
    rejected_n = counts.get("REJECTED", 0)
    promoted_n = counts.get("PROMOTED", 0)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("PENDING_CEO",      pending_n)
    k2.metric("HELD",             held_n)
    k3.metric("APPROVED_TO_MAIN", approved_n)
    k4.metric("REJECTED",         rejected_n)
    k5.metric("PROMOTED",         promoted_n)

    st.divider()

    # ---- フィルター ----
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
    with fc1:
        status_key = st.selectbox(
            "表示ステータス",
            ["PENDING+HELD", "PENDING_CEO", "HELD"],
            key="yp_status_filter",
        )
    with fc2:
        sort_by = st.selectbox(
            "並び替え",
            ["sold_date_desc", "sold_date_asc", "confidence_desc", "fetched_desc"],
            format_func=lambda x: {
                "sold_date_desc":  "落札日 新→旧",
                "sold_date_asc":   "落札日 旧→新",
                "confidence_desc": "信頼度 高→低",
                "fetched_desc":    "取得日 新→旧",
            }.get(x, x),
            key="yp_sort_by",
        )
    with fc3:
        cert_filter = st.selectbox(
            "鑑定会社",
            ["ALL", "NGC", "PCGS"],
            key="yp_cert_filter",
        )
    with fc4:
        min_conf = st.slider(
            "最低 confidence",
            min_value=0.0, max_value=1.0, value=0.0, step=0.05,
            key="yp_min_conf",
        )

    cert_val = "" if cert_filter == "ALL" else cert_filter

    # ---- データ取得 ----
    rows = _load_yahoo_pending(status_key, sort_by, cert_val, min_conf)

    if not rows:
        st.info("該当レコードがありません。フィルタを調整してください。")
        st.caption("  yahoo-sync を実行すると market_transactions から staging に同期されます。")
        st.code("python scripts/yahoo_sold_sync.py --dry-run", language="bash")
        return

    st.caption(f"{len(rows)} 件表示中")

    # ---- 一覧テーブル ----
    list_records = []
    for r in rows:
        conf = r.get("parse_confidence")
        conf_str = f"{int((conf or 0)*100)}%" if conf is not None else "-"
        list_records.append({
            "lot_id":      r.get("yahoo_lot_id", "")[:16],
            "title":       (r.get("lot_title") or "")[:55],
            "sold_date":   str(r.get("sold_date") or "-")[:10],
            "price_jpy":   fmt_jpy(r.get("sold_price_jpy")),
            "cert":        f"{r.get('cert_company') or ''} {r.get('cert_number') or ''}".strip() or "-",
            "year":        str(r.get("year") or "-"),
            "grade":       r.get("grade_text") or "-",
            "denomination": r.get("denomination") or "-",
            "confidence":  conf_str,
            "status":      r.get("status", ""),
        })

    df = pd.DataFrame(list_records)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "lot_id":      st.column_config.TextColumn("lot_id", width="small"),
            "title":       st.column_config.TextColumn("タイトル", width="large"),
            "sold_date":   st.column_config.TextColumn("落札日",  width="small"),
            "price_jpy":   st.column_config.TextColumn("落札額",  width="small"),
            "cert":        st.column_config.TextColumn("Cert",   width="medium"),
            "year":        st.column_config.TextColumn("年",      width="small"),
            "grade":       st.column_config.TextColumn("グレード",width="small"),
            "denomination":st.column_config.TextColumn("額面",    width="small"),
            "confidence":  st.column_config.TextColumn("conf",   width="small"),
            "status":      st.column_config.TextColumn("status", width="small"),
        },
    )

    st.divider()

    # ---- 詳細パネル + レビュー ----
    st.subheader("詳細確認 / レビュー")

    # セレクタ: lot_id で選択
    lot_id_options = [r.get("yahoo_lot_id", "") for r in rows if r.get("yahoo_lot_id")]
    if not lot_id_options:
        st.info("lot_id のないレコードのみです。")
        return

    selected_lot_id = st.selectbox(
        "確認する lot_id を選択",
        lot_id_options,
        key="yp_selected_lot_id",
        format_func=lambda lid: (
            next(
                (f"{lid} — {(r.get('lot_title') or '')[:50]}"
                 for r in rows if r.get("yahoo_lot_id") == lid),
                lid,
            )
        ),
    )

    if not selected_lot_id:
        return

    # 選択レコードを取得
    selected_row = next(
        (r for r in rows if r.get("yahoo_lot_id") == selected_lot_id),
        None,
    )
    if not selected_row:
        st.warning("選択レコードが見つかりません。")
        return

    staging_id = selected_row.get("id", "")

    # ---- 詳細表示 ----
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown("#### コイン情報")
        st.markdown(f"**タイトル (生)**")
        st.text(selected_row.get("lot_title", "-"))
        st.markdown(f"**タイトル (正規化)**")
        st.text(selected_row.get("title_normalized") or "-")

        dc1, dc2, dc3, dc4 = st.columns(4)
        dc1.metric("落札額", fmt_jpy(selected_row.get("sold_price_jpy")))
        dc2.metric("落札日", str(selected_row.get("sold_date") or "-")[:10])
        dc3.metric("年号",   str(selected_row.get("year") or "-"))
        dc4.metric("額面",   selected_row.get("denomination") or "-")

        dc5, dc6, dc7, dc8 = st.columns(4)
        dc5.metric("鑑定会社", selected_row.get("cert_company") or "-")
        dc6.metric("cert#",  selected_row.get("cert_number")  or "-")
        dc7.metric("グレード", selected_row.get("grade_text")  or "-")
        dc8.metric("confidence", f"{int((selected_row.get('parse_confidence') or 0)*100)}%")

        # ソースリンク
        source_url = selected_row.get("source_url")
        if source_url:
            st.markdown(f"[🔗 Yahoo!オークションで確認]({source_url})")
        else:
            st.caption("source_url なし")

        # 画像表示
        img_url = selected_row.get("image_url") or selected_row.get("thumbnail_url")
        if img_url:
            st.image(img_url, width=240, caption="落札時画像")
        else:
            st.caption("画像なし")

    with col_right:
        st.markdown("#### ステータス")
        current_status = selected_row.get("status", "")
        st.markdown(_yahoo_badge(current_status), unsafe_allow_html=True)

        # ---- レビュー履歴 ----
        st.markdown("#### レビュー履歴")
        client = get_supabase_client()
        history = get_review_history(client, staging_id)
        if history:
            for rev in history:
                dec   = rev.get("decision", "?")
                dec_emoji = {"approved": "✅", "rejected": "❌", "held": "⏸"}.get(dec, "?")
                rev_at = str(rev.get("reviewed_at", ""))[:16]
                by_who = rev.get("reviewer", "?")
                reason = rev.get("reason") or ""
                note   = rev.get("review_note") or ""
                st.markdown(
                    f"{dec_emoji} **{dec}** — {rev_at} by {by_who}"
                    + (f"\n\n  理由: {reason}" if reason else "")
                    + (f"\n\n  メモ: {note}" if note else "")
                )
        else:
            st.caption("レビュー履歴なし")

    st.divider()

    # ---- レビューアクション ----
    st.markdown("#### レビューを記録する")

    # APPROVED / REJECTED の場合は再レビューを警告表示
    if current_status in (YahooStagingStatus.APPROVED_TO_MAIN, YahooStagingStatus.REJECTED):
        st.warning(
            f"この案件は既に **{current_status}** です。"
            f"変更する場合のみ操作してください。"
        )

    ra1, ra2 = st.columns(2)
    with ra1:
        reviewer_name = st.selectbox(
            "レビュー担当",
            ["ceo", "cap", "auto"],
            key="yp_reviewer",
        )
    with ra2:
        review_reason = st.text_input(
            "理由・却下コード (任意)",
            key="yp_reason",
            placeholder="roi_thin / different_coin / manual_hold など",
        )

    review_note = st.text_area(
        "メモ (任意)",
        key="yp_note",
        placeholder="詳細コメントを入力...",
        height=80,
    )

    btn_c1, btn_c2, btn_c3, _ = st.columns([1, 1, 1, 3])
    approve_btn = btn_c1.button("✅ APPROVE", key="yp_btn_approve", type="primary")
    hold_btn    = btn_c2.button("⏸ HOLD",    key="yp_btn_hold")
    reject_btn  = btn_c3.button("❌ REJECT",  key="yp_btn_reject",  type="secondary")

    decision_to_submit: str | None = None
    if approve_btn:
        decision_to_submit = "approved"
    elif hold_btn:
        decision_to_submit = "held"
    elif reject_btn:
        decision_to_submit = "rejected"

    if decision_to_submit:
        result = save_review_decision(
            client       = get_supabase_client(),
            staging_id   = staging_id,
            decision     = decision_to_submit,
            reviewer     = reviewer_name,
            reason       = review_reason or None,
            review_note  = review_note or None,
        )
        if result.ok:
            if result.warning:
                st.warning(result.warning)
            st.success(
                f"✅ {decision_to_submit.upper()} 保存完了 — "
                f"staging status → **{result.staging_status}**"
            )
            # キャッシュを破棄して一覧を再描画
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(f"保存失敗: {result.error}")


# =========================================================
# Main
# =========================================================

def main():
    st.title(APP_TITLE)
    st.caption("CEO承認中心の候補審査 · 証拠確認 · 入札キュー管理 · Day14 Go-Live")

    render_sidebar()

    tabs = st.tabs([
        "📋 候補一覧",
        "🔍 CEO確認",
        "📋 Yahoo履歴確認",
        "🎯 入札実績",
        "⚙️ 運用サマリー",
    ])

    with tabs[0]:
        try:
            render_candidate_list_tab()
        except Exception as e:
            st.error(f"候補一覧エラー: {e}")
            import traceback; st.code(traceback.format_exc())

    with tabs[1]:
        try:
            render_ceo_review_tab()
        except Exception as e:
            st.error(f"CEO確認エラー: {e}")
            import traceback; st.code(traceback.format_exc())

    with tabs[2]:
        try:
            render_yahoo_pending_tab()
        except Exception as e:
            st.error(f"Yahoo履歴確認エラー: {e}")
            import traceback; st.code(traceback.format_exc())

    with tabs[3]:
        try:
            render_bid_records_tab()
        except Exception as e:
            st.error(f"入札実績エラー: {e}")
            import traceback; st.code(traceback.format_exc())

    with tabs[4]:
        try:
            render_ops_tab()
        except Exception as e:
            st.error(f"運用サマリーエラー: {e}")
            import traceback; st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
