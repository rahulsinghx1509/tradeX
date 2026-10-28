"""Telegram alerts.

How to set up (once):
1. In Telegram, message @BotFather, send /newbot, and follow the steps.
   BotFather gives you a bot token. Put it in .env as TELEGRAM_BOT_TOKEN.
   Never paste it into a chat or commit it.
2. Open your new bot in Telegram and send it any message (e.g. "hi").
3. Run:  python scripts/telegram_chat_id.py   and copy the number into .env
   as TELEGRAM_CHAT_ID.
4. Run:  python scripts/send_test_alert.py   - you should get a message.

More than one person: Telegram bots can't message a phone number, only a chat that has
messaged the bot. Each person opens the bot and sends it "hi"; step 3 then prints every chat
id. Put them all in TELEGRAM_CHAT_ID separated by commas, e.g. TELEGRAM_CHAT_ID=111,222.

Today this sends test and heartbeat messages. Entry/exit alerts are added once
the rule engine exists and has been backtested.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
MAX_LEN = 4000  # Telegram's limit is 4096 characters


class AlertError(RuntimeError):
    pass


def chat_ids(value: str | None) -> list[str]:
    """TELEGRAM_CHAT_ID may hold several ids separated by commas."""
    return [c.strip() for c in (value or "").split(",") if c.strip()]


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        self._token = token
        self._chat_ids = chat_ids(chat_id)
        self._client = client or httpx.Client(timeout=10)

    def _url(self, method: str) -> str:
        return f"{API}/bot{self._token}/{method}"

    def send(self, text: str) -> None:
        """Send to every chat. One chat failing never stops the others; the error lists them all."""
        errors = []
        for n, chat in enumerate(self._chat_ids, start=1):
            try:
                self._send_one(chat, text)
            except AlertError as exc:
                errors.append(f"chat {n} of {len(self._chat_ids)}: {exc}"
                              if len(self._chat_ids) > 1 else str(exc))
        if errors:
            raise AlertError("; ".join(errors))

    def _send_one(self, chat_id: str, text: str) -> None:
        try:
            resp = self._client.post(self._url("sendMessage"),
                                     json={"chat_id": chat_id, "text": text[:MAX_LEN],
                                           "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            raise AlertError(f"Could not reach Telegram ({type(exc).__name__})") from None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or not body.get("ok"):
            # Telegram's description is safe to show; the token is never included.
            desc = body.get("description", "")
            raise AlertError(f"Telegram rejected the message: HTTP {resp.status_code} {desc}")

    def recent_chat_ids(self) -> list[dict]:
        """Chats that have messaged the bot recently (used to find TELEGRAM_CHAT_ID)."""
        resp = self._client.get(self._url("getUpdates"))
        if resp.status_code != 200:
            raise AlertError(f"getUpdates failed: HTTP {resp.status_code}")
        chats = {}
        for upd in resp.json().get("result", []):
            chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
            if chat:
                chats[chat["id"]] = {"chat_id": chat["id"], "type": chat.get("type"),
                                     "name": chat.get("title") or chat.get("first_name")}
        return list(chats.values())


class ConsoleNotifier:
    """Used when Telegram is not configured: prints alerts instead of sending them."""

    def send(self, text: str) -> None:
        print(f"[ALERT - Telegram not configured]\n{text}")


def get_notifier(settings: Settings) -> TelegramNotifier | ConsoleNotifier:
    if settings.telegram_bot_token and chat_ids(settings.telegram_chat_id):
        return TelegramNotifier(settings.telegram_bot_token.get_secret_value(),
                                settings.telegram_chat_id)
    return ConsoleNotifier()
