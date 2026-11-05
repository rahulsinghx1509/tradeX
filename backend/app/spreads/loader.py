"""Fetch the 1-minute option candles SP-1.0 needs (credit, debit and condor legs).

Reuses the 9/21 EMA tables and helpers: spot and expiries are loaded like `ema-load` (days already
stored are skipped), then only the contracts a spread could trade:
- every ET-1.0 entry signal: the ATM sell leg and the 100 / 150-point hedge legs;
- every ORB-1.0 breakout: the ATM buy leg and the 100 / 150-point short legs (all breakouts, so the
  volume filter can be applied at backtest time);
- every day: the four condor legs from the 09:59 close (the range/VIX filter is applied later).
Read-only: no orders. Safe to stop and re-run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.ema921 import bars as B
from app.ema921 import engine as EE
from app.ema921 import loader as L
from app.ema921 import store as S
from app.ema921 import trend as T
from app.orb import engine as O
from app.spreads import UNDERLYING, WIDTHS
from app.spreads import engine as E
from app.straddle import loader as STL


def _legs_for(day: date, c: B.Cross, modes, expiries, cfg) -> list[tuple[date, date, float, str]]:
    e = EE.pick_expiry(day, expiries, cfg.roll_on_expiry_day)
    if e is None:
        return []
    return [(day, e, k, t) for m in modes for _, t, k in [EE.leg_for(m, c.direction, c.spot, cfg)]]


def needed_legs(spot: pd.DataFrame, start: date, end: date, expiries: list[date],
                cfg: EE.Config) -> list[tuple[date, date, float, str]]:
    out: list[tuple[date, date, float, str]] = []
    et = E.et_config(cfg)
    crosses = B.crosses(B.add_emas(B.resample(spot, et.timeframe)))
    blocked = T.blocked(spot, crosses)
    cs_modes = (E.CS_SHORT, *(E.cs_wing(w) for w in WIDTHS))
    for c in crosses:
        if start <= c.day <= end and EE._entry_allowed(c, et, blocked):
            out += _legs_for(c.day, c, cs_modes, expiries, cfg)
    ds_modes = (E.DS_LONG, *(E.ds_short(w) for w in WIDTHS))
    for d, (_, c) in O.signals(spot).items():
        if start <= d <= end:
            out += _legs_for(d, c, ds_modes, expiries, cfg)
    out += STL.needed_legs(spot, start, end, expiries, E.condor_config())
    return sorted(set(out))


def load_all(session: Session, client: Any, start: date, end: date,
             log: Callable[[str], None] = print) -> L.LoadReport:
    cfg = L.config_for(UNDERLYING)
    rep = L.LoadReport()
    L.load_spot(session, client, start, end, rep, log, UNDERLYING)
    L.load_expiries(session, client, start, end, rep, log, UNDERLYING)
    spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, UNDERLYING)
    legs = needed_legs(spot, start, end, S.expiries(session, UNDERLYING), cfg)
    L.fetch_legs(session, client, legs, rep, UNDERLYING, "Spread legs", log)
    return rep
