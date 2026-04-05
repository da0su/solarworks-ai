"""
run_cap_pipeline.py
===================
今日のCAP_DRAFTアイテム(scan_date=today)に対して:
1. cap_enrichment を1件ずつ実行
2. category='CEO_REVIEW' → marketing_status='MARKETING_REVIEW' に更新
3. その他 → marketing_status='OBSERVATION' or 'INVESTIGATION' に更新

使用方法:
  python scripts/run_cap_pipeline.py
  python scripts/run_cap_pipeline.py --scan-date 2026-04-04
  python scripts/run_cap_pipeline.py --dry-run
"""
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from scripts.cap_enrichment import run_cap_enrichment

logging.basicConfig(
    level=logging.WARNING,
    format='%(levelname)s %(message)s',
    stream=sys.stdout,
)

MARKETING_STATUS_MAP = {
    'CEO_REVIEW':    'MARKETING_REVIEW',
    'INVESTIGATION': 'INVESTIGATION',
    'OBSERVATION':   'OBSERVATION',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan-date', default=None, help='対象scan_date (default: today)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=None, help='処理上限件数')
    args = parser.parse_args()

    scan_date = args.scan_date or datetime.now().strftime('%Y-%m-%d')
    c = get_client()
    now = datetime.now(timezone.utc).isoformat()

    # 今日のCAP_DRAFTを取得
    q = (c.table('ceo_review_log')
          .select('id,title_snapshot,snapshot_score,source_group,review_bucket,cap_judgment,category')
          .eq('scan_date', scan_date)
          .eq('marketing_status', 'CAP_DRAFT')
          .order('snapshot_score', desc=True))
    rows = q.execute().data
    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    print(f'\n{"="*60}')
    print(f' CAP Pipeline: scan_date={scan_date}  対象={total}件')
    print(f' dry_run={args.dry_run}')
    print(f'{"="*60}\n')

    stats = {'total': total, 'ceo_review': 0, 'investigation': 0, 'observation': 0, 'errors': 0}

    for i, item in enumerate(rows, 1):
        item_id = item['id']
        title = (item.get('title_snapshot') or '')[:60]
        score = item.get('snapshot_score', 0)
        print(f'[{i:3d}/{total}] Sc={score:2} {title}')
        sys.stdout.flush()

        try:
            # cap_enrichment を1件実行 (no-dry-run)
            result = run_cap_enrichment(
                source='all',
                bucket='all',
                dry_run=args.dry_run,
                item_id=item_id,
                verbose=False,
            )
        except Exception as e:
            print(f'       ERROR: {e}')
            stats['errors'] += 1
            continue

        # cap_enrichmentがDBを更新した後、categoryを読み直してmarketing_statusを設定
        if not args.dry_run:
            try:
                updated = c.table('ceo_review_log').select('category,cap_judgment').eq('id', item_id).single().execute().data
                category = updated.get('category', 'OBSERVATION')
                cap_j = updated.get('cap_judgment', 'CAP_NG')
                ms = MARKETING_STATUS_MAP.get(category, 'OBSERVATION')
                c.table('ceo_review_log').update({
                    'marketing_status': ms,
                    'updated_at': now,
                }).eq('id', item_id).execute()
                print(f'       → {cap_j} | {category} | marketing_status={ms}')
                if ms == 'MARKETING_REVIEW':
                    stats['ceo_review'] += 1
                elif ms == 'INVESTIGATION':
                    stats['investigation'] += 1
                else:
                    stats['observation'] += 1
            except Exception as e:
                print(f'       marketing_status更新エラー: {e}')
                stats['errors'] += 1
        else:
            # dry-run時はresultから推定
            cap_j = result.get('processed', 0)
            print(f'       [DRY-RUN] processed={result.get("processed",0)} ceo_review={result.get("ceo_review",0)}')

    print(f'\n{"="*60}')
    print(f' Pipeline 完了')
    print(f'  total:          {stats["total"]}')
    print(f'  MARKETING_REVIEW:  {stats["ceo_review"]}')
    print(f'  INVESTIGATION:     {stats["investigation"]}')
    print(f'  OBSERVATION:       {stats["observation"]}')
    print(f'  errors:            {stats["errors"]}')
    print(f'{"="*60}\n')

    return stats


if __name__ == '__main__':
    main()
