"""コイン仕入れDB ダッシュボード

起動:
    python -m streamlit run coin_business/dashboard.py

URL:
    http://localhost:8501
"""

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from scripts.supabase_client import get_client

# スクリーンショット保存先
SCREENSHOT_DIR = ROOT / "data" / "bid_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════
# ページ設定
# ════════════════════════════════════════════
st.set_page_config(
    page_title="コイン仕入れDB",
    page_icon="🪙",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ─ 全体 ─ */
.block-container { padding-top: 1rem; max-width: 860px; }
[data-testid="collapsedControl"] { display: none; }

/* ─ タイトル ─ */
.db-title {
  color: #f5c518;
  font-size: 2rem;
  font-weight: 800;
  text-align: center;
  margin-bottom: 2px;
}
.rate-bar {
  color: #94a3b8;
  font-size: 0.78rem;
  text-align: center;
  margin-bottom: 14px;
}

/* ─ カード ─ */
.coin-card {
  background: #1a2540;
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 10px;
}
.grader-name {
  color: #f5c518;
  font-weight: 700;
  font-size: 0.85rem;
  letter-spacing: .03em;
}
.coin-title {
  color: #f0f4ff;
  font-size: 1.05rem;
  font-weight: 700;
  line-height: 1.3;
}
.coin-sub {
  color: #94a3b8;
  font-size: 0.82rem;
}
.label-small {
  color: #94a3b8;
  font-size: 0.75rem;
  text-align: right;
}
.price-main {
  color: #00e676;
  font-size: 1.55rem;
  font-weight: 800;
  text-align: right;
  line-height: 1.1;
}
.price-sub {
  color: #64748b;
  font-size: 0.78rem;
  text-align: right;
}
.badge-ok  { color:#22c55e; font-weight:700; }
.badge-ng  { color:#ef4444; font-weight:700; }
.badge-ceo { color:#f97316; font-weight:700; }
.badge-rev { color:#eab308; font-weight:700; }

/* ─ CEO確認カード ─ */
.ceo-card {
  background: #1e2d1e;
  border: 1px solid #2d4a2d;
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 8px;
}
.ceo-card-rejected {
  background: #2d1e1e;
  border: 1px solid #4a2d2d;
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 8px;
  opacity: 0.6;
}

/* ─ 入札実績カード ─ */
.bid-card {
  background: #1a2035;
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 8px;
}
.result-win       { color:#22c55e; font-weight:700; }
.result-lose      { color:#ef4444; font-weight:700; }
.result-scheduled { color:#f5c518; font-weight:700; }
.result-cancelled { color:#94a3b8; font-weight:700; }

/* ─ 検索ボタン ─ */
.stButton > button {
  background: #f5c518 !important;
  color: #000 !important;
  font-weight: 700 !important;
  border: none !important;
  border-radius: 8px !important;
}
.stButton > button:hover {
  background: #d4a800 !important;
}
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════
# データ取得
# ════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def load_rates() -> dict:
    try:
        db = get_client()
        r = db.table('daily_rates').select('*').order('rate_date', desc=True).limit(1).execute()
        return r.data[0] if r.data else {}
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner="データ取得中…")
def load_coins() -> pd.DataFrame:
    db = get_client()
    rows = []
    last_id = '00000000-0000-0000-0000-000000000000'
    while True:
        resp = (db.table('coin_slab_data')
                .select('id,management_no,slab_line1,slab_line2,slab_line3,'
                        'material,grader,grade,'
                        'front_img_url,back_img_url,'
                        'ref1_buy_limit_20k_jpy,ref1_buy_limit_15pct_jpy,'
                        'ref2_buy_limit_20k_jpy,ref2_buy_limit_15pct_jpy,'
                        'ref1_buy_limit_jpy')
                .eq('status', 'completed_hit')
                .not_.is_('purity', 'null')
                .gt('id', last_id)
                .order('id')
                .limit(500)
                .execute())
        if not resp.data:
            break
        rows.extend(resp.data)
        last_id = resp.data[-1]['id']
        if len(resp.data) < 500:
            break
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def load_candidates() -> pd.DataFrame:
    db = get_client()
    r = (db.table('daily_candidates')
         .select('management_no,lot_title,lot_url,current_price,fx_rate,'
                 'estimated_cost_jpy,judgment,judgment_reason,report_date')
         .order('report_date', desc=True)
         .limit(500)
         .execute())
    if not r.data:
        return pd.DataFrame()
    df = pd.DataFrame(r.data)
    return df.drop_duplicates('management_no', keep='first')


@st.cache_data(ttl=30, show_spinner=False)
def load_ceo_pending() -> tuple[list[dict], str]:
    """CEO確認待ち候補を取得（OK/CEO判断/REVIEW）。"""
    db = get_client()
    try:
        r = (db.table('daily_candidates')
             .select('id,dedup_key,management_no,lot_title,judgment,judgment_reason,'
                     'buy_limit_jpy,estimated_cost_jpy,estimated_margin_pct,'
                     'auction_house,lot_url,lot_end_time,priority,status,'
                     'ceo_decision,ceo_ng_reason,ceo_comment,ceo_decided_at,'
                     'ref1_buy_limit_20k_jpy,current_price,currency,fx_rate')
             .in_('judgment', ['OK', 'CEO判断', 'REVIEW'])
             .eq('status', 'pending')
             .order('priority', desc=True)
             .order('lot_end_time', desc=False)
             .limit(200)
             .execute())
        return r.data or [], ""
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=30, show_spinner=False)
def load_bid_history_cached() -> tuple[list[dict], str]:
    """入札実績をキャッシュ付きで取得。"""
    db = get_client()
    try:
        r = (db.table('bid_history')
             .select('*')
             .order('bid_date', desc=True)
             .order('created_at', desc=True)
             .limit(200)
             .execute())
        return r.data or [], ""
    except Exception as e:
        return [], str(e)


# ════════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════════
def sv(v, default='') -> str:
    """NaN / None / 'nan' を安全に文字列化"""
    if v is None:
        return default
    if isinstance(v, float) and pd.isna(v):
        return default
    s = str(v).strip()
    return s if s and s.lower() != 'nan' else default


def fmt_jpy(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return '—'
    try:
        return f"¥{int(v):,}"
    except Exception:
        return '—'


def fmt_jpy_plain(v) -> str:
    """カンマ区切り・¥なし（価格表示用）"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return '—'
    try:
        return f"{int(v):,}円"
    except Exception:
        return '—'


JUDGMENT_CONFIG = {
    'OK':      ('✅ OK',      'ok'),
    'NG':      ('❌ NG',      'ng'),
    'CEO判断': ('🔶 CEO判断', 'ceo'),
    'REVIEW':  ('🔄 REVIEW',  'rev'),
}

RESULT_LABELS = {
    'scheduled': ('⏳ 予定', 'result-scheduled'),
    'win':       ('🏆 落札', 'result-win'),
    'lose':      ('❌ 落選', 'result-lose'),
    'cancelled': ('🚫 取消', 'result-cancelled'),
}

AUCTION_HOUSES = ['eBay', 'Heritage', 'Spink', 'NumisBids', 'Stack\'s Bowers', 'その他']


# ════════════════════════════════════════════
# Tab 1: 相場DB（既存機能）
# ════════════════════════════════════════════
def render_tab_db(rates: dict):
    usd_rate = float(rates.get('usd_jpy_calc') or 150)
    gbp_rate = float(rates.get('gbp_jpy_calc') or 200)

    coins_df = load_coins()
    cands_df = load_candidates()

    if coins_df.empty:
        st.error("データが取得できませんでした")
        return

    if not cands_df.empty:
        coins_df = coins_df.merge(cands_df, on='management_no', how='left')
    else:
        for col in ['lot_title','lot_url','current_price','fx_rate',
                    'estimated_cost_jpy','judgment','judgment_reason','report_date']:
            coins_df[col] = None

    col_s, col_b = st.columns([5, 1])
    with col_s:
        search_q = st.text_input(
            "コイン検索", placeholder="スラブテキスト / 国名 / 年号で検索",
            label_visibility="collapsed", key="search_q")
    with col_b:
        st.button("検索", use_container_width=True, key="search_btn")

    mat_options = ['全て', 'Gold', 'Silver', 'Platinum']
    sel_mat = st.radio("素材", mat_options, horizontal=True,
                       label_visibility="collapsed", key="mat_filter")

    df = coins_df.copy()
    if sel_mat != '全て':
        df = df[df['material'].str.lower() == sel_mat.lower()]
    if search_q:
        q = search_q.lower()
        mask = (
            df['slab_line1'].fillna('').str.lower().str.contains(q) |
            df['slab_line2'].fillna('').str.lower().str.contains(q) |
            df['slab_line3'].fillna('').str.lower().str.contains(q) |
            df['management_no'].fillna('').str.lower().str.contains(q)
        )
        df = df[mask]

    df = df.sort_values('ref1_buy_limit_20k_jpy', ascending=False, na_position='last')
    df = df.reset_index(drop=True)

    PAGE_SIZE = 50
    total = len(df)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    pcol1, pcol2, pcol3 = st.columns([3, 2, 2])
    with pcol1:
        st.markdown(f"<p style='color:#94a3b8;font-size:.85rem;margin:4px 0 8px'>{total:,}件</p>",
                    unsafe_allow_html=True)
    with pcol2:
        page = st.number_input("ページ", min_value=1, max_value=total_pages,
                               value=1, step=1, key="db_page",
                               label_visibility="collapsed")
    with pcol3:
        if st.button("🔄 再読込", key="reload_btn"):
            st.cache_data.clear()
            st.rerun()

    st.caption(f"ページ {page}/{total_pages}（{PAGE_SIZE}件/ページ）")

    start = (page - 1) * PAGE_SIZE
    end   = min(start + PAGE_SIZE, total)
    df = df.iloc[start:end]

    st.write("")

    for _, row in df.iterrows():
        grader    = sv(row.get('grader'))
        grade     = sv(row.get('grade'))
        line1     = sv(row.get('slab_line1'))
        line2     = sv(row.get('slab_line2'))
        mat       = sv(row.get('material'))
        front     = sv(row.get('front_img_url')) or None
        back      = sv(row.get('back_img_url'))  or None
        judgment  = sv(row.get('judgment'))
        lot_url   = sv(row.get('lot_url')) or None
        lot_title = sv(row.get('lot_title'))

        coin_name = lot_title or line1 or '—'
        sub_parts = [p for p in [line2, grade] if p]
        coin_sub  = ' | '.join(sub_parts)

        price_val = row.get('ref1_buy_limit_20k_jpy')
        r1_15     = row.get('ref1_buy_limit_15pct_jpy')
        r2_20     = row.get('ref2_buy_limit_20k_jpy')
        r2_15     = row.get('ref2_buy_limit_15pct_jpy')

        price_usd = price_gbp = ''
        if price_val and not pd.isna(price_val):
            try:
                price_usd = f"USD{int(price_val) / usd_rate:,.0f}"
                price_gbp = f"GBP{int(price_val) / gbp_rate:,.0f}"
            except Exception:
                pass

        badge_html = ''
        if judgment:
            badge_text, badge_cls = JUDGMENT_CONFIG.get(judgment, (judgment, ''))
            badge_html = f'<span class="badge-{badge_cls}">{badge_text}</span>&nbsp;'

        st.markdown('<div class="coin-card">', unsafe_allow_html=True)
        img_col, info_col, price_col = st.columns([1, 3, 2])

        with img_col:
            if front:
                st.image(front, width=110)
            elif back:
                st.image(back, width=110)
            else:
                st.markdown(
                    '<div style="width:110px;height:110px;background:#253352;'
                    'border-radius:8px;display:flex;align-items:center;'
                    'justify-content:center;font-size:2.2rem;">🪙</div>',
                    unsafe_allow_html=True)

        with info_col:
            st.markdown(
                f'<div class="grader-name">{badge_html}{grader}</div>'
                f'<div class="coin-title">{coin_name}</div>'
                f'<div class="coin-sub">{coin_sub}</div>'
                f'<div class="coin-sub" style="margin-top:4px">{mat}</div>',
                unsafe_allow_html=True)

        with price_col:
            if price_val and not pd.isna(price_val):
                st.markdown(
                    f'<div class="label-small">eBay上限（基準1）</div>'
                    f'<div class="price-main">{fmt_jpy_plain(price_val)}</div>'
                    f'<div class="price-sub">{price_usd} / {price_gbp}</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown('<div class="price-main" style="color:#475569">—</div>',
                            unsafe_allow_html=True)

        with st.expander("詳細を見る"):
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("🏆 基準1(2万円)", fmt_jpy(price_val),
                      help="主判定: 最低利益2万円ライン（基準1）")
            d2.metric("基準1(15%)",     fmt_jpy(r1_15),
                      help="補助: 粗利15%ライン（基準1）")
            d3.metric("基準2(2万円)",   fmt_jpy(r2_20),
                      help="補助: 最低利益2万円ライン（基準2）")
            d4.metric("基準2(15%)",     fmt_jpy(r2_15),
                      help="補助: 粗利15%ライン（基準2）")

            cost = row.get('estimated_cost_jpy')
            if cost and price_val and not pd.isna(price_val):
                try:
                    profit = int(price_val) - int(cost)
                    sign   = '+' if profit >= 0 else ''
                    color  = '#22c55e' if profit >= 20000 else '#ef4444'
                    st.markdown(
                        f"推定利益: <span style='color:{color};font-weight:700'>"
                        f"{sign}¥{profit:,}</span>　仕入: {fmt_jpy(cost)}",
                        unsafe_allow_html=True)
                except Exception:
                    pass

            if lot_url:
                st.markdown(f"[🔗 eBayで見る]({lot_url})")

            if back and back != front:
                st.image(back, width=150, caption="裏面")

        st.markdown('</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════
# Tab 2: CEO確認 (P0)
# ════════════════════════════════════════════
def render_tab_ceo():
    try:
        from scripts.candidates_writer import update_ceo_decision
    except Exception as import_err:
        st.error(f"import error: {import_err}")
        return

    st.markdown("#### 🔍 CEO確認 — 承認/NG判断")
    st.caption("status=pending の OK / CEO判断 / REVIEW 案件を表示しています。")

    try:
        candidates, _err = load_ceo_pending()
    except Exception as fetch_err:
        st.error(f"fetch error: {fetch_err}")
        return
    if _err:
        st.error(f"CEO確認データ取得エラー: {_err}")
    if not candidates:
        st.info("現在、確認待ちの案件はありません。")
        return

    # フィルター
    houses = sorted(set(c.get('auction_house') or 'その他' for c in candidates))
    house_options = ['全て'] + houses
    col_f1, col_f2 = st.columns([2, 2])
    with col_f1:
        sel_house = st.selectbox("オークション", house_options, key="ceo_house_filter")
    with col_f2:
        sel_judgment = st.selectbox("判定", ['全て', 'OK', 'CEO判断', 'REVIEW'], key="ceo_j_filter")

    filtered = candidates
    if sel_house != '全て':
        filtered = [c for c in filtered if (c.get('auction_house') or 'その他') == sel_house]
    if sel_judgment != '全て':
        filtered = [c for c in filtered if c.get('judgment') == sel_judgment]

    # 承認済み/NG済みの分離
    pending_list  = [c for c in filtered if c.get('ceo_decision') in (None, 'pending', '')]
    decided_list  = [c for c in filtered if c.get('ceo_decision') in ('approved', 'rejected')]

    st.markdown(f"**未判断: {len(pending_list)}件** ／ 判断済み: {len(decided_list)}件")
    st.divider()

    if st.button("🔄 再読込", key="ceo_reload"):
        st.cache_data.clear()
        st.rerun()

    # ─── 未判断カード ───
    for c in pending_list:
        dedup_key    = c.get('dedup_key', '')
        lot_title    = c.get('lot_title') or '(タイトルなし)'
        auction_h    = c.get('auction_house') or '—'
        judgment     = c.get('judgment') or '—'
        lot_url      = c.get('lot_url') or ''
        end_time_raw = c.get('lot_end_time') or ''
        priority     = c.get('priority') or 1
        buy_limit    = c.get('buy_limit_jpy') or c.get('ref1_buy_limit_20k_jpy')
        cost         = c.get('estimated_cost_jpy')
        margin_pct   = c.get('estimated_margin_pct')
        reason       = c.get('judgment_reason') or '—'
        cur_price    = c.get('current_price')
        currency     = c.get('currency') or 'USD'
        fx_rate      = c.get('fx_rate') or 150

        # 締切日時フォーマット
        end_str = ''
        if end_time_raw:
            try:
                end_dt = datetime.fromisoformat(str(end_time_raw).replace('Z','+00:00'))
                end_str = end_dt.strftime('%m/%d %H:%M')
            except Exception:
                end_str = str(end_time_raw)[:16]

        j_label, j_cls = JUDGMENT_CONFIG.get(judgment, (judgment, ''))

        st.markdown('<div class="ceo-card">', unsafe_allow_html=True)

        hcol1, hcol2 = st.columns([3, 1])
        with hcol1:
            st.markdown(
                f'<span class="badge-{j_cls}">{j_label}</span> &nbsp; '
                f'<b style="color:#f0f4ff">{lot_title[:70]}</b>',
                unsafe_allow_html=True)
            st.markdown(
                f'<span style="color:#94a3b8;font-size:.82rem">'
                f'🏛 {auction_h} &nbsp;|&nbsp; P{priority} &nbsp;|&nbsp; 締切: {end_str or "—"}'
                f'</span>',
                unsafe_allow_html=True)
        with hcol2:
            if lot_url:
                st.link_button("🔗 見る", lot_url, use_container_width=True)

        # 価格情報
        pcol1, pcol2, pcol3 = st.columns(3)
        with pcol1:
            price_str = f"{currency} {cur_price:,.0f}" if cur_price else '—'
            st.metric("現在価格", price_str)
        with pcol2:
            st.metric("仕入コスト", fmt_jpy(cost))
        with pcol3:
            margin_str = f"+{margin_pct:.1f}%" if margin_pct else '—'
            st.metric("仕入限界", fmt_jpy(buy_limit), delta=margin_str if margin_pct else None)

        with st.expander("判断理由を見る"):
            st.caption(reason)

        # 承認/NG ボタン
        key_prefix = dedup_key[:12] if dedup_key else str(id(c))
        bcol1, bcol2, bcol3 = st.columns([2, 2, 3])
        with bcol1:
            if st.button("✅ 承認", key=f"approve_{key_prefix}", use_container_width=True):
                ok = update_ceo_decision(dedup_key, "approved")
                if ok:
                    st.success("承認しました")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("保存失敗")
        with bcol2:
            ng_key = f"ng_toggle_{key_prefix}"
            if st.button("❌ NG", key=f"ng_btn_{key_prefix}", use_container_width=True):
                st.session_state[ng_key] = not st.session_state.get(ng_key, False)

        # NG理由入力（トグル）
        if st.session_state.get(f"ng_toggle_{key_prefix}", False):
            ng_reason = st.text_area("NG理由を入力", key=f"ng_reason_{key_prefix}",
                                     placeholder="例: 価格が予算オーバー / グレード不足 / 発送元リスク等")
            ng_comment = st.text_input("コメント（任意）", key=f"ng_comment_{key_prefix}")
            if st.button("💾 NGを保存", key=f"ng_save_{key_prefix}"):
                ok = update_ceo_decision(dedup_key, "rejected",
                                         ng_reason=ng_reason or None,
                                         comment=ng_comment or None)
                if ok:
                    st.warning("NGとして保存しました")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("保存失敗")

        st.markdown('</div>', unsafe_allow_html=True)
        st.write("")

    # ─── 判断済み（折りたたみ） ───
    if decided_list:
        with st.expander(f"判断済み {len(decided_list)}件"):
            for c in decided_list:
                decision = c.get('ceo_decision')
                icon = '✅' if decision == 'approved' else '❌'
                ng_r = c.get('ceo_ng_reason') or ''
                decided_at = str(c.get('ceo_decided_at') or '')[:16]
                st.markdown(
                    f"{icon} **{c.get('lot_title','')[:50]}** &nbsp; "
                    f"<span style='color:#94a3b8;font-size:.8rem'>{c.get('auction_house','')} | {decided_at}</span>"
                    + (f"<br><span style='color:#ef4444;font-size:.8rem'>NG理由: {ng_r}</span>" if ng_r else ""),
                    unsafe_allow_html=True)


# ════════════════════════════════════════════
# Tab 3: 入札実績 (P1)
# ════════════════════════════════════════════
def render_tab_bid_history(usd_rate: float):
    from scripts.candidates_writer import save_bid_entry, update_bid_entry

    st.markdown("#### 📋 入札実績 — エビデンス管理")
    st.caption("入札した案件の結果を記録・蓄積します。CEOはスクリーンショットを送るだけでOK。")

    # ─── サマリー ───
    history, _bh_err = load_bid_history_cached()
    if _bh_err:
        st.error(f"入札実績取得エラー: {_bh_err}")
    if history:
        total  = len(history)
        wins   = sum(1 for h in history if h.get('result') == 'win')
        loses  = sum(1 for h in history if h.get('result') == 'lose')
        sched  = sum(1 for h in history if h.get('result') == 'scheduled')
        inv_jpy = sum(h.get('our_bid_jpy') or 0 for h in history if h.get('result') in ('win', 'scheduled'))

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("総入札数", f"{total}件")
        sc2.metric("🏆 落札", f"{wins}件")
        sc3.metric("❌ 落選", f"{loses}件")
        sc4.metric("総投資額", f"¥{inv_jpy:,}" if inv_jpy else "—")
        st.divider()

    # ─── 新規入力フォーム ───
    with st.expander("➕ 新規入力 / スクリーンショット登録", expanded=(len(history) == 0)):
        st.markdown("##### 案件情報")
        fc1, fc2 = st.columns(2)
        with fc1:
            f_title = st.text_input("コイン名 *", key="bh_title",
                                    placeholder="例: 1914 Prussia Germany Gold 20 Mark NGC MS63")
        with fc2:
            f_house = st.selectbox("オークション", AUCTION_HOUSES, key="bh_house")

        fc3, fc4 = st.columns(2)
        with fc3:
            f_url = st.text_input("URL", key="bh_url",
                                  placeholder="https://www.ebay.com/itm/...")
        with fc4:
            f_lot_no = st.text_input("Lot# / Item#", key="bh_lotno",
                                     placeholder="例: 236710306271")

        fc5, fc6 = st.columns(2)
        with fc5:
            f_bid_date = st.date_input("入札日", value=date.today(), key="bh_bid_date")
        with fc6:
            f_end_date = st.date_input("締切日", value=None, key="bh_end_date")

        st.markdown("##### 入札額")
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            f_bid_usd = st.number_input("入札額 (USD)", min_value=0.0, step=1.0, key="bh_bid_usd")
        with pc2:
            calc_jpy = int(f_bid_usd * usd_rate) if f_bid_usd else 0
            st.metric("円換算 (参考)", f"¥{calc_jpy:,}" if calc_jpy else "—")
        with pc3:
            f_bid_jpy = st.number_input("入札額 (円) ※手動上書き可",
                                        min_value=0, step=1000,
                                        value=calc_jpy, key="bh_bid_jpy")

        st.markdown("##### 結果")
        result_options = {'scheduled': '⏳ 予定', 'win': '🏆 落札', 'lose': '❌ 落選', 'cancelled': '🚫 取消'}
        f_result = st.radio("結果", options=list(result_options.keys()),
                            format_func=lambda x: result_options[x],
                            horizontal=True, key="bh_result")

        fc7, fc8 = st.columns(2)
        with fc7:
            f_final_usd = st.number_input("落札価格 (USD)",
                                          min_value=0.0, step=1.0, key="bh_final_usd",
                                          disabled=(f_result != 'win'))
        with fc8:
            f_final_jpy = st.number_input("落札価格 (円)",
                                          min_value=0, step=1000, key="bh_final_jpy",
                                          disabled=(f_result != 'win'))

        st.markdown("##### スクリーンショット")
        uploaded = st.file_uploader(
            "📎 ドラッグ＆ドロップ または クリックして選択",
            type=["png", "jpg", "jpeg"],
            key="bh_screenshot",
            help="CEOのスクショをそのままアップロードできます")

        f_notes = st.text_area("備考・コメント", key="bh_notes",
                               placeholder="例: キャップ推薦案件。直近3か月中央値¥200,000で割安感あり。")

        if st.button("💾 保存する", key="bh_save", type="primary"):
            if not f_title:
                st.error("コイン名は必須です")
            else:
                # スクリーンショット保存
                screenshot_path = None
                if uploaded:
                    file_id = uuid.uuid4().hex[:8]
                    save_path = SCREENSHOT_DIR / f"{file_id}_{uploaded.name}"
                    save_path.write_bytes(uploaded.read())
                    screenshot_path = str(save_path)

                entry = {
                    "lot_title":       f_title,
                    "auction_house":   f_house,
                    "lot_url":         f_url or None,
                    "lot_number":      f_lot_no or None,
                    "bid_date":        f_bid_date.isoformat(),
                    "auction_end_at":  f_end_date.isoformat() if f_end_date else None,
                    "our_bid_usd":     f_bid_usd or None,
                    "our_bid_jpy":     f_bid_jpy or None,
                    "result":          f_result,
                    "final_price_usd": f_final_usd if f_result == 'win' else None,
                    "final_price_jpy": f_final_jpy if f_result == 'win' else None,
                    "screenshot_path": screenshot_path,
                    "notes":           f_notes or None,
                    "recommended_by":  "cap",
                }
                new_id = save_bid_entry(entry)
                if new_id:
                    st.success(f"✅ 保存しました！ (ID: {new_id[:8]}...)")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("保存に失敗しました。DB接続を確認してください。")

    st.divider()

    # ─── 実績一覧 ───
    st.markdown("##### 実績一覧")
    if st.button("🔄 再読込", key="bh_reload"):
        st.cache_data.clear()
        st.rerun()

    if not history:
        st.info("まだ実績がありません。上のフォームから最初の入札を記録しましょう！")
        return

    for h in history:
        result_val = h.get('result') or 'scheduled'
        result_label, result_cls = RESULT_LABELS.get(result_val, (result_val, ''))
        bid_date_str  = str(h.get('bid_date') or '')[:10]
        lot_title     = h.get('lot_title') or '—'
        auction_house = h.get('auction_house') or '—'
        our_bid_usd   = h.get('our_bid_usd')
        our_bid_jpy   = h.get('our_bid_jpy')
        final_usd     = h.get('final_price_usd')
        final_jpy     = h.get('final_price_jpy')
        actual_profit = h.get('actual_profit_jpy')
        notes         = h.get('notes') or ''
        ss_path       = h.get('screenshot_path') or ''
        lot_url       = h.get('lot_url') or ''
        bid_id        = h.get('id') or ''

        bid_usd_str = f"${our_bid_usd:,.0f}" if our_bid_usd else (fmt_jpy(our_bid_jpy) if our_bid_jpy else '—')

        st.markdown('<div class="bid-card">', unsafe_allow_html=True)

        hc1, hc2 = st.columns([4, 1])
        with hc1:
            st.markdown(
                f'<span class="{result_cls}">{result_label}</span> &nbsp; '
                f'<b style="color:#f0f4ff">{lot_title[:65]}</b>',
                unsafe_allow_html=True)
            st.markdown(
                f'<span style="color:#94a3b8;font-size:.82rem">'
                f'🏛 {auction_house} &nbsp;|&nbsp; 📅 {bid_date_str} &nbsp;|&nbsp; 入札: {bid_usd_str}'
                f'</span>',
                unsafe_allow_html=True)
        with hc2:
            if lot_url:
                st.link_button("🔗", lot_url, use_container_width=True)

        # 落札時の詳細
        if result_val == 'win' and (final_usd or final_jpy):
            final_str = f"${final_usd:,.0f}" if final_usd else fmt_jpy(final_jpy)
            profit_str = (f"{'+'if actual_profit >= 0 else ''}¥{actual_profit:,}"
                          if actual_profit else '—')
            profit_color = '#22c55e' if (actual_profit or 0) >= 0 else '#ef4444'
            st.markdown(
                f"落札: **{final_str}** &nbsp;|&nbsp; "
                f"実利益: <span style='color:{profit_color}'>{profit_str}</span>",
                unsafe_allow_html=True)

        # 詳細展開
        with st.expander("詳細・編集"):
            if notes:
                st.caption(f"📝 {notes}")

            # スクリーンショット表示
            if ss_path and Path(ss_path).exists():
                st.image(ss_path, caption="スクリーンショット", width=400)
            elif ss_path:
                st.caption(f"📎 {Path(ss_path).name}（ファイルが見つかりません）")

            # 結果更新フォーム
            st.markdown("**結果を更新する**")
            uc1, uc2, uc3 = st.columns(3)
            with uc1:
                u_result = st.selectbox("結果", list(result_options.keys()),
                                        index=list(result_options.keys()).index(result_val),
                                        format_func=lambda x: result_options[x],
                                        key=f"u_result_{bid_id[:8]}")
            with uc2:
                u_final_usd = st.number_input("落札額(USD)", min_value=0.0,
                                              value=float(final_usd or 0),
                                              key=f"u_fusd_{bid_id[:8]}")
            with uc3:
                u_final_jpy = st.number_input("落札額(円)", min_value=0,
                                              value=int(final_jpy or 0),
                                              key=f"u_fjpy_{bid_id[:8]}")

            u_actual_cost = st.number_input("実際の仕入コスト(円・送料込)",
                                            min_value=0,
                                            value=int(h.get('actual_cost_jpy') or 0),
                                            key=f"u_cost_{bid_id[:8]}")
            u_notes = st.text_area("備考", value=notes, key=f"u_notes_{bid_id[:8]}")

            # 追加スクリーンショット
            u_ss = st.file_uploader("スクリーンショット更新",
                                    type=["png","jpg","jpeg"],
                                    key=f"u_ss_{bid_id[:8]}")

            if st.button("💾 更新保存", key=f"u_save_{bid_id[:8]}"):
                upd: dict = {
                    "result": u_result,
                    "notes":  u_notes or None,
                }
                if u_result == 'win':
                    upd["final_price_usd"] = u_final_usd or None
                    upd["final_price_jpy"] = u_final_jpy or None
                if u_actual_cost:
                    upd["actual_cost_jpy"] = u_actual_cost
                    if u_final_jpy and u_actual_cost:
                        upd["actual_profit_jpy"] = int(u_final_jpy) - int(u_actual_cost)
                if u_ss:
                    file_id = uuid.uuid4().hex[:8]
                    sp = SCREENSHOT_DIR / f"{file_id}_{u_ss.name}"
                    sp.write_bytes(u_ss.read())
                    upd["screenshot_path"] = str(sp)

                ok = update_bid_entry(bid_id, upd)
                if ok:
                    st.success("更新しました")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("更新失敗")

        st.markdown('</div>', unsafe_allow_html=True)
        st.write("")


# ════════════════════════════════════════════
# メイン
# ════════════════════════════════════════════
def main():
    rates    = load_rates()
    usd_rate = float(rates.get('usd_jpy_calc') or 150)
    gbp_rate = float(rates.get('gbp_jpy_calc') or 200)
    gold_g   = rates.get('gold_jpy_per_g')
    silver_g = rates.get('silver_jpy_per_g')
    rate_date = str(rates.get('rate_date', ''))[:10]

    gold_str   = f"{gold_g:,.0f}円/g"   if gold_g   else '—'
    silver_str = f"{silver_g:.1f}円/g"  if silver_g else '—'

    st.markdown('<div class="db-title">🪙 コイン仕入れDB</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="rate-bar">更新: {rate_date} &nbsp;|&nbsp; '
        f'Gold: {gold_str} &nbsp;|&nbsp; Silver: {silver_str} &nbsp;|&nbsp; '
        f'USD: {usd_rate:.0f} &nbsp;|&nbsp; GBP: {gbp_rate:.0f}</div>',
        unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["📊 相場DB", "🔍 CEO確認", "📋 入札実績"])

    with tab1:
        render_tab_db(rates)

    with tab2:
        try:
            render_tab_ceo()
        except Exception as e:
            st.error(f"CEO確認タブエラー: {e}")
            import traceback
            st.code(traceback.format_exc())

    with tab3:
        render_tab_bid_history(usd_rate)


if __name__ == '__main__':
    main()
