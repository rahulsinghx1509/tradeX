import logging

import httpx

from app.alerts.telegram import TelegramNotifier
from app.core.log_setup import setup_logging

TOKEN = "123456:SECRET-TOKEN-abc"


def test_telegram_token_never_reaches_the_logs(caplog):
    setup_logging()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True, "result": {}})))
    with caplog.at_level(logging.INFO):  # root at INFO, as on the server
        TelegramNotifier(TOKEN, "42", client).send("hello")
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
