"""Find your TELEGRAM_CHAT_ID.

1. Put TELEGRAM_BOT_TOKEN in .env.
2. Open your bot in Telegram and send it any message.
3. Run this script and copy the chat_id it prints into .env.
"""

import sys

import _bootstrap  # noqa: F401

from app.alerts.telegram import AlertError, TelegramNotifier
from app.core.config import get_settings

s = get_settings()
if not s.telegram_bot_token:
    print("Add TELEGRAM_BOT_TOKEN to .env first.")
    sys.exit(1)
try:
    chats = TelegramNotifier(s.telegram_bot_token.get_secret_value(), "").recent_chat_ids()
except AlertError as exc:
    print(f"Failed: {exc}")
    sys.exit(1)
if not chats:
    print("No messages found. Send your bot a message in Telegram, then run this again.")
    sys.exit(1)
for c in chats:
    print(f"chat_id={c['chat_id']}  type={c['type']}  name={c['name']}")
