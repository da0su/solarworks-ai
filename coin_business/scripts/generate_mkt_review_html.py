"""
generate_mkt_review_html.py
============================
MARKETING_REVIEW状態の候補について審査票HTMLを生成し、
web/mkt_review_11.html に保存する。

機能:
  - 11件の全フィールドをカード形式で表示
  - delta_summary (何が同じで何が違うか) を自動生成
  - 承認/差し戻しボタン (Supabase REST API直接呼び出し)
  - 画像サムネイル + 商品リンク
  - localhost:8502/mkt_review_11.html でアクセス可能

実行:
  python scripts/generate_mkt_review_html.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client


# ================================================================
# delta_summary生成
# ================================================================
def _build_delta_summary(item: dict) -> str:
    """
    比較種別ごとに「何が同じで何が違うか」を説明するサマリー。
    """
    ctype = item.get('comparison_type') or 'NONE'
    title = item.get('title_snapshot') or ''
    year = item.get('year')
    grade = item.get('grade') or ''
    cert = item.get('cert_company') or ''
    country_raw = item.get('country') or ''
    material = item.get('material') or ''
    denomination = item.get('denomination') or ''

    ref_title = item.get('yahoo_ref_title') or ''
    ref_price = item.get('yahoo_ref_price_jpy')
    ref_date = item.get('yahoo_ref_date') or ''
    ref_grade = item.get('yahoo_ref_grade') or ''

    if ctype == 'EXACT':
        return (
            f"【完全一致】Yahoo落札履歴と同一グレード・同一銘柄。"
            f"参照: {ref_grade} / ¥{ref_price:,} ({ref_date})。"
            f"本品: {cert} {grade}。"
            f"価格根拠の信頼度: 高"
        ) if ref_price else "【完全一致】参照データあり。"

    elif ctype == 'GRADE_DELTA':
        ig = _grade_num(grade)
        rg = _grade_num(ref_grade)
        diff = ig - rg
        direction = f"本品が{abs(diff)}ポイント{'上位' if diff > 0 else '下位'}" if diff != 0 else "同ポイント"
        return (
            f"【グレード差】同銘柄・同年・グレードのみ異なる。"
            f"本品:{cert} {grade} / 参照:{ref_grade}({direction})。"
            f"同一コインの別グレードとして参照。"
            f"参照: ¥{ref_price:,} ({ref_date})。"
            f"グレード差による価格補正あり"
        ) if ref_price else "【グレード差】参照データあり。"

    elif ctype == 'YEAR_DELTA':
        ref_year_str = ref_title[:4] if ref_title else '不明'
        dy = ''
        if year and ref_title:
            try:
                ry = int(ref_title[:4])
                dy = f"年号差: 本品{year}年 / 参照{ry}年 ({abs(year-ry)}年差)。"
            except Exception:
                dy = f"本品{year}年。"
        return (
            f"【年号近似】同銘柄・同グレードだが年号が異なる。"
            f"{dy}"
            f"参照: {ref_grade} / ¥{ref_price:,} ({ref_date})。"
            f"同シリーズとして参照。年号違いは価格変動軽微とCAPが判断"
        ) if ref_price else "【年号近似】参照データあり。"

    elif ctype == 'TYPE_ONLY':
        return (
            f"【同種参照】年号・グレードが一致する直接参照なし。"
            f"同一コインタイプ（{material or '同素材'}）の別ロットを参照。"
            f"参照: {ref_grade} / ¥{ref_price:,} ({ref_date})。"
            f"同種最高値を参照のため価格根拠は弱め。差し戻し可"
        ) if ref_price else "【同種参照】参照データあり。"

    else:
        return "【参照なし】Yahoo落札履歴に該当なし。継続調査が必要。価格根拠不足のため差し戻し推奨。"


def _grade_num(g: str) -> int:
    _MAP = {
        "PF60":60,"PF61":61,"PF62":62,"PF63":63,"PF64":64,"PF65":65,
        "PF66":66,"PF67":67,"PF68":68,"PF69":69,"PF70":70,
        "PF69UC":69,"PF70UC":70,"PF68UC":68,
        "MS60":60,"MS61":61,"MS62":62,"MS63":63,"MS64":64,"MS65":65,
        "MS66":66,"MS67":67,"MS68":68,"MS69":69,"MS70":70,
    }
    if not g:
        return 0
    return _MAP.get(g.strip().upper().replace(" ","").replace("-",""), 0)


# ================================================================
# HTML生成
# ================================================================
def _cap_judgment_html(j: str) -> str:
    if j == 'CAP_BUY':
        return '<span class="badge badge-buy">✅ CAP_BUY</span>'
    elif j == 'CAP_HOLD':
        return '<span class="badge badge-hold">⏸ CAP_HOLD</span>'
    else:
        return '<span class="badge badge-ng">❌ CAP_NG</span>'


def _match_type_html(m: str) -> str:
    colors = {
        'EXACT':       ('#e0f2fe', '#0284c7', '完全一致'),
        'GRADE_DELTA': ('#fef9c3', '#a16207', 'グレード差'),
        'YEAR_DELTA':  ('#fef3c7', '#b45309', '年号差'),
        'TYPE_ONLY':   ('#fee2e2', '#b91c1c', '同種参照'),
        'NONE':        ('#f3f4f6', '#374151', '参照なし'),
    }
    bg, color, label = colors.get(m, ('#f3f4f6','#374151', m))
    return f'<span class="match-badge" style="background:{bg};color:{color};">{m} ({label})</span>'


def _yen(v) -> str:
    if v is None:
        return '--'
    return f'¥{int(v):,}'


def _usd(v) -> str:
    if v is None:
        return '--'
    return f'${float(v):.0f}'


def render_card(item: dict, rank: int, sb_url: str, sb_key: str) -> str:
    uuid = item['id']
    safe_id = uuid.replace('-', '_')
    title = item.get('title_snapshot') or '（タイトルなし）'
    src = item.get('source_group') or 'EBAY'
    house = item.get('auction_house') or 'EBAY'
    url = item.get('url') or '#'
    image_url = item.get('image_url') or ''
    cert = item.get('cert_company') or ''
    cert_no = item.get('cert_number') or '—'
    grade = item.get('grade') or '—'
    year = item.get('year') or '—'
    country = item.get('country') or '—'
    denom = item.get('denomination') or '—'
    material = item.get('material') or '—'
    bids = item.get('bid_count_snapshot')
    bids_str = str(bids) if bids is not None else '—'
    price_usd = item.get('price_snapshot_usd')
    price_usd_str = f'${price_usd:.2f}' if price_usd else '—'

    # Yahoo ref
    ref_id = item.get('yahoo_ref_id') or '—'
    ref_title = item.get('yahoo_ref_title') or '—'
    ref_price = item.get('yahoo_ref_price_jpy')
    ref_date = item.get('yahoo_ref_date') or '—'
    ref_grade = item.get('yahoo_ref_grade') or '—'

    # CAP price
    buy_limit_jpy = item.get('cap_bid_limit_jpy')
    buy_limit_usd = item.get('cap_bid_limit_usd')
    total_cost = item.get('total_cost_jpy')
    sell_est = item.get('estimated_sell_price_jpy')
    profit = item.get('expected_profit_jpy')
    roi = item.get('expected_roi_pct')
    roi_str = f'{roi:.1f}%' if roi is not None else '—'

    profit_color = '#16a34a' if (profit or 0) >= 20000 else ('#d97706' if (profit or 0) > 0 else '#dc2626')

    cap_j = item.get('cap_judgment') or '—'
    cap_comment = item.get('cap_comment') or '（コメントなし）'
    evidence = item.get('evidence_status') or '—'
    match_type = item.get('comparison_type') or 'NONE'

    delta_summary = _build_delta_summary(item)

    thumb_html = (
        f'<img src="{image_url}" alt="coin" class="thumb" onerror="this.src=\'\';">'
        if image_url else
        '<div class="thumb-placeholder">🪙</div>'
    )

    # House badge
    house_label = {'EBAY':'eBay','HERITAGE':'★Heritage','SPINK':'Spink','NOBLE':'Noble'}.get(house, house)
    house_color = '#f59e0b' if house == 'HERITAGE' else '#60a5fa'

    return f'''
<div class="card" id="card-{safe_id}">
  <div class="card-header">
    <div class="rank">#{rank}</div>
    {thumb_html}
    <div class="card-meta">
      <div class="badges">
        <span class="badge badge-src" style="color:{house_color};">{house_label}</span>
        {_cap_judgment_html(cap_j)}
        {_match_type_html(match_type)}
        <span class="badge badge-evidence">🗂 {evidence}</span>
      </div>
      <div class="card-title">{title[:100]}</div>
      <div class="card-sub">{cert} {grade} | {year}年 | {country} | {material} | {denom}</div>
      <div class="quick-profit" style="color:{profit_color};">
        想定利益 {_yen(profit)} (ROI {roi_str}) &nbsp;|&nbsp; 仕入上限 {_yen(buy_limit_jpy)} ({_usd(buy_limit_usd)})
      </div>
    </div>
    <a href="{url}" target="_blank" class="ext-link">🔗 商品を見る</a>
  </div>

  <div class="card-body">

    <!-- Row 1: 商品詳細 -->
    <div class="section-title">📋 商品詳細</div>
    <table class="info-table">
      <tr><th>marketplace</th><td>{src} / {house}</td><th>cert_company</th><td>{cert}</td></tr>
      <tr><th>cert_number</th><td>{cert_no}</td><th>grade</th><td>{grade}</td></tr>
      <tr><th>year</th><td>{year}</td><th>denomination</th><td>{denom}</td></tr>
      <tr><th>material</th><td>{material}</td><th>country</th><td>{country}</td></tr>
      <tr><th>bid_count</th><td>{bids_str}</td><th>現在価格(USD)</th><td>{price_usd_str}</td></tr>
      <tr><th>URL</th><td colspan="3"><a href="{url}" target="_blank" style="color:#60a5fa;">{url[:70]}...</a></td></tr>
    </table>

    <!-- Row 2: Yahoo比較元 -->
    <div class="section-title">📊 Yahoo比較元</div>
    <table class="info-table yahoo-ref">
      <tr><th>管理番号</th><td>{ref_id[:36]}</td><th>比較種別</th><td>{_match_type_html(match_type)}</td></tr>
      <tr><th>Yahoo落札タイトル</th><td colspan="3" class="ref-title">{ref_title[:80]}</td></tr>
      <tr>
        <th>落札価格</th><td class="price-cell">{_yen(ref_price)}</td>
        <th>落札日 / グレード</th><td>{ref_date} / {ref_grade}</td>
      </tr>
      <tr><th colspan="4" class="delta-label">delta_summary (何が同じで何が違うか)</th></tr>
      <tr><td colspan="4" class="delta-cell">{delta_summary}</td></tr>
    </table>

    <!-- Row 3: 価格根拠 -->
    <div class="section-title">💴 価格根拠（CEO確定計算式）</div>
    <div class="price-grid">
      <div class="price-box">
        <div class="price-label">buy_limit（仕入上限）</div>
        <div class="price-value">{_yen(buy_limit_jpy)}<br><span class="usd">{_usd(buy_limit_usd)}</span></div>
      </div>
      <div class="price-box">
        <div class="price-label">total_cost_estimate</div>
        <div class="price-value">{_yen(total_cost)}</div>
      </div>
      <div class="price-box">
        <div class="price-label">resale_estimate</div>
        <div class="price-value">{_yen(sell_est)}</div>
      </div>
      <div class="price-box" style="border-color:{profit_color};">
        <div class="price-label">profit_estimate</div>
        <div class="price-value" style="color:{profit_color};">{_yen(profit)}<br><span class="roi">ROI {roi_str}</span></div>
      </div>
    </div>

    <!-- Row 4: CAPコメント -->
    <div class="section-title">💬 CAPコメント</div>
    <div class="cap-comment">{cap_comment}</div>

    <!-- Row 5: 審査ボタン -->
    <div class="action-area" id="action-{safe_id}">
      <button class="btn-approve" onclick="doApprove('{uuid}','{safe_id}')">✅ 承認 → CEO確認へ</button>
      <button class="btn-return" onclick="showReturn('{safe_id}')">❌ 差し戻す</button>
    </div>
    <div class="return-area" id="return-{safe_id}" style="display:none;">
      <textarea id="rtext-{safe_id}" placeholder="差し戻し理由（必須）" rows="3"></textarea>
      <button class="btn-return-confirm" onclick="doReturn('{uuid}','{safe_id}')">差し戻し確定</button>
    </div>
    <div class="result-msg" id="result-{safe_id}"></div>

  </div>
</div>
'''


def generate_html(rows: list, sb_url: str, sb_key: str) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    cards_html = '\n'.join(render_card(r, i+1, sb_url, sb_key) for i, r in enumerate(rows))

    total = len(rows)
    ebay_count = sum(1 for r in rows if r.get('source_group') == 'EBAY')
    world_count = total - ebay_count

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>マーケ中間審査票 — MARKETING_REVIEW {total}件 ({now})</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0f172a; color:#e2e8f0; padding:16px; }}
h1 {{ font-size:22px; color:#fbbf24; margin-bottom:6px; }}
.meta {{ font-size:12px; color:#64748b; margin-bottom:16px; }}
.kpi-row {{ display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }}
.kpi {{ background:#1e293b; border-radius:10px; padding:14px 24px; text-align:center; border:1px solid #334155; }}
.kpi .num {{ font-size:28px; font-weight:bold; color:#fbbf24; }}
.kpi .lbl {{ font-size:11px; color:#94a3b8; margin-top:4px; }}
.info-banner {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:12px 16px; margin-bottom:20px; font-size:12px; color:#94a3b8; line-height:1.6; }}
.info-banner strong {{ color:#fbbf24; }}

.card {{ background:#1e293b; border-radius:14px; margin-bottom:20px; border:1px solid #334155; overflow:hidden; }}
.card.approved {{ border-color:#16a34a; }}
.card.returned {{ border-color:#dc2626; }}

.card-header {{ display:flex; align-items:flex-start; gap:12px; padding:14px; background:#1a2744; }}
.rank {{ font-size:13px; color:#64748b; flex-shrink:0; min-width:24px; }}
.thumb {{ width:64px; height:64px; object-fit:cover; border-radius:8px; border:1px solid #334155; flex-shrink:0; }}
.thumb-placeholder {{ width:64px; height:64px; border-radius:8px; background:#0f172a; border:1px solid #334155; display:flex; align-items:center; justify-content:center; font-size:24px; flex-shrink:0; }}
.card-meta {{ flex:1; min-width:0; }}
.badges {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:6px; }}
.badge {{ font-size:11px; padding:2px 8px; border-radius:10px; font-weight:bold; white-space:nowrap; }}
.badge-buy {{ background:#052e16; color:#4ade80; border:1px solid #4ade80; }}
.badge-hold {{ background:#422006; color:#fbbf24; border:1px solid #d97706; }}
.badge-ng {{ background:#450a0a; color:#f87171; border:1px solid #f87171; }}
.badge-src {{ background:#1e293b; border:1px solid #334155; }}
.badge-evidence {{ background:#1e293b; color:#94a3b8; border:1px solid #334155; }}
.match-badge {{ font-size:11px; padding:2px 8px; border-radius:10px; font-weight:bold; }}
.card-title {{ font-size:13px; font-weight:bold; margin-bottom:4px; word-break:break-word; }}
.card-sub {{ font-size:11px; color:#94a3b8; margin-bottom:4px; }}
.quick-profit {{ font-size:12px; font-weight:bold; margin-top:4px; }}
.ext-link {{ color:#60a5fa; text-decoration:none; font-size:12px; white-space:nowrap; flex-shrink:0; padding-top:4px; }}
.ext-link:hover {{ text-decoration:underline; }}

.card-body {{ padding:14px; }}
.section-title {{ font-size:12px; font-weight:bold; color:#fbbf24; margin:14px 0 6px; padding-bottom:4px; border-bottom:1px solid #334155; }}
.section-title:first-child {{ margin-top:0; }}

.info-table {{ width:100%; border-collapse:collapse; font-size:12px; margin-bottom:4px; }}
.info-table th {{ background:#0f172a; color:#94a3b8; padding:5px 10px; text-align:left; font-weight:normal; width:20%; white-space:nowrap; }}
.info-table td {{ padding:5px 10px; color:#e2e8f0; word-break:break-word; }}
.info-table tr:nth-child(even) td {{ background:#162032; }}
.info-table .price-cell {{ font-size:14px; font-weight:bold; color:#4ade80; }}
.info-table .ref-title {{ color:#93c5fd; font-size:11px; }}
.info-table .delta-label {{ background:#1a2744; color:#fbbf24; font-size:11px; padding:6px 10px; }}
.info-table .delta-cell {{ background:#0a1929; padding:10px; font-size:12px; color:#cbd5e1; line-height:1.7; }}
.yahoo-ref {{ border:1px solid #1e3a5f; border-radius:6px; }}

.price-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:6px; }}
@media (max-width:700px) {{ .price-grid {{ grid-template-columns:repeat(2,1fr); }} }}
.price-box {{ background:#0f172a; border-radius:8px; padding:10px 14px; text-align:center; border:1px solid #334155; }}
.price-label {{ font-size:10px; color:#64748b; margin-bottom:4px; }}
.price-value {{ font-size:16px; font-weight:bold; }}
.price-value .usd {{ font-size:11px; color:#94a3b8; font-weight:normal; }}
.price-value .roi {{ font-size:11px; font-weight:normal; }}

.cap-comment {{ background:#1a2744; border-radius:8px; padding:12px; font-size:12px; color:#cbd5e1; line-height:1.8; white-space:pre-wrap; word-break:break-all; margin-bottom:12px; }}

.action-area {{ display:flex; gap:10px; }}
.btn-approve {{ flex:1; padding:14px; border:none; border-radius:8px; background:#ffd700; color:#000; font-size:14px; font-weight:bold; cursor:pointer; }}
.btn-approve:hover {{ background:#ffc300; }}
.btn-return {{ flex:1; padding:14px; border:1px solid #7f1d1d; border-radius:8px; background:#1e0a0a; color:#fca5a5; font-size:14px; font-weight:bold; cursor:pointer; }}
.btn-return:hover {{ background:#2a0a0a; }}
.return-area {{ margin-top:8px; }}
.return-area textarea {{ width:100%; padding:10px; border:1px solid #444; border-radius:6px; background:#1e293b; color:#fff; font-size:13px; resize:vertical; }}
.btn-return-confirm {{ width:100%; margin-top:6px; padding:12px; border:none; border-radius:6px; background:#7f1d1d; color:#fca5a5; font-weight:bold; font-size:13px; cursor:pointer; }}
.result-msg {{ margin-top:8px; font-size:13px; text-align:center; padding:8px; border-radius:6px; display:none; }}
.result-msg.success {{ background:#052e16; color:#4ade80; display:block; }}
.result-msg.error {{ background:#450a0a; color:#f87171; display:block; }}
</style>
</head>
<body>

<h1>📋 マーケ中間審査票</h1>
<div class="meta">生成日時: {now} &nbsp;|&nbsp; 対象: MARKETING_REVIEW {total}件 (eBay:{ebay_count}件 / 世界オークション:{world_count}件)</div>

<div class="kpi-row">
  <div class="kpi"><div class="num" id="cnt-total">{total}</div><div class="lbl">確認待ち合計</div></div>
  <div class="kpi"><div class="num" id="cnt-ebay">{ebay_count}</div><div class="lbl">eBay</div></div>
  <div class="kpi"><div class="num" id="cnt-world">{world_count}</div><div class="lbl">世界オークション</div></div>
  <div class="kpi"><div class="num" style="color:#4ade80;" id="cnt-approved">0</div><div class="lbl">承認済</div></div>
  <div class="kpi"><div class="num" style="color:#f87171;" id="cnt-returned">0</div><div class="lbl">差し戻し</div></div>
</div>

<div class="info-banner">
  <strong>審査フロー:</strong> CAP → <strong>マーケ（本画面）</strong> → CEO確認<br>
  ✅ 承認 → MARKETING_APPROVED → CEO確認タブへ自動移動<br>
  ❌ 差し戻し → MARKETING_RETURNED → CAPが再修正後に再提出<br>
  <strong>差し戻し条件:</strong> Yahoo参照なし / 利益根拠不足 / TYPE_ONLYで価格根拠弱い / スラブ画像未確認
</div>

{cards_html}

<script>
const SB_URL = '{sb_url}';
const SB_KEY = '{sb_key}';
const SB_HDR = {{ 'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY, 'Content-Type': 'application/json', 'Prefer': 'return=minimal' }};

let approved = 0;
let returned = 0;

function showReturn(safeId) {{
  const el = document.getElementById('return-' + safeId);
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}}

async function doApprove(uuid, safeId) {{
  const btn = document.querySelector('#card-' + safeId + ' .btn-approve');
  if (btn) btn.disabled = true;
  const res = await fetch(SB_URL + '/rest/v1/ceo_review_log?id=eq.' + encodeURIComponent(uuid), {{
    method: 'PATCH',
    headers: SB_HDR,
    body: JSON.stringify({{
      marketing_status: 'MARKETING_APPROVED',
      marketing_reviewed_at: new Date().toISOString(),
      marketing_reviewed_by: 'mkt'
    }})
  }});
  const card = document.getElementById('card-' + safeId);
  const msg  = document.getElementById('result-' + safeId);
  if (res.ok || res.status === 204) {{
    card.classList.add('approved');
    msg.className = 'result-msg success';
    msg.textContent = '✅ 承認しました — CEO確認タブへ移動しました';
    document.getElementById('action-' + safeId).style.display = 'none';
    approved++;
    document.getElementById('cnt-approved').textContent = approved;
  }} else {{
    msg.className = 'result-msg error';
    msg.textContent = 'エラー: ' + res.status;
    if (btn) btn.disabled = false;
  }}
}}

async function doReturn(uuid, safeId) {{
  const text = document.getElementById('rtext-' + safeId).value.trim();
  if (!text) {{ alert('差し戻し理由を入力してください'); return; }}
  const btn = document.querySelector('#return-' + safeId + ' .btn-return-confirm');
  if (btn) btn.disabled = true;
  const res = await fetch(SB_URL + '/rest/v1/ceo_review_log?id=eq.' + encodeURIComponent(uuid), {{
    method: 'PATCH',
    headers: SB_HDR,
    body: JSON.stringify({{
      marketing_status: 'MARKETING_RETURNED',
      marketing_comment: text,
      marketing_reviewed_at: new Date().toISOString(),
      marketing_reviewed_by: 'mkt'
    }})
  }});
  const card = document.getElementById('card-' + safeId);
  const msg  = document.getElementById('result-' + safeId);
  if (res.ok || res.status === 204) {{
    card.classList.add('returned');
    msg.className = 'result-msg error';
    msg.textContent = '❌ 差し戻しました: ' + text;
    document.getElementById('action-' + safeId).style.display = 'none';
    document.getElementById('return-' + safeId).style.display = 'none';
    returned++;
    document.getElementById('cnt-returned').textContent = returned;
  }} else {{
    msg.className = 'result-msg error';
    msg.textContent = 'エラー: ' + res.status;
    if (btn) btn.disabled = false;
  }}
}}
</script>
</body>
</html>
'''


def main():
    c = get_client()

    # Supabase URL/KEY
    sb_url = os.environ.get('SUPABASE_URL', '')
    sb_key = os.environ.get('SUPABASE_KEY', '')

    if not sb_url or not sb_key:
        # Try loading from .env
        env_path = PROJECT_ROOT / '.env'
        if env_path.exists():
            for line in env_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line.startswith('SUPABASE_URL='):
                    sb_url = line.split('=', 1)[1].strip().strip('"').strip("'")
                elif line.startswith('SUPABASE_KEY='):
                    sb_key = line.split('=', 1)[1].strip().strip('"').strip("'")

    rows = (c.table('ceo_review_log')
              .select('*')
              .eq('marketing_status', 'MARKETING_REVIEW')
              .order('snapshot_score', desc=True)
              .execute().data)

    print(f'MARKETING_REVIEW: {len(rows)} items')

    html = generate_html(rows, sb_url, sb_key)

    out_path = PROJECT_ROOT / 'web' / 'mkt_review_11.html'
    out_path.write_text(html, encoding='utf-8')
    print(f'Generated: {out_path}')
    print(f'Access at: http://localhost:8502/mkt_review_11.html')
    print(f'File size: {len(html):,} chars')


if __name__ == '__main__':
    main()
