"""ROOM BOT - Slack連携（非推奨・統合済み）

このファイルは slack_room_bot.py に統合されました。
正式エントリポイント: python slack_room_bot.py

v6.2 (2026-03-16):
  - slack_room_bot.py に排他制御（3層防御）を実装
  - このファイルは後方互換のため slack_room_bot.py に委譲するだけ
"""

import sys
from pathlib import Path

print("=" * 60)
print("INFO: このファイルは非推奨です。")
print("正式エントリポイント: python slack_room_bot.py")
print("自動的に slack_room_bot.py を起動します...")
print("=" * 60)

# slack_room_bot.py に委譲
sys.path.insert(0, str(Path(__file__).parent))
from slack_room_bot import start_bot

start_bot()
