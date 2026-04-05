"""
world_auction_scan.py  ─  世界オークション100本ノック
======================================================
Heritage / Spink / Noble / Noonans など世界オークション会場から
候補ロットを取得・スコアリングし、ceo_review_log に投入する。

【CHG-027 設計変更: Heritage優先1/3確保】
- Heritage を最優先会場として先に取得・スコアリング
- 世界オークション枠の 1/3 を Heritage 目標配分 (= ceil(top_n / 3))
- 不足時は理由を必ず数値で明文化して報告
- 会場ボーナス: Heritage +3 / Noble +1 / Spink +1
- 会場優先順位: Heritage > Noble > Spink > Stack's > GreatCol > Sixbid > Catawiki > MA-Shops > Other

使い方:
  python scripts/world_auction_scan.py               # 全会場 (active/imminent)
  python scripts/world_auction_scan.py --dry-run     # DB書き込みなし
  python scripts/world_auction_scan.py --sources heritage spink
  python scripts/world_auction_scan.py --top 100     # 上位100件を保存 (デフォルト)

会場別 auction_house マッピング:
  heritage      → HERITAGE
  spink         → SPINK
  noble         → NOBLE
  noonans       → NOONANS
  stacks_bowers → STACKS_BOWERS
  greatcollections → GREATCOLLECTIONS
  sixbid        → SIXBID
  catawiki      → CATAWIKI
  ma_shops      → MA_SHOPS
  (その他)      → OTHER
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from scripts.daily_scan import (
    parse_ebay_title,
    score_profit_axis, score_db_axis, score_bids_axis, score_yahoo_ref_axis,
    db_label, profit_label,
    YAHOO_FEE,
)

logger = logging.getLogger(__name__)

# ── 定数 ──────────────────────────────────────────────────────────
TOP_N_SAVE  = 100   # 上位N件を ceo_review_log に保存

# source_key / auction_house 文字列 → ceo_review_log auction_house 値
HOUSE_MAP: dict[str, str] = {
    'heritage':         'HERITAGE',
    'spink':            'SPINK',
    'noble':            'NOBLE',
    'noonans':          'NOONANS',
    'stacks_bowers':    'STACKS_BOWERS',
    'greatcollections': 'GREATCOLLECTIONS',
    'sixbid':           'SIXBID',
    'catawiki':         'CATAWIKI',
    'ma_shops':         'MA_SHOPS',
    'sincona':          'OTHER',
}

# 【CHG-027】会場優先順位 (Heritage最優先)
VENUE_PRIORITY: list[str] = [
    'heritage', 'noble', 'spink', 'noonans',
    'stacks_bowers', 'greatcollections', 'sixbid',
    'catawiki', 'ma_shops', 'other',
]

# 【CHG-027】会場ボーナス (Heritage +3, Noble/Spink +1)
VENUE_BONUS: dict[str, int] = {
    'heritage': 3,
    'noble':    1,
    'spink':    1,
}

# スコープ: 素材 × グレード閾値
SCOPE_GOLD_OK_DENOMS_EXCLUDE = {'$5 Eagle', '$10 Eagle'}
SCOPE_SILVER_MIN_GRADE: dict[str, int] = {
    'Morgan Dollar':       62,
    'Peace Dollar':        63,
    'Walking Liberty':     63,
    'Mercury Dime':        63,
    'Franklin Half':       63,
    'Kennedy Half':        64,
    'Barber Coinage':      62,
    'Half Dollar Classic': 62,
    'Classic US Silver':   62,
    '$50 Eagle':           65,
    'UNKNOWN':             65,
}
SCOPE_SILVER_DEFAULT_MIN = 60

# 非コインキーワード除外リスト
EXCLUDE_TITLE_KW = [
    'nugget', 'quartz', 'troy ounce', 'troy oz', '.999 fine',
    'group of ', 'lot of ', 'collection of ', 'assorted ',
    'bullion', 'gold bar', 'silver bar', 'ingot',
    'printed specification', 'specification card',
    'hoard', 'roman', 'ancient', 'medieval',
    'token', 'medal', 'urn', 'badge', 'plaque',
]


# ── スコープ判定 ─────────────────────────────────────────────────
def _grade_num(grade: str) -> int:
    try:
        return int(grade[2:]) if grade and len(grade) >= 4 else 0
    except ValueError:
        return 0


def is_in_scope(ep: dict) -> bool:
    """当社スコープ内かどうか判定。"""
    material = ep.get('material', 'unknown')
    denom    = ep.get('denomination', 'UNKNOWN')
    grade    = ep.get('grade_num', '')
    gn       = _grade_num(grade)
    title    = (ep.get('_title', '') or '').lower()
    cert     = ep.get('cert_company', '')
    year     = ep.get('year')

    if any(kw in title for kw in EXCLUDE_TITLE_KW):
        return False
    if not year and not cert:
        return False

    if material == 'gold':
        return denom not in SCOPE_GOLD_OK_DENOMS_EXCLUDE
    elif material == 'silver':
        if denom == '$50 Eagle':
            return gn >= 65
        min_g = SCOPE_SILVER_MIN_GRADE.get(denom, SCOPE_SILVER_DEFAULT_MIN)
        return gn >= min_g
    elif material == 'platinum':
        return True

    return False


# ── DB照合 ──────────────────────────────────────────────────────
def _db_match(ep: dict, sb) -> tuple[Optional[dict], str]:
    """coin_slab_data から ep に対応するレコードを検索。"""
    country  = ep.get('country', 'UNKNOWN')
    year     = ep.get('year')
    denom    = ep.get('denomination', 'UNKNOWN')
    grade    = ep.get('grade_num', '')

    if country == 'UNKNOWN' or not year or not grade:
        return None, None

    try:
        q = (sb.table('coin_slab_data')
               .select('staging_id,sold_price_jpy,grade,year,country,denomination')
               .eq('country', country)
               .eq('year', year)
               .eq('denomination', denom)
               .eq('grade', grade))
        rows = q.limit(1).execute().data
        if rows:
            return rows[0], 'exact'

        gn = _grade_num(grade)
        near_rows = (sb.table('coin_slab_data')
                       .select('staging_id,sold_price_jpy,grade,year,country,denomination')
                       .eq('country', country)
                       .eq('denomination', denom)
                       .gte('year', year - 1)
                       .lte('year', year + 1)
                       .limit(5)
                       .execute().data)
        for row in near_rows:
            rg = _grade_num(row.get('grade', ''))
            if abs(rg - gn) <= 1:
                return row, 'near'

    except Exception as e:
        logger.debug(f'DB照合エラー: {e}')

    return None, None


# ── 利益計算 ─────────────────────────────────────────────────────
def calc_profit(estimated_cost_jpy: Optional[float], yahoo_ref_jpy: Optional[int]) -> Optional[int]:
    if not estimated_cost_jpy or not yahoo_ref_jpy:
        return None
    sell   = yahoo_ref_jpy * (1 - YAHOO_FEE)
    profit = sell - estimated_cost_jpy
    return int(profit)


# ── 1ロットのスコア計算 ─────────────────────────────────────────
def _score_lot(ep: dict, lot: dict, db_row: Optional[dict], match_type: Optional[str]) -> int:
    """
    1ロットのスコアを計算。
    base_score (profit + db + bids + yahoo_ref)
    + bonus (grade + cert + year + estimated_cost)
    + venue_bonus (Heritage +3, Noble/Spink +1)  【CHG-027】
    """
    yahoo_ref  = db_row['sold_price_jpy'] if db_row else None
    est_cost   = lot.get('estimated_cost_jpy')
    profit_jpy = calc_profit(est_cost, yahoo_ref)

    s_profit = score_profit_axis(profit_jpy)
    s_db     = score_db_axis(match_type)
    s_bids   = 0   # 世界オークション: 入札データなし
    s_yahoo  = score_yahoo_ref_axis(yahoo_ref)
    base_score = s_profit + s_db + s_bids + s_yahoo

    # 品質ボーナス (grade / cert / year / cost)
    bonus = 0
    gn = _grade_num(ep.get('grade_num', ''))
    if gn >= 65:   bonus += 8
    elif gn >= 63: bonus += 5
    elif gn >= 60: bonus += 2
    if ep.get('cert_company'):  bonus += 5
    if ep.get('year'):          bonus += 2
    est = lot.get('estimated_cost_jpy') or 0
    if 0 < est <= 300000:   bonus += 5
    elif est <= 500000:     bonus += 3
    elif est <= 1000000:    bonus += 1

    # 【CHG-027】会場ボーナス
    src_key      = lot.get('source', 'other').lower()
    venue_bonus  = VENUE_BONUS.get(src_key, 0)

    return base_score + bonus + venue_bonus


# ── ロット群をスコアリング ────────────────────────────────────────
def _score_lots(scoped: list[dict], sb) -> list[tuple]:
    """
    スコープ通過済み ep のリストを DB照合 + スコアリングして
    (score, ep, lot, db_row, match_type, profit_jpy) のタプルリストを返す。
    """
    scored = []
    for ep in scoped:
        lot = ep['_lot']
        db_row, match_type = _db_match(ep, sb)
        yahoo_ref  = db_row['sold_price_jpy'] if db_row else None
        est_cost   = lot.get('estimated_cost_jpy')
        profit_jpy = calc_profit(est_cost, yahoo_ref)
        score = _score_lot(ep, lot, db_row, match_type)
        scored.append((score, ep, lot, db_row, match_type, profit_jpy))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ── Heritage不足理由分析 ──────────────────────────────────────────
def _analyze_heritage_shortage(
    heritage_lots_total: int,
    heritage_scoped: int,
    heritage_scored: int,
    heritage_saved: int,
    heritage_target: int,
) -> dict:
    """Heritage不足時の理由を集計して返す。"""
    shortage = heritage_target - heritage_saved
    reasons = []

    excluded = heritage_lots_total - heritage_scoped
    if excluded > 0:
        # EXCLUDE_TITLE_KW分析は呼び出し元で行う
        reasons.append(f'非コイン・KW除外: {excluded}件')

    if heritage_scoped == 0 and heritage_lots_total > 0:
        reasons.append('全件スコープフィルター除外')
    elif heritage_scoped > 0 and heritage_scored == 0:
        reasons.append('スコアリング処理失敗')
    elif heritage_scored < shortage:
        if heritage_scored > 0:
            reasons.append(f'スコープ通過後スコアリング: {heritage_scored}件のみ')

    if heritage_lots_total == 0:
        reasons.append('Heritage取得0件 (API/スクレイピング失敗 or 取得対象なし)')

    return {
        'target':    heritage_target,
        'saved':     heritage_saved,
        'shortage':  shortage,
        'fetched':   heritage_lots_total,
        'scoped':    heritage_scoped,
        'scored':    heritage_scored,
        'reasons':   reasons,
    }


# ── メイン: ロット取得 → スコアリング → 保存 ───────────────────
def run_world_scan(
    sources: Optional[list[str]] = None,
    top_n: int = TOP_N_SAVE,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    世界オークション100本ノック実行。
    【CHG-027】Heritage優先1/3確保フロー。

    Returns:
        summary dict (投入件数、会場別内訳、Heritage達成率等)
    """
    scan_date = str(date.today())
    now_iso   = datetime.now(timezone.utc).isoformat()
    sb        = get_client()

    # Heritage目標件数 = ceil(top_n / 3)
    heritage_target = math.ceil(top_n / 3)
    if verbose:
        logger.info(f'[設計] top_n={top_n} / Heritage目標={heritage_target}件 ({heritage_target/top_n*100:.0f}%)')

    # ── STEP1-A: Heritage 優先取得 ──────────────────────────────
    heritage_lots_total = 0
    heritage_scoped_items: list[dict] = []
    heritage_scored_items: list[tuple] = []

    if sources is None or 'heritage' in sources:
        try:
            from scripts.heritage_fetcher import fetch_heritage_lots
            h_lots = fetch_heritage_lots(dry_run=True)
            heritage_lots_total = len(h_lots)
            if verbose:
                logger.info(f'[STEP1-A][Heritage] 取得: {heritage_lots_total}件')

            # スコープフィルター
            kw_hits: dict[str, int] = {}
            for lot in h_lots:
                title = lot.get('lot_title', '') or ''
                if not title.strip():
                    continue
                ep = parse_ebay_title(title)
                ep['_lot'] = lot
                ep['_title'] = title
                if is_in_scope(ep):
                    heritage_scoped_items.append(ep)
                else:
                    # 除外理由トラッキング
                    t = title.lower()
                    for kw in EXCLUDE_TITLE_KW:
                        if kw in t:
                            kw_hits[kw] = kw_hits.get(kw, 0) + 1
                            break

            if verbose:
                logger.info(f'[STEP1-A][Heritage] スコープ通過: {len(heritage_scoped_items)}件 / {heritage_lots_total}件')
                if kw_hits and len(heritage_scoped_items) == 0:
                    logger.info(f'  除外KW: {dict(sorted(kw_hits.items(), key=lambda x:-x[1]))}')

            # DB照合 + スコアリング
            heritage_scored_items = _score_lots(heritage_scoped_items, sb)
            if verbose:
                logger.info(f'[STEP1-A][Heritage] スコアリング: {len(heritage_scored_items)}件')

        except Exception as e:
            logger.warning(f'[Heritage] 取得失敗: {e}')

    # ── STEP1-B: 補完会場 (Noble / Spink / Noonans) 取得 ────────
    other_lots_total = 0
    other_scoped_items: list[dict] = []
    other_scored_items: list[tuple] = []

    numisbids_sources: list[str] = []
    if sources is None:
        numisbids_sources = ['noble', 'spink', 'noonans']
    else:
        for s in ['noble', 'spink', 'noonans']:
            if s in sources:
                numisbids_sources.append(s)

    if numisbids_sources:
        try:
            from scripts.numisbids_fetcher import fetch_numisbids_lots
            o_lots = fetch_numisbids_lots(sources=numisbids_sources, dry_run=True)
            other_lots_total = len(o_lots)
            if verbose:
                by_src = Counter(l['source'] for l in o_lots)
                for s, n in by_src.items():
                    logger.info(f'[STEP1-B][{s}] 取得: {n}件')

            for lot in o_lots:
                title = lot.get('lot_title', '') or ''
                if not title.strip():
                    continue
                ep = parse_ebay_title(title)
                ep['_lot'] = lot
                ep['_title'] = title
                if is_in_scope(ep):
                    other_scoped_items.append(ep)

            if verbose:
                logger.info(f'[STEP1-B][補完会場] スコープ通過: {len(other_scoped_items)}件 / {other_lots_total}件')

            other_scored_items = _score_lots(other_scoped_items, sb)
            if verbose:
                logger.info(f'[STEP1-B][補完会場] スコアリング: {len(other_scored_items)}件')

        except Exception as e:
            logger.warning(f'[Numisbids] 取得失敗: {e}')

    # GreatCollections / Sixbid / Catawiki / MA-Shops → 将来実装
    for stub_src in ['greatcollections', 'sixbid', 'catawiki', 'ma_shops']:
        if sources is None or stub_src in sources:
            pass  # TODO: 各会場フェッチャー実装

    # ── STEP2: Heritage優先配分 + 補完 ─────────────────────────
    # Heritage: 上位 heritage_target 件まで（品質を落とさない）
    heritage_to_save  = heritage_scored_items[:heritage_target]
    heritage_saved_n  = len(heritage_to_save)

    # 残り枠を補完会場で埋める
    remaining_slots   = top_n - heritage_saved_n
    others_to_save    = other_scored_items[:remaining_slots]

    # 最終リスト: Heritage優先 → 補完会場 (スコア順で再ソート)
    final_items = heritage_to_save + others_to_save
    final_items.sort(key=lambda x: x[0], reverse=True)

    if verbose:
        logger.info(f'[STEP2] Heritage: {heritage_saved_n}件 (目標{heritage_target}件) / '
                    f'補完: {len(others_to_save)}件 / 合計: {len(final_items)}件')

    # ── STEP3: Heritage不足レポート ─────────────────────────────
    heritage_shortage = _analyze_heritage_shortage(
        heritage_lots_total=heritage_lots_total,
        heritage_scoped=len(heritage_scoped_items),
        heritage_scored=len(heritage_scored_items),
        heritage_saved=heritage_saved_n,
        heritage_target=heritage_target,
    )
    if verbose and heritage_shortage['shortage'] > 0:
        _log_heritage_shortage(heritage_shortage, top_n)
    elif verbose:
        logger.info(f'[Heritage] 目標達成: {heritage_saved_n}件 / 目標{heritage_target}件')

    # ── STEP4: ceo_review_log に upsert ─────────────────────────
    if not final_items or dry_run:
        if verbose and dry_run:
            logger.info(f'[dry-run] 保存スキップ (top={len(final_items)}件)')
        return _build_summary(
            all_lots_total=heritage_lots_total + other_lots_total,
            scoped_total=len(heritage_scoped_items) + len(other_scoped_items),
            scored_total=len(heritage_scored_items) + len(other_scored_items),
            saved=0,
            final_items=final_items,
            heritage_shortage=heritage_shortage,
            heritage_target=heritage_target,
            top_n=top_n,
        )

    records = _build_records(final_items, scan_date, now_iso)

    saved = 0
    try:
        r = sb.table('ceo_review_log').upsert(
            records,
            on_conflict='marketplace,item_id,scan_date'
        ).execute()
        saved = len(r.data) if r.data else len(records)
        if verbose:
            logger.info(f'[STEP4] ceo_review_log: {saved}件 upsert完了')
    except Exception as e:
        logger.error(f'[STEP4] upsert失敗: {e}')

    return _build_summary(
        all_lots_total=heritage_lots_total + other_lots_total,
        scoped_total=len(heritage_scoped_items) + len(other_scoped_items),
        scored_total=len(heritage_scored_items) + len(other_scored_items),
        saved=saved,
        final_items=final_items,
        heritage_shortage=heritage_shortage,
        heritage_target=heritage_target,
        top_n=top_n,
    )


def _build_records(final_items: list[tuple], scan_date: str, now_iso: str) -> list[dict]:
    """upsert 用レコードリストを構築。"""
    records = []
    for i, (score, ep, lot, db_row, match_type, profit_jpy) in enumerate(final_items):
        if i < 20:
            bucket = 'Top20'
        elif i < 50:
            bucket = 'Top50'
        else:
            bucket = 'Top100'

        src_key   = lot.get('source', 'other').lower()
        house_key = HOUSE_MAP.get(src_key, 'OTHER')
        price_jpy = lot.get('price_jpy') or 0
        price_usd = None
        if lot.get('currency') == 'USD':
            price_usd = lot.get('current_price')

        records.append({
            'marketplace':         lot.get('auction_house', 'WorldAuction'),
            'source_group':        'WORLD',
            'auction_house':       house_key,
            'item_id':             f"{lot.get('auction_id', 'unknown')}_{lot.get('lot_number', '0')}",
            'url':                 lot.get('lot_url', ''),
            'title_snapshot':      (lot.get('lot_title', '') or '')[:200],
            'cert_company':        ep.get('cert_company', ''),
            'cert_number':         None,
            'grade':               ep.get('grade_num', ''),
            'country':             ep.get('country', ''),
            'year':                ep.get('year'),
            'denomination':        ep.get('denomination', ''),
            'material':            ep.get('material', ''),
            'bid_count_snapshot':  0,
            'price_snapshot_usd':  price_usd,
            'price_snapshot_jpy':  price_jpy if price_jpy > 0 else None,
            'yahoo_ref_price':     db_row['sold_price_jpy'] if db_row else None,
            'profit_estimate':     profit_jpy,
            'db_similarity':       db_label(match_type),
            'db_ref_id':           db_row.get('staging_id', '') if db_row else None,
            'snapshot_score':      score,
            'scan_date':           scan_date,
            'review_bucket':       bucket,
            'first_seen_at':       now_iso,
            'submitted_to_ceo_at': now_iso,
            'submit_count':        1,
            'duplicate_status':    'NEW',
            'resubmit_reason':     None,
        })
    return records


def _log_heritage_shortage(shortage: dict, top_n: int) -> None:
    """Heritage不足をロガーに出力。"""
    pct = shortage['saved'] / shortage['target'] * 100 if shortage['target'] > 0 else 0
    logger.warning(
        f'[Heritage不足] 取得:{shortage["fetched"]}件 / '
        f'スコープ通過:{shortage["scoped"]}件 / '
        f'スコアリング:{shortage["scored"]}件 / '
        f'投入:{shortage["saved"]}件 / '
        f'目標:{shortage["target"]}件 / '
        f'不足:{shortage["shortage"]}件 ({pct:.0f}%達成)'
    )
    for reason in shortage['reasons']:
        logger.warning(f'  主因: {reason}')


def _build_summary(
    all_lots_total: int,
    scoped_total: int,
    scored_total: int,
    saved: int,
    final_items: list[tuple],
    heritage_shortage: dict,
    heritage_target: int,
    top_n: int,
) -> dict:
    breakdown = _breakdown(final_items)
    by_house  = _house_breakdown(final_items)
    heritage_ratio = (heritage_shortage['saved'] / saved * 100) if saved > 0 else 0.0

    return {
        'total':            all_lots_total,
        'scoped':           scoped_total,
        'scored':           scored_total,
        'saved':            saved,
        'breakdown':        breakdown,
        'by_house':         by_house,
        'heritage_target':  heritage_target,
        'heritage_saved':   heritage_shortage['saved'],
        'heritage_ratio':   heritage_ratio,
        'heritage_shortage': heritage_shortage,
        'top_n':            top_n,
    }


def _breakdown(items: list[tuple]) -> dict:
    d = {'Top20': 0, 'Top50': 0, 'Top100': 0}
    for i, _ in enumerate(items):
        if i < 20:   d['Top20']  += 1
        elif i < 50: d['Top50']  += 1
        else:        d['Top100'] += 1
    return d


def _house_breakdown(items: list[tuple]) -> dict:
    c: dict[str, int] = {}
    for _, ep, lot, *_ in items:
        src = lot.get('source', 'other').lower()
        h   = HOUSE_MAP.get(src, 'OTHER')
        c[h] = c.get(h, 0) + 1
    return c


def _print_world_report(result: dict) -> None:
    """日次報告フォーマットで世界オークション結果を表示。"""
    print()
    print('=' * 60)
    print('  世界オークション スキャン結果')
    print('=' * 60)

    # 全体統計
    print(f'  取得総件数:     {result.get("total", 0):,}件')
    print(f'  スコープ通過:   {result.get("scoped", 0):,}件')
    print(f'  スコアリング:   {result.get("scored", 0):,}件')
    print(f'  CEO確認投入:    {result.get("saved", 0):,}件')

    bd = result.get('breakdown', {})
    print(f'    └ Top20:{bd.get("Top20",0)} / Top50:{bd.get("Top50",0)} / Top100:{bd.get("Top100",0)}')

    # Heritage配分レポート
    print()
    print('  ── Heritage配分 ──')
    hs    = result.get('heritage_shortage', {})
    saved = result.get('saved', 0)
    h_saved  = result.get('heritage_saved', 0)
    h_target = result.get('heritage_target', 0)
    h_ratio  = result.get('heritage_ratio', 0.0)
    shortage = result.get('top_n', 100) // 3 - h_saved
    print(f'  世界総投入件数:   {saved}件')
    print(f'  Heritage投入件数: {h_saved}件')
    print(f'  Heritage比率:     {h_ratio:.0f}% (目標33%)')
    print(f'  Heritage目標件数: {h_target}件')
    deficit = h_target - h_saved
    if deficit > 0:
        print(f'  Heritage不足件数: {deficit}件')
        print(f'    取得: {hs.get("fetched",0)}件 / スコープ通過: {hs.get("scoped",0)}件 / スコアリング: {hs.get("scored",0)}件')
        for r in hs.get('reasons', []):
            print(f'    主因: {r}')
    else:
        print(f'  Heritage: ✅ 目標達成')

    # 会場別内訳
    bh = result.get('by_house', {})
    if bh:
        print()
        print('  ── 会場別投入件数 ──')
        for h in VENUE_PRIORITY:
            hkey = HOUSE_MAP.get(h, 'OTHER')
            n = bh.get(hkey, 0)
            if n > 0:
                mark = ' ★優先' if h == 'heritage' else ''
                print(f'  {hkey:<20}: {n}件{mark}')
    print('=' * 60)


# ── CLI エントリーポイント ───────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description='世界オークション100本ノック (Heritage優先1/3設計)')
    parser.add_argument('--dry-run',  action='store_true', help='DB書き込みなし')
    parser.add_argument('--sources',  nargs='+', help='対象会場 (heritage spink noble noonans ...)')
    parser.add_argument('--top',      type=int, default=TOP_N_SAVE, help=f'上位N件保存 (デフォルト={TOP_N_SAVE})')
    parser.add_argument('--verbose',  action='store_true', default=True)
    args = parser.parse_args()

    result = run_world_scan(
        sources  = args.sources,
        top_n    = args.top,
        dry_run  = args.dry_run,
        verbose  = args.verbose,
    )

    _print_world_report(result)
