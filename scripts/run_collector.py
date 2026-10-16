"""The always-on collector (what will run on the small server during market hours).

Usage:
    python scripts/run_collector.py          # runs until you press Ctrl+C
    python scripts/run_collector.py --once   # one cycle, then exit (good for testing)
"""

import argparse
import logging
import time

import _bootstrap  # noqa: F401

from app.alerts.telegram import AlertError, get_notifier
from app.core.config import get_settings
from app.core.market_time import utc_now
from app.db.session import get_sessionmaker
from app.providers.factory import get_provider
from app.services.collector import CollectorState, run_cycle
from app.services.ingestion import sync_instruments

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collector")


class SafeNotifier:
    """A failed alert must never stop data collection; it is logged instead."""

    def __init__(self, inner):
        self.inner = inner

    def send(self, text: str) -> None:
        try:
            self.inner.send(text)
        except AlertError as exc:
            log.error("Alert not sent: %s", exc)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    s = get_settings()
    provider, notifier = get_provider(s), SafeNotifier(get_notifier(s))
    state = CollectorState()
    Session = get_sessionmaker()
    with Session() as session:
        sync_instruments(session, provider, s.underlying_list, s.futures_expiries_to_track)
    log.info("Collector started (provider=%s, every %s min)", provider.name,
             s.collector_interval_minutes)
    while True:
        try:
            with Session() as session:
                did = run_cycle(session, provider, notifier, state, utc_now())
            log.info("cycle: %s", did)
        except Exception:
            log.exception("cycle crashed; will retry next interval")
        if args.once:
            break
        time.sleep(s.collector_interval_minutes * 60)


if __name__ == "__main__":
    main()
