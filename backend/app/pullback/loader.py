"""Fetch the option legs TP-1.0 trades. Read-only, resumable.

Which contract a trade needs depends on earlier trades of the day (one trade at a time, a new
pullback after each, the loss limits), so the loader simply runs the backtest, notes every leg it
asked for that is not stored yet, fetches those and runs again, until nothing new is asked for
(at most MAX_PASSES). Needs the futures VWAP series from `fut-load` first.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.ema921 import loader as L
from app.ema921 import store as S
from app.pullback import MODES, UNDERLYING
from app.pullback import engine as E

MAX_PASSES = 6


class Recorder:
    """The database's OptionData, noting each (day, expiry, strike, type) that had no candles."""

    def __init__(self, data: S.DbOptionData):
        self.data = data
        self.asked: set[tuple[date, date, float, str]] = set()
        self._last: tuple | None = None

    def expiries(self):
        return self.data.expiries()

    def contract(self, expiry, strike, option_type):
        self._last = (expiry, float(strike), option_type)
        return self.data.contract(expiry, strike, option_type) or \
            f"MISSING|{expiry}|{strike:g}|{option_type}"

    def minutes(self, symbol, day):
        df = None if symbol.startswith("MISSING|") else self.data.minutes(symbol, day)
        if df is None and self._last is not None:
            e, k, t = self._last
            self.asked.add((day, e, k, t))
        return df


def load_all(session: Session, client: Any, start: date, end: date,
             log: Callable[[str], None] = print) -> L.LoadReport:
    cfg = L.config_for(UNDERLYING)
    rep = L.LoadReport()
    L.load_spot(session, client, start, end, rep, log, UNDERLYING)
    L.load_expiries(session, client, start, end, rep, log, UNDERLYING)
    spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, UNDERLYING)
    fut = E.load_futures(session, start, end)
    if fut.empty:
        log("TP-1.0: no NIFTY futures 1-minute data stored (run fut-load first); legs skipped.")
        return rep
    tried: set = set()
    for p in range(1, MAX_PASSES + 1):
        rec = Recorder(S.DbOptionData(session, UNDERLYING))
        for limits in (False, True):
            E.run(spot, fut, rec, start, end, cfg, MODES, limits)
        new = sorted(rec.asked - tried)
        log(f"TP-1.0 pass {p}: {len(new)} new legs to fetch")
        if not new:
            break
        tried |= set(new)
        L.fetch_legs(session, client, new, rep, UNDERLYING, "Pullback legs", log)
    return rep
