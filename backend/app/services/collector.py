"""The always-on collector: the process that will run on the small server.

During market hours (09:15-15:30 IST, Mon-Fri) it wakes up every few minutes and:
  1. stores a quote snapshot for each tracked instrument,
  2. tops up the latest 1-hour candles,
  3. once a day from 15:15 IST, stores the option chains (see services/option_chain.py).
After the close it loads the day's daily candle once (and India VIX's), checks the VIX-jump
volatility warning (VJ-1.0, see services/vix_warning.py) and sends one alert when a jump starts,
then sends a heartbeat message so you know it is alive. Outside market hours it sleeps.

This is where the rule engine plugs in later: after each cycle, run the
(backtested) rules on fresh data and send ENTER / EXIT alerts through the notifier.
Today no trade alerts are sent, because no rules have been tested yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import SESSION_CLOSE, is_market_open, is_trading_day, to_ist
from app.db.models import Instrument
from app.providers.base import MarketDataProvider
from app.services import option_chain, vix_warning
from app.services.ingestion import ingest_snapshot, refresh_recent

log = logging.getLogger(__name__)


@dataclass
class CollectorState:
    """Remembers what already happened today, so end-of-day work runs once."""

    eod_done_for: str | None = None
    chain_done_for: str | None = None
    open_heartbeat_for: str | None = None
    vix_alert_for: str | None = None  # the VIX close date a warning was already sent for
    failures: list[str] = field(default_factory=list)


def run_cycle(session: Session, provider: MarketDataProvider, notifier, state: CollectorState,
              now: datetime) -> str:
    """One wake-up. Returns what it did (useful for logs and tests)."""
    today = to_ist(now).date().isoformat()
    if is_market_open(now):
        if state.open_heartbeat_for != today:
            notifier.send(f"TradeX collector is running for {today} ({provider.name} data).")
            state.open_heartbeat_for = today
        instruments = session.scalars(
            select(Instrument).where(Instrument.provider == provider.name, Instrument.is_active)
        ).all()
        failed = [i.provider_symbol for i in instruments
                  if ingest_snapshot(session, provider, i).status != "SUCCESS"]
        failed += [f"candles run {r.run_id}" for r in refresh_recent(session, provider, "1h", now)
                   if r.status != "SUCCESS"]
        if failed:
            state.failures.extend(failed)
            notifier.send("TradeX data problem: " + ", ".join(failed[:5]) +
                          ". Signals would be NO TRADE until data recovers.")
        if (state.chain_done_for != today and option_chain.supports_chains(provider)
                and to_ist(now).time() >= option_chain.CHAIN_TIME_IST):
            res = option_chain.snapshot(session, provider,
                                        option_chain.underlyings(session, provider), now)
            bad = [f"{r.symbol} {r.expiry or ''}".strip() for r in res if r.status != "SUCCESS"]
            if bad:
                notifier.send("TradeX option chains not stored: " + ", ".join(bad[:5]))
            state.chain_done_for = today
            return "intraday+chains"
        return "intraday"

    after_close = to_ist(now).time() >= SESSION_CLOSE
    if is_trading_day(to_ist(now).date()) and after_close and state.eod_done_for != today:
        # 1h first: daily candles missing an open take it from the day's first 1h candle.
        results = refresh_recent(session, provider, "1h", now) + \
            refresh_recent(session, provider, "1d", now)
        bad = sum(r.status != "SUCCESS" for r in results)
        vix_load = vix_warning.refresh(session, provider, now)
        warning = vix_warning.current(session, provider.name, now)
        vix_note = ("" if vix_load.status == "SUCCESS" else " India VIX load failed.")
        notifier.send(f"TradeX end of day {today}: {len(results) - bad} loads ok, {bad} failed."
                      f"{vix_note} Volatility warning: {warning.status}.")
        if (warning.status == "WARNING" and warning.started_today
                and state.vix_alert_for != warning.as_of):
            notifier.send(vix_warning.alert_text(warning))
            state.vix_alert_for = warning.as_of
        state.eod_done_for = today
        return "end_of_day"
    return "idle"
