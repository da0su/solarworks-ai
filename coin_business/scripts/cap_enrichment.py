"""
coin_business/scripts/cap_enrichment.py
========================================
ceo_review_log の候補に対してCAPが事前審査を行い、審査票を作成する。

処理フロー:
  1. ceo_review_log から対象アイテムを取得
  2. market_transactions (Yahoo 24,961件) で参照価格を検索
  3. 利益計算 (CEO確定式: 粗利率15%/関税×1.1/US転送¥2000/国内¥750/ヤフオク10%)
  4. CAP判定 (CAP_BUY/CAP_HOLD/CAP_NG) と CAPコメント生成
  5. カテゴリ分類 (CEO_REVIEW/INVESTIGATION/OBSERVATION)
  6. ceo_review_log に保存

比較種別 (comparison_type):
  EXACT       : cert_number + grader 完全一致
  YEAR_DELTA  : 同コイン種、年号±5以内
  GRADE_DELTA : 同コイン種・同年、グレード違い
  TYPE_ONLY   : 同国・同面額・同素材、年号/グレードなし
  NONE        : 参照データなし → INVESTIGATION

CLI:
  python cap_enrichment.py --help
  python cap_enrichment.py --source EBAY  --bucket Top20 --dry-run
  python cap_enrichment.py --source WORLD --bucket Top20 --dry-run
  python cap_enrichment.py --source EBAY  --bucket Top20
  python cap_enrichment.py --source all   --bucket Top20
  python cap_enrichment.py --id <ceo_review_log.id>
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)

# ================================================================
# 定数 (CEO確定利益計算式)
# ================================================================
CUSTOMS_RATE          = 1.10   # 関税概算 (10%)
US_FORWARDING_JPY     = 2_000  # US転送費
DOMESTIC_SHIPPING_JPY = 750    # 国内送料・倉庫
YAHOO_FEE_RATE        = 0.10   # ヤフオク手数料
MIN_GROSS_MARGIN      = 0.15   # 粗利率下限
MIN_PROFIT_JPY        = 20_000 # 最低利益
USD_TO_JPY            = 150    # 為替レート (フォールバック)
FX_RATE               = USD_TO_JPY

# 変数コスト合計 (転送+国内)
FIXED_COST_JPY = US_FORWARDING_JPY + DOMESTIC_SHIPPING_JPY  # 2,750

# ================================================================
# グレードランク (数値比較用)
# ================================================================
_GRADE_RANK: dict[str, int] = {
    "P1":1,"FR2":2,"AG3":3,"G4":4,"G6":6,"VG8":8,"VG10":10,
    "F12":12,"F15":15,"VF20":20,"VF25":25,"VF30":30,"VF35":35,
    "EF40":40,"EF45":45,"AU50":50,"AU53":53,"AU55":55,"AU58":58,
    "MS60":60,"MS61":61,"MS62":62,"MS63":63,"MS64":64,"MS65":65,
    "MS66":66,"MS67":67,"MS68":68,"MS69":69,"MS70":70,
    "PF60":60,"PF61":61,"PF62":62,"PF63":63,"PF64":64,"PF65":65,
    "PF66":66,"PF67":67,"PF68":68,"PF69":69,"PF70":70,
    # variant forms
    "PF69UC":69,"PF70UC":70,"PF68UC":68,"MS69FS":69,"MS70FS":70,
}

def _grade_rank(g: str | None) -> int:
    if not g:
        return 0
    key = g.strip().upper().replace(" ","").replace("-","")
    return _GRADE_RANK.get(key, 0)


# ================================================================
# 利益計算
# ================================================================
def calc_cap_bid_limit_jpy(sell_price_jpy: int) -> Optional[int]:
    """
    仕入上限（円）を計算する。
    原価上限 = min(revenue - MIN_PROFIT, revenue × 0.85)
    仕入相場上限 = (原価上限 - FIXED_COST) / CUSTOMS_RATE
    """
    if not sell_price_jpy or sell_price_jpy <= 0:
        return None
    revenue = sell_price_jpy * (1.0 - YAHOO_FEE_RATE)
    cost_limit = min(revenue - MIN_PROFIT_JPY, revenue * (1.0 - MIN_GROSS_MARGIN))
    if cost_limit <= FIXED_COST_JPY:
        return None
    return int((cost_limit - FIXED_COST_JPY) / CUSTOMS_RATE)


def calc_expected_profit_jpy(
    sell_price_jpy: int,
    bid_jpy: int,
) -> int:
    """入札額 bid_jpy での想定利益を計算する。"""
    revenue = sell_price_jpy * (1.0 - YAHOO_FEE_RATE)
    total_cost = bid_jpy * CUSTOMS_RATE + FIXED_COST_JPY
    return int(revenue - total_cost)


def calc_roi_pct(sell_price_jpy: int, bid_jpy: int) -> float:
    """ROI (%) = profit / revenue × 100"""
    revenue = sell_price_jpy * (1.0 - YAHOO_FEE_RATE)
    if revenue <= 0:
        return 0.0
    profit = calc_expected_profit_jpy(sell_price_jpy, bid_jpy)
    return round(profit / revenue * 100, 1)


# ================================================================
# 仕入判定ルール
# ================================================================
#  CAP_BUY  : 利益見込み >= MIN_PROFIT_JPY
#  CAP_HOLD : 利益見込み > 0  AND < MIN_PROFIT_JPY
#  CAP_NG   : 利益見込み <= 0  OR 参照データなし（NONE） OR 非コイン商品
def _cap_judgment(expected_profit: Optional[int], has_ref: bool) -> str:
    if not has_ref or expected_profit is None:
        return 'CAP_NG'
    if expected_profit >= MIN_PROFIT_JPY:
        return 'CAP_BUY'
    if expected_profit > 0:
        return 'CAP_HOLD'
    return 'CAP_NG'


def _category(judgment: str, has_ref: bool) -> str:
    if not has_ref:
        return 'INVESTIGATION'
    if judgment == 'CAP_BUY':
        return 'CEO_REVIEW'
    if judgment == 'CAP_HOLD':
        return 'CEO_REVIEW'
    return 'OBSERVATION'


# ================================================================
# Yahoo 参照検索
# ================================================================
# キーワードマッピング: title キーワード → Yahoo 検索語
_KEYWORD_MAP: list[tuple[str, str]] = [
    # eBay US coins
    ('Silver Eagle',          'Silver Eagle'),
    ('American Eagle',        'Silver Eagle'),
    ('シルバーイーグル',          'Silver Eagle'),
    ('Panda',                 'Panda'),
    ('パンダ',                  'Panda'),
    ('Morgan',                'Morgan'),
    ('モルガン',                 'Morgan'),
    ('Franklin',              'Franklin'),
    ('フランクリン',              'Franklin'),
    ('Walking Liberty',       'Walking'),
    ('ウォーキング',              'Walking'),
    ('Kennedy',               'Kennedy'),
    ('ケネディ',                 'Kennedy'),
    ('Saint-Gaudens',         'Saint-Gaudens'),
    ('Double Eagle',          'Double Eagle'),
    ('ダブルイーグル',            'Double Eagle'),
    ('Liberty',               'Liberty'),
    ('リバティ',                 'Liberty'),
    ('Kangaroo',              'Kangaroo'),
    ('カンガルー',               'Kangaroo'),
    ('Maple',                 'Maple'),
    ('メイプル',                 'Maple'),
    ('Britannia',             'Britannia'),
    ('ブリタニア',               'Britannia'),
    ('Krugerrand',            'Krugerrand'),
    ('クルーガーランド',           'Krugerrand'),
    ('Sovereign',             'Sovereign'),
    ('ソブリン',                 'Sovereign'),
    ('Habsburg',              'Habsburg'),
    # China/World
    ('China',                 'China'),
    ('Chinese',               'China'),
    ('Tibet',                 'Tibet'),
    ('チベット',                 'Tibet'),
    ('Latvia',                'Latvia'),
    ('Morocco',               'Morocco'),
    ('Russia',                'Russia'),
    ('ロシア',                  'Russia'),
]

# 単独でNG判定する商品 (lot, collection, novelty)
_NG_KEYWORDS = [
    'estate', 'collection', 'lot', 'bank roll', 'proof set', 'mint set',
    '1/200 gold gram', 'gram round', 'skull', 'day of the dead',
]


def _is_ng_item(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _NG_KEYWORDS)


def _extract_keyword(title: str) -> Optional[str]:
    """タイトルから Yahoo 検索キーワードを抽出する。"""
    for trigger, kw in _KEYWORD_MAP:
        if trigger.lower() in title.lower():
            return kw
    return None


def search_yahoo_reference(
    c,
    title: str,
    year: Optional[int],
    grade: Optional[str],
    material: Optional[str],
    country: Optional[str],
    max_rows: int = 20,
) -> list[dict]:
    """
    market_transactions から参照レコードを検索し、スコア順に返す。
    """
    kw = _extract_keyword(title)
    if not kw:
        return []

    # ilike 検索
    q = c.table('market_transactions').select(
        'id,title,year,grade,grader,price_jpy,sold_date,country,material,denomination'
    ).ilike('title', f'%{kw}%').order('sold_date', desc=True).limit(50).execute()

    rows = q.data
    if not rows:
        return []

    # スコアリング
    def _score(r: dict) -> tuple:
        s_yr = 0
        s_gr = 0
        # 年号一致
        if year and r.get('year') == year:
            s_yr = 3
        elif year and r.get('year') and abs((r['year'] or 0) - year) <= 5:
            s_yr = 1
        # グレード一致
        rg = _grade_rank(r.get('grade'))
        cg = _grade_rank(grade)
        if rg and cg:
            diff = abs(rg - cg)
            if diff == 0:
                s_gr = 3
            elif diff <= 2:
                s_gr = 2
            elif diff <= 5:
                s_gr = 1
        # 素材一致
        s_mt = 0
        if material and r.get('material'):
            # 簡易マッチ (金/gold, 銀/silver)
            m1 = material.lower()
            m2 = r['material'].lower() if r['material'] else ''
            if (('gold' in m1 and ('金' in m2 or 'gold' in m2)) or
                ('silver' in m1 and ('銀' in m2 or 'silver' in m2)) or
                ('platinum' in m1 and ('プラチナ' in m2 or 'pt' in m2.lower()))):
                s_mt = 1
        return (s_yr + s_gr + s_mt, r.get('price_jpy', 0) or 0)

    rows.sort(key=_score, reverse=True)
    return rows[:max_rows]


def _determine_comparison_type(
    ref: dict,
    year: Optional[int],
    grade: Optional[str],
    cert_number: Optional[str],
) -> str:
    """参照レコードとの比較種別を判定する。"""
    # EXACT: cert match (cert_number は市場取引DBでは保持していないが将来拡張用)
    if cert_number and ref.get('cert_number') and cert_number == ref['cert_number']:
        return 'EXACT'

    yr_ok = (year and ref.get('year') and year == ref['year'])
    yr_delta = (
        year and ref.get('year') and year != ref['year']
        and abs(year - ref['year']) <= 5
    )
    gr_ok = (_grade_rank(grade) and _grade_rank(ref.get('grade')) and
             abs(_grade_rank(grade) - _grade_rank(ref.get('grade'))) == 0)
    gr_delta = (_grade_rank(grade) and _grade_rank(ref.get('grade')) and
                0 < abs(_grade_rank(grade) - _grade_rank(ref.get('grade'))) <= 5)

    if yr_ok and gr_ok:
        return 'EXACT'
    if yr_ok and gr_delta:
        return 'GRADE_DELTA'
    if yr_delta and (gr_ok or gr_delta):
        return 'YEAR_DELTA'
    if yr_ok:
        return 'GRADE_DELTA'
    return 'TYPE_ONLY'


# ================================================================
# CAPコメント生成
# ================================================================
def _build_cap_comment(
    title: str,
    comparison_type: str,
    yahoo_ref_title: Optional[str],
    yahoo_ref_price_jpy: Optional[int],
    yahoo_ref_date: Optional[str],
    yahoo_ref_grade: Optional[str],
    ref_year: Optional[int],
    item_year: Optional[int],
    item_grade: Optional[str],
    cap_bid_limit_jpy: Optional[int],
    cap_bid_limit_usd: Optional[float],
    expected_profit_jpy: Optional[int],
    expected_roi_pct: Optional[float],
    cap_judgment: str,
    price_snapshot_jpy: Optional[int],
    source_group: str,
) -> str:
    """CAPコメント (1-3文) を生成する。"""
    parts = []

    # ① 参照情報
    if yahoo_ref_price_jpy:
        ref_label = f"Yahoo参照¥{yahoo_ref_price_jpy:,} ({yahoo_ref_date or '日付不明'} / {yahoo_ref_grade or 'グレード不明'})"
        if comparison_type == 'EXACT':
            parts.append(f"同一コイン確認 ({comparison_type})。{ref_label}。")
        elif comparison_type == 'YEAR_DELTA':
            dy = abs((item_year or 0) - (ref_year or 0))
            parts.append(f"年号±{dy}年差の近似一致 ({comparison_type})。{ref_label}。")
        elif comparison_type == 'GRADE_DELTA':
            ig = _grade_rank(item_grade)
            rg = _grade_rank(yahoo_ref_grade)
            diff_str = f"本品{item_grade or '?'}/参照{yahoo_ref_grade or '?'}"
            parts.append(f"グレード差あり ({comparison_type}: {diff_str})。{ref_label}。")
        elif comparison_type == 'TYPE_ONLY':
            parts.append(f"同種コイン参照 ({comparison_type})。{ref_label}。")
    else:
        parts.append(f"Yahoo参照なし。類似取引不足、要継続調査。")

    # ② 価格・利益
    if cap_bid_limit_jpy and expected_profit_jpy is not None:
        if source_group == 'EBAY':
            bid_usd_str = f"(≒${cap_bid_limit_usd:.0f})" if cap_bid_limit_usd else ""
            parts.append(
                f"仕入上限¥{cap_bid_limit_jpy:,}{bid_usd_str}まで。"
                f"想定利益¥{expected_profit_jpy:,}(ROI {expected_roi_pct:.1f}%)。"
            )
        else:
            auction_price = f"現在入札額¥{price_snapshot_jpy:,}で" if price_snapshot_jpy else ""
            parts.append(
                f"{auction_price}仕入上限¥{cap_bid_limit_jpy:,}。"
                f"想定利益¥{expected_profit_jpy:,}(ROI {expected_roi_pct:.1f}%)。"
            )
    elif not yahoo_ref_price_jpy:
        parts.append(f"Yahoo相場不明のため利益計算不可。現地調査後に再評価。")

    # ③ CAP結論
    judgment_map = {
        'CAP_BUY':  '✅ CAP_BUY：仕入推奨。',
        'CAP_HOLD': '⚠️ CAP_HOLD：条件次第で検討可。',
        'CAP_NG':   '❌ CAP_NG：現時点では見送り推奨。',
    }
    parts.append(judgment_map.get(cap_judgment, cap_judgment))

    return ' '.join(parts)


# ================================================================
# メイン処理
# ================================================================
def run_cap_enrichment(
    source: str = 'EBAY',       # 'EBAY' / 'WORLD' / 'all'
    bucket: str = 'Top20',      # 'Top20' / 'Top50' / 'Top100' / 'all'
    dry_run: bool = True,
    item_id: Optional[str] = None,
    verbose: bool = True,
) -> dict:
    """
    CAP分析を実行し、ceo_review_log を更新する。

    Returns:
      {processed, ceo_review, investigation, observation, errors}
    """
    c = get_client()

    # --------------------------------------------------------
    # 対象取得
    # --------------------------------------------------------
    q = c.table('ceo_review_log').select('*')
    if item_id:
        q = q.eq('id', item_id)
    else:
        if source != 'all':
            q = q.eq('source_group', source.upper())
        if bucket != 'all':
            q = q.eq('review_bucket', bucket)

    # RETURNED 以外を対象 (既に差し戻し済みも再処理可)
    rows = q.order('snapshot_score', desc=True).execute().data
    if not rows:
        logger.warning('対象レコードなし')
        return {'processed': 0, 'ceo_review': 0, 'investigation': 0, 'observation': 0, 'errors': 0}

    if verbose:
        print(f"\n{'='*60}")
        print(f" CAP Enrichment 実行中 source={source} bucket={bucket} dry_run={dry_run}")
        print(f" 対象: {len(rows)}件")
        print(f"{'='*60}\n")

    stats = {'processed': 0, 'ceo_review': 0, 'investigation': 0, 'observation': 0, 'errors': 0}

    for item in rows:
        stats['processed'] += 1
        item_id_val = item['id']
        title = item.get('title_snapshot', '') or ''
        year = item.get('year')
        grade = item.get('grade') or ''
        material = item.get('material') or ''
        country = item.get('country') or ''
        cert_co = item.get('cert_company') or ''
        price_usd = item.get('price_snapshot_usd')
        price_jpy = item.get('price_snapshot_jpy')
        src_group = item.get('source_group', 'EBAY')
        auction_house = item.get('auction_house', '')
        url = item.get('url', '') or ''

        if verbose:
            print(f"[{stats['processed']:2d}/{len(rows)}] {title[:60]}")
            print(f"       {country} {year} {grade} {material} | Sc={item.get('snapshot_score')}")

        # NG品チェック（非コイン商品）
        if _is_ng_item(title):
            update = {
                'comparison_type':  'NONE',
                'cap_judgment':     'CAP_NG',
                'cap_comment':      'CAP_NG: Non-coin item (lot/collection/novelty). Not sourcing target.',
                'category':         'OBSERVATION',
                'updated_at':       datetime.now(timezone.utc).isoformat(),
            }
            if verbose:
                print(f"       -> NG_ITEM OBSERVATION")
            if not dry_run:
                c.table('ceo_review_log').update(update).eq('id', item_id_val).execute()
            stats['observation'] += 1
            continue

        # --------------------------------------------------------
        # Yahoo 参照検索
        # --------------------------------------------------------
        refs = search_yahoo_reference(c, title, year, grade, material, country)
        best = refs[0] if refs else None

        # --------------------------------------------------------
        # 利益計算
        # --------------------------------------------------------
        sell_price_jpy = None
        ref_id = None
        ref_title = None
        ref_price_jpy = None
        ref_date = None
        ref_grade = None
        ref_year = None
        comparison_type = 'NONE'

        if best:
            ref_id        = best['id']
            ref_title     = best.get('title', '')
            ref_price_jpy = best.get('price_jpy')
            ref_date      = best.get('sold_date')
            ref_grade     = best.get('grade') or ''
            ref_year      = best.get('year')
            comparison_type = _determine_comparison_type(best, year, grade, None)
            sell_price_jpy = ref_price_jpy

        # World auction: auction estimate があれば sell_price の下限として使う
        if src_group == 'WORLD' and price_jpy:
            if not sell_price_jpy:
                sell_price_jpy = price_jpy
            else:
                # auction estimate と Yahoo参照の高い方を採用（売価は楽観的に）
                sell_price_jpy = max(sell_price_jpy, price_jpy)

        cap_bid_limit_jpy = calc_cap_bid_limit_jpy(sell_price_jpy) if sell_price_jpy else None
        cap_bid_limit_usd = round(cap_bid_limit_jpy / FX_RATE, 0) if cap_bid_limit_jpy else None

        # eBay: 現在価格が判明している場合は bid_jpy = price_usd × fx として利益を計算
        if src_group == 'EBAY' and price_usd and sell_price_jpy:
            bid_jpy_actual = int(price_usd * FX_RATE)
            expected_profit = calc_expected_profit_jpy(sell_price_jpy, bid_jpy_actual)
            roi = calc_roi_pct(sell_price_jpy, bid_jpy_actual)
        elif cap_bid_limit_jpy and sell_price_jpy:
            # 仕入上限ギリギリで買った場合の利益
            expected_profit = calc_expected_profit_jpy(sell_price_jpy, cap_bid_limit_jpy)
            roi = calc_roi_pct(sell_price_jpy, cap_bid_limit_jpy)
        else:
            expected_profit = None
            roi = None

        # World: auction estimate での利益（auction estimate = 実際の入札額として計算）
        if src_group == 'WORLD' and price_jpy and sell_price_jpy:
            expected_profit = calc_expected_profit_jpy(sell_price_jpy, price_jpy)
            roi = calc_roi_pct(sell_price_jpy, price_jpy)

        judgment = _cap_judgment(expected_profit, bool(best))
        category = _category(judgment, bool(best))

        # WORLD アイテムは画像URL (evidence_status) も設定
        image_url = item.get('image_url')
        evidence_status = '要確認'
        if src_group == 'WORLD':
            if not image_url:
                evidence_status = 'スラブ未確認'
            else:
                evidence_status = '画像確認済'
        else:
            evidence_status = '要確認'

        # CAPコメント
        cap_comment = _build_cap_comment(
            title=title,
            comparison_type=comparison_type,
            yahoo_ref_title=ref_title,
            yahoo_ref_price_jpy=ref_price_jpy,
            yahoo_ref_date=str(ref_date) if ref_date else None,
            yahoo_ref_grade=ref_grade,
            ref_year=ref_year,
            item_year=year,
            item_grade=grade,
            cap_bid_limit_jpy=cap_bid_limit_jpy,
            cap_bid_limit_usd=cap_bid_limit_usd,
            expected_profit_jpy=expected_profit,
            expected_roi_pct=roi,
            cap_judgment=judgment,
            price_snapshot_jpy=price_jpy,
            source_group=src_group,
        )

        update = {
            'comparison_type':         comparison_type,
            'yahoo_ref_id':            ref_id,
            'yahoo_ref_title':         ref_title,
            'yahoo_ref_price_jpy':     ref_price_jpy,
            'yahoo_ref_date':          str(ref_date) if ref_date else None,
            'yahoo_ref_grade':         ref_grade,
            'cap_bid_limit_jpy':       cap_bid_limit_jpy,
            'cap_bid_limit_usd':       float(cap_bid_limit_usd) if cap_bid_limit_usd else None,
            'estimated_sell_price_jpy':sell_price_jpy,
            'total_cost_jpy':          int(cap_bid_limit_jpy * CUSTOMS_RATE + FIXED_COST_JPY) if cap_bid_limit_jpy else None,
            'expected_profit_jpy':     expected_profit,
            'expected_roi_pct':        roi,
            'cap_judgment':            judgment,
            'cap_comment':             cap_comment,
            'evidence_status':         evidence_status,
            'category':                category,
            'updated_at':              datetime.now(timezone.utc).isoformat(),
        }

        if verbose:
            if sell_price_jpy:
                print(f"       -> {comparison_type} | sell_jpy={sell_price_jpy:,}")
            else:
                print(f"       -> {comparison_type} | no_sell_price")
            if cap_bid_limit_jpy and expected_profit is not None:
                print(f"       -> limit_jpy={cap_bid_limit_jpy:,} profit={expected_profit:,} ROI={roi}%")
            else:
                print(f"       -> limit: N/A")
            print(f"       -> {judgment} category={category}")
            try:
                print(f"       comment: {cap_comment[:80]}")
            except UnicodeEncodeError:
                print(f"       comment: {cap_comment[:80].encode('ascii','replace').decode()}")
            print()

        if not dry_run:
            try:
                c.table('ceo_review_log').update(update).eq('id', item_id_val).execute()
            except Exception as e:
                logger.error(f"更新エラー id={item_id_val}: {e}")
                stats['errors'] += 1

        if category == 'CEO_REVIEW':
            stats['ceo_review'] += 1
        elif category == 'INVESTIGATION':
            stats['investigation'] += 1
        else:
            stats['observation'] += 1

    # --------------------------------------------------------
    # サマリー
    # --------------------------------------------------------
    if verbose:
        print(f"\n{'='*60}")
        print(f" CAP Enrichment DONE source={source} bucket={bucket}")
        print(f"  processed: {stats['processed']}")
        print(f"  CEO_REVIEW:   {stats['ceo_review']}")
        print(f"  INVESTIGATION:{stats['investigation']}")
        print(f"  OBSERVATION:  {stats['observation']}")
        print(f"  errors: {stats['errors']}")
        if dry_run:
            print(f"  [DRY-RUN: no DB writes]")
        print(f"{'='*60}\n")

    return stats


# ================================================================
# CLI
# ================================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='CAP Enrichment - CEO審査票生成')
    parser.add_argument('--source', default='EBAY',
                        help='EBAY / WORLD / all (default: EBAY)')
    parser.add_argument('--bucket', default='Top20',
                        help='Top20 / Top50 / Top100 / all (default: Top20)')
    parser.add_argument('--id',    default=None,
                        help='特定アイテムIDを処理 (--source/--bucket 無視)')
    parser.add_argument('--dry-run', action='store_true',
                        help='DBへの書き込みを行わない')
    parser.add_argument('--no-dry-run', dest='dry_run', action='store_false')
    parser.set_defaults(dry_run=True)

    args = parser.parse_args()
    run_cap_enrichment(
        source=args.source,
        bucket=args.bucket,
        dry_run=args.dry_run,
        item_id=args.id,
        verbose=True,
    )
