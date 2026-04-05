import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from supabase_client import get_client
sb = get_client()

r = sb.table('daily_candidates').select(
    'management_no,created_at,source,auction_house,lot_url,judgment'
).order('created_at').execute()
print(f"daily_candidates 件数: {len(r.data)}")
for row in r.data:
    mgmt = row.get('management_no', '?')
    created = str(row.get('created_at', ''))[:10]
    src = row.get('source', '?')
    auction = row.get('auction_house', '?')
    url = str(row.get('lot_url', ''))[:45]
    judg = row.get('judgment', '?')
    print(f"  [{mgmt}] {created} | src={src} | auction={auction} | {judg} | {url}")
