"""
#coin-cap-marke の最新メッセージを確認するスクリプト
セッション開始時に実行して未返信メッセージを検出する

Usage:
    cd coin_business
    python scripts/check_coin_channel.py
"""
import sys, os, urllib.request, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ルート .env も試す
if not os.environ.get('SLACK_BOT_TOKEN'):
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

token = os.environ.get('SLACK_BOT_TOKEN', '')
channel = 'C0AMLJU2GRW'  # #coin-cap-marke
BOT_USER_ID = 'U0AMM2M9Y48'  # solarworkscoo

if not token:
    print('ERROR: SLACK_BOT_TOKEN が見つかりません')
    sys.exit(1)

req = urllib.request.Request(
    f'https://slack.com/api/conversations.history?channel={channel}&limit=10',
    headers={'Authorization': f'Bearer {token}'}
)
with urllib.request.urlopen(req) as r:
    res = json.loads(r.read())

if not res.get('ok'):
    print(f'ERROR: {res.get("error")}')
    sys.exit(1)

messages = res.get('messages', [])
human_msgs = [m for m in messages if m.get('user') != BOT_USER_ID and m.get('user')]

print(f'=== #coin-cap-marke 最新メッセージ ({len(messages)}件中 人間={len(human_msgs)}件) ===')
print()
for m in messages:
    user = m.get('user', m.get('bot_id', 'bot'))
    is_bot = user == BOT_USER_ID
    prefix = '  [BOT]' if is_bot else '→ [MARKE]'
    text = m.get('text', '')[:120].replace('\n', ' ')
    ts = m.get('ts', '')
    print(f'{prefix} [{ts}] {text}')
    print()

if human_msgs:
    print(f'⚠️  未処理の人間メッセージが {len(human_msgs)} 件あります。受領報告を返してください。')
else:
    print('✅ 未返信の人間メッセージはありません。')
