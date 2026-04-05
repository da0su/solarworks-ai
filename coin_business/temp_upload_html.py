import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from scripts.supabase_client import get_client
from pathlib import Path

c = get_client()
html_path = Path('web/mkt_review_11.html')
html_content = html_path.read_bytes()

bucket = 'coin-web'
remote_name = 'mkt_review_11.html'

try:
    # Try upsert (update if exists)
    result = c.storage.from_(bucket).upload(
        remote_name,
        html_content,
        file_options={
            'content-type': 'text/html; charset=utf-8',
            'upsert': 'true',
            'cache-control': 'no-cache'
        }
    )
    print(f'Upload OK: {result}')
except Exception as e:
    print(f'Upload error: {e}')
    # Try remove then upload
    try:
        c.storage.from_(bucket).remove([remote_name])
        print('Removed old file')
    except:
        pass
    try:
        result = c.storage.from_(bucket).upload(
            remote_name,
            html_content,
            file_options={'content-type': 'text/html; charset=utf-8'}
        )
        print(f'Re-upload OK: {result}')
    except Exception as e2:
        print(f'Re-upload error: {e2}')

supabase_url = 'https://sgitwndpyxzsslyyvpyn.supabase.co'
public_url = f'{supabase_url}/storage/v1/object/public/{bucket}/{remote_name}'
print(f'\nPublic URL:\n{public_url}')
print(f'File size: {len(html_content):,} bytes')
