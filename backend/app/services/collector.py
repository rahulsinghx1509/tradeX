"""The always-on collector: the process that will run on the small server.

During market hours (09:15-15:30 IST, Mon-Fri) it wakes up every few minutes and:
  1. stores a quote snapshot for each tracked instrument,
  2. tops up the latest 1-hour candles,
  3. once a day from 15:15 IST, stores the option chains (see services/option_chain.py).
After the close it loads the day's daily candle once (and India VIX's), checks the VIX-jump
volatility warning (VJ-1.0, see services/vix_warning.py) and sends one alert when a jump starts,
then sends a heartbeat message so you know it is alive. From 19:00 IST it loads NSE's free daily
F&O file (services/nse_bhavcopy.py), catching up any missed days. Otherwise it sleeps.

This is where the rule engine plugs in later: after each cycle, run the
(backtested) rules on fresh data and send ENTER / EXIT alerts through the notifier.
Today no trade alerts are sent, because no rules have been tested yet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.market_time import SESSION_CLOSE, is_market_open, is_trading_day, to_ist
from app.db.models import Instrument
from app.providers.base import MarketDataProvider
from app.services import nse_bhavcopy, option_chain, vix_warning
from app.services.ingestion import ingest_snapshot, refresh_recent

log = logging.getLogger(__name__)

FO_FILE_FROM_IST = time(19, 0)  # NSE publishes the day's F&O file in the evening
FO_FILE_GIVE_UP_IST = time(23, 0)
FO_CATCH_UP_DAYS = 10


@dataclass
class CollectorState:
    """Remembers what already happened today, so end-of-day work runs once."""

    eod_done_for: str | None = None
    chain_done_for: str | None = None
    open_heartbeat_for: str | None = None
    vix_alert_for: str | None = None  # the VIX close date a warning was already sent for
    fo_done_for: str | None = None  # the day NSE's F&O file was loaded (or given up on)
    http_client: Callable[[], httpx.Client] = httpx.Client
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
    if (provider.name != "sample" and is_trading_day(to_ist(now).date())
            and to_ist(now).time() >= FO_FILE_FROM_IST and state.fo_done_for != today):
        return load_fo_file(session, notifier, state, now)
    return "idle"


def load_fo_file(session: Session, notifier, state: CollectorState, now: datetime) -> str:
    """Load today's NSE F&O file plus any missed days; retry each cycle until it appears."""
    today = to_ist(now).date()
    latest = nse_bhavcopy.latest_day(session)
    start = max(latest + timedelta(days=1), today - timedelta(days=FO_CATCH_UP_DAYS)) if latest else today
    with state.http_client() as client:
        results = nse_bhavcopy.load(session, client, get_settings().underlying_list, start, today)
    mine = next((r for r in results if r.trade_date == today), None)
    failed = [r for r in results if r.status == "FAILED"]
    if mine and mine.status == "NO_FILE" and not failed and to_ist(now).time() < FO_FILE_GIVE_UP_IST:
        return "fo_file_waiting"
    if failed or not mine or mine.status != "LOADED":
        why = failed[0].error if failed else "no file by 23:00 IST"
        notifier.send(f"TradeX: NSE F&O file for {today.isoformat()} not stored ({why}). "
                      "Option history has a gap for that day; it is retried tomorrow.")
    state.fo_done_for = today.isoformat()
    return "fo_file"
