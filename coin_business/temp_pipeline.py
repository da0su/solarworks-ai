
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import argparse, logging
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path("scripts").resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.supabase_client import get_client
from scripts.cap_enrichment import run_cap_enrichment

scan_date = '2026-04-04'
c = get_client()
now = datetime.now(timezone.utc).isoformat()

q = (c.table('ceo_review_log')
      .select('id,title_snapshot,snapshot_score,source_group,review_bucket,cap_judgment,category')
      .eq('scan_date', scan_date)
      .eq('marketing_status', 'CAP_DRAFT')
      .order('snapshot_score', desc=True))
rows = q.execute().data
total = len(rows)
print(f'CAP_DRAFT to process: {total}')

MARKETING_STATUS_MAP = {
    'CEO_REVIEW':    'MARKETING_REVIEW',
    'INVESTIGATION': 'INVESTIGATION',
    'OBSERVATION':   'OBSERVATION',
}

stats = {'total': total, 'ceo_review': 0, 'investigation': 0, 'observation': 0, 'errors': 0}

for i, item in enumerate(rows, 1):
    item_id = item['id']
    title = (item.get('title_snapshot') or '')[:50]
    score = item.get('snapshot_score', 0)
    try:
        print(f'[{i}/{total}] Sc={score} {title}', flush=True)
    except:
        print(f'[{i}/{total}] Sc={score} [title encoding error]', flush=True)
    
    try:
        result = run_cap_enrichment(
            source='all',
            bucket='all',
            dry_run=False,
            item_id=item_id,
            verbose=False,
        )
    except Exception as e:
        print(f'  ERROR: {e}', flush=True)
        stats['errors'] += 1
        continue
    
    try:
        updated = c.table('ceo_review_log').select('category,cap_judgment').eq('id', item_id).single().execute().data
        category = updated.get('category', 'OBSERVATION')
        cap_j = updated.get('cap_judgment', 'CAP_NG')
        ms = MARKETING_STATUS_MAP.get(category, 'OBSERVATION')
        c.table('ceo_review_log').update({'marketing_status': ms, 'updated_at': now}).eq('id', item_id).execute()
        print(f'  -> {cap_j} | {category} | {ms}', flush=True)
        if ms == 'MARKETING_REVIEW': stats['ceo_review'] += 1
        elif ms == 'INVESTIGATION': stats['investigation'] += 1
        else: stats['observation'] += 1
    except Exception as e:
        print(f'  update error: {e}', flush=True)
        stats['errors'] += 1

print(f'Done: MARKETING_REVIEW={stats["ceo_review"]} INVESTIGATION={stats["investigation"]} OBSERVATION={stats["observation"]} errors={stats["errors"]}')
