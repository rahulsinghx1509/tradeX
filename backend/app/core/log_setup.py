"""One logging setup for the API and the collector.

httpx logs every request URL at INFO level. Telegram puts the bot token inside the URL
(api.telegram.org/bot<token>/sendMessage), so at INFO the token ended up in the server logs.
Request logs from httpx and httpcore are therefore kept to warnings and errors only.
"""

import logging

QUIET_LOGGERS = ("httpx", "httpcore")


def setup_logging(fmt: str = "%(asctime)s %(levelname)s %(message)s") -> None:
    logging.basicConfig(level=logging.INFO, format=fmt)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
