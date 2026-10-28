"""The always-on collector: the process that will run on the small server.

During market hours (09:15-15:30 IST, Mon-Fri) it wakes up every few minutes and:
  0. once a day, refreshes the contract list (expired futures stop being tracked, the next
     month's are added),
  1. stores a quote snapshot for each tracked instrument,
  2. tops up the latest 1-hour candles,
  3. once a day from 15:15 IST, stores the option chains (see services/option_chain.py).
After the close it loads the day's daily candle once (and India VIX's), checks the VIX-jump
volatility warning (VJ-1.0, see services/vix_warning.py) and sends one alert when a jump starts,
then sends a heartbeat message so you know it is alive. From 19:00 IST it loads NSE's free daily
F&O file (services/nse_bhavcopy.py) and participant-wise OI file (services/participant_oi.py),
catching up any missed days. Otherwise it sleeps.

Data problems: a quote or candle load that fails once and works on the next cycle is a blip
(Groww has short hiccups); it is logged and counted in the end-of-day message. Only a problem
that lasts ALERT_AFTER_CYCLES cycles in a row sends a Telegram alert, once, naming the contract
and the error, and a second message when it recovers.

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
from app.db.models import IngestionRun, Instrument
from app.providers.base import MarketDataProvider
from app.services import nse_bhavcopy, option_chain, participant_oi, vix_warning
from app.services.ingestion import ingest_snapshot, refresh_recent, sync_instruments

log = logging.getLogger(__name__)

FO_FILE_FROM_IST = time(19, 0)  # NSE publishes the day's F&O file in the evening
FO_FILE_GIVE_UP_IST = time(23, 0)
FO_CATCH_UP_DAYS = 10
ALERT_AFTER_CYCLES = 2  # a data problem must last this many cycles in a row to alert


@dataclass
class CollectorState:
    """Remembers what already happened today, so end-of-day work runs once."""

    eod_done_for: str | None = None
    chain_done_for: str | None = None
    open_heartbeat_for: str | None = None
    vix_alert_for: str | None = None  # the VIX close date a warning was already sent for
    fo_done_for: str | None = None  # the day NSE's F&O file was loaded (or given up on)
    problem_streak: dict[str, int] = field(default_factory=dict)  # problem -> cycles in a row
    alerted: set[str] = field(default_factory=set)  # problems already sent, until they recover
    blips: int = 0  # one-cycle problems today (counted in the end-of-day message)
    http_client: Callable[[], httpx.Client] = httpx.Client
    failures: list[str] = field(default_factory=list)


def run_cycle(session: Session, provider: MarketDataProvider, notifier, state: CollectorState,
              now: datetime) -> str:
    """One wake-up. Returns what it did (useful for logs and tests)."""
    today = to_ist(now).date().isoformat()
    if is_market_open(now):
        if state.open_heartbeat_for != today:
            settings = get_settings()
            synced = sync_instruments(session, provider, settings.underlying_list,
                                      settings.futures_expiries_to_track)
            note = "" if synced.status == "SUCCESS" else \
                f" Contract list refresh failed ({synced.error}); yesterday's list is used."
            notifier.send(f"TradeX collector is running for {today} ({provider.name} data).{note}")
            state.open_heartbeat_for, state.blips = today, 0
        instruments = session.scalars(
            select(Instrument).where(Instrument.provider == provider.name, Instrument.is_active)
        ).all()
        problems = {}
        for i in instruments:
            r = ingest_snapshot(session, provider, i)
            if r.status != "SUCCESS":
                problems[f"{i.provider_symbol} quote"] = r.error
        for r in refresh_recent(session, provider, "1h", now):
            if r.status != "SUCCESS":
                run = session.get(IngestionRun, r.run_id)
                inst = session.get(Instrument, run.instrument_id) if run else None
                name = inst.provider_symbol if inst else f"run {r.run_id}"
                problems[f"{name} 1h candles"] = r.error
        for text in track_problems(state, problems):
            notifier.send(text)
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
        blips = (f" {state.blips} short data blip{'s' if state.blips != 1 else ''} during the day"
                 " (each fixed itself by the next cycle)." if state.blips else "")
        notifier.send(f"TradeX end of day {today}: {len(results) - bad} loads ok, {bad} failed."
                      f"{vix_note} Volatility warning: {warning.status}.{blips}")
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


def track_problems(state: CollectorState, problems: dict[str, str | None]) -> list[str]:
    """Alert texts for this cycle: problems that just reached ALERT_AFTER_CYCLES in a row, and
    alerted problems that are gone. One-cycle problems are only counted (state.blips)."""
    for key in list(state.problem_streak):
        if key not in problems:
            if state.problem_streak[key] < ALERT_AFTER_CYCLES:
                state.blips += 1
            del state.problem_streak[key]
    for key in problems:
        state.problem_streak[key] = state.problem_streak.get(key, 0) + 1
    out = []
    new = [k for k in problems if state.problem_streak[k] == ALERT_AFTER_CYCLES]
    if new:
        state.failures.extend(new)
        state.alerted.update(new)
        detail = "; ".join(f"{k} ({(problems[k] or 'no detail')[:120]})" for k in new[:5])
        out.append(f"TradeX data problem for {ALERT_AFTER_CYCLES} checks in a row: {detail}. "
                   "Signals would be NO TRADE until data recovers.")
    gone = sorted(k for k in state.alerted if k not in problems)
    if gone:
        state.alerted.difference_update(gone)
        out.append("TradeX data recovered: " + ", ".join(gone[:5]) + ".")
    return out


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
    load_participant_file(session, notifier, state, today)
    state.fo_done_for = today.isoformat()
    return "fo_file"


def load_participant_file(session: Session, notifier, state: CollectorState, today) -> None:
    """Today's participant OI file plus missed days. Runs once, right after the F&O file."""
    latest = participant_oi.latest_day(session)
    start = (max(latest + timedelta(days=1), today - timedelta(days=FO_CATCH_UP_DAYS))
             if latest else today)
    with state.http_client() as client:
        results = participant_oi.load(session, client, start, today)
    bad = [r for r in results if r.status == "FAILED"
           or (r.trade_date == today and r.status != "LOADED")]
    if bad:
        why = bad[0].error or bad[0].status
        notifier.send(f"TradeX: NSE participant OI for {bad[0].trade_date.isoformat()} not stored "
                      f"({why}). It is retried tomorrow.")
