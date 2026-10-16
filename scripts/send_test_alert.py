"""Send a test Telegram message. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."""

import sys

import _bootstrap  # noqa: F401

from app.alerts.telegram import AlertError, ConsoleNotifier, get_notifier
from app.core.config import get_settings

notifier = get_notifier(get_settings())
if isinstance(notifier, ConsoleNotifier):
    print("Telegram is not configured yet. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env")
    sys.exit(1)
try:
    notifier.send("TradeX test alert: Telegram is connected. (No trade signals yet.)")
except AlertError as exc:
    print(f"Failed: {exc}")
    sys.exit(1)
print("Sent. Check Telegram.")
